"""
Firewall manager for the Asterisk PBX panel.

Design:
- Uses nftables when available (table inet asterisk_gui), otherwise falls back
  to plain iptables (INPUT chain with an ASTERISK-GUI comment tag).
- Rules are persisted to /etc/asterisk-gui/firewall.json so the panel is the
  source of truth, and re-applied idempotently.
- Default rule set opens only what the PBX needs: SSH, web GUI, SIP/RTP and the
  optional services (OpenVPN, VLESS, tg2sip, TURN).
- Fail2ban's own chains are never touched: we add our rules alongside.

All operations are defensive: if the firewall backend is missing we report a
clear error instead of silently pretending success.
"""

import json
import os
import subprocess
import shutil
import datetime

CONFIG_DIR = "/etc/asterisk-gui"
STATE_FILE = os.path.join(CONFIG_DIR, "firewall.json")
NFT_TABLE = "asterisk_gui"
IPT_CHAIN = "ASTERISK-GUI"

# Well-known ports the PBX needs to operate.
DEFAULT_RULES = [
    {"id": "ssh", "name": "SSH (удалённое управление)", "port": "22", "proto": "tcp", "action": "allow", "source": "any", "builtin": True},
    {"id": "web", "name": "Веб-панель (Asterisk GUI)", "port": "8888", "proto": "tcp", "action": "allow", "source": "any", "builtin": True},
    {"id": "sip", "name": "SIP (регистрация телефонов)", "port": "5060", "proto": "udp", "action": "allow", "source": "any", "builtin": True},
    {"id": "sip_tcp", "name": "SIP over TCP", "port": "5060", "proto": "tcp", "action": "allow", "source": "any", "builtin": False},
    {"id": "sip_tls", "name": "SIP TLS (защищённый)", "port": "5061", "proto": "tcp", "action": "allow", "source": "any", "builtin": False},
    {"id": "rtp", "name": "RTP (голос, медиапоток)", "port": "10000-20000", "proto": "udp", "action": "allow", "source": "any", "builtin": True},
    {"id": "sip_extra", "name": "Доп. SIP порт", "port": "5160", "proto": "udp", "action": "allow", "source": "any", "builtin": False},
]


def _run(cmd, timeout=10):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        class _R:
            returncode = 1
            stdout = ""
            stderr = str(e)
        return _R()


def backend():
    """Returns 'nft' or 'iptables' depending on what is available."""
    if shutil.which("nft"):
        return "nft"
    if shutil.which("iptables"):
        return "iptables"
    return None


def is_available():
    return backend() is not None


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data.setdefault("rules", [])
                data.setdefault("enabled", False)
                data.setdefault("installed_defaults", False)
                return data
        except Exception:
            pass
    return {"enabled": False, "installed_defaults": False, "rules": [], "updated_at": ""}


def save_state(state):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_FILE)
        return True
    except Exception as e:
        print(f"[firewall] save failed: {e}")
        return False


def ensure_defaults(state=None):
    """Adds the standard PBX ports if the rule list is empty (first install)."""
    if state is None:
        state = load_state()
    existing_ids = {r.get("id") for r in state.get("rules", [])}
    changed = False
    for rule in DEFAULT_RULES:
        if rule["id"] not in existing_ids:
            state["rules"].append(dict(rule))
            changed = True
    if changed:
        state["installed_defaults"] = True
        save_state(state)
    return state


def _normalize_port(port):
    """Accepts '5060', '10000-20000' and returns (start, end) or None."""
    port = str(port).strip()
    if not port:
        return None
    if "-" in port:
        a, _, b = port.partition("-")
        if a.strip().isdigit() and b.strip().isdigit():
            return int(a), int(b)
        return None
    if port.isdigit():
        return int(port), int(port)
    return None


def _nft_port_expr(rule):
    proto = rule.get("proto", "tcp")
    rng = _normalize_port(rule.get("port", ""))
    if not rng:
        return None
    start, end = rng
    if start == end:
        return f"{proto} dport {start}"
    return f"{proto} dport {start}-{end}"


def apply_rules(state=None, safe=True):
    """Applies the stored rules to the live firewall. Idempotent.

    When `safe` is True a watchdog is scheduled: if the administrator does not
    confirm connectivity within 90 seconds, the firewall is flushed so nobody
    gets permanently locked out by a bad rule.
    """
    if state is None:
        state = load_state()
    be = backend()
    if not be:
        return False, "Не найден ни nft, ни iptables. Установите: apt-get install -y nftables"

    rules = state.get("rules", [])
    if be == "nft":
        ok, msg = _apply_nft(rules)
    else:
        ok, msg = _apply_iptables(rules)

    if ok and safe:
        _schedule_safety_revert()
        msg += " Авто-откат через 90 секунд, если не подтвердить."
    return ok, msg


WATCHDOG_SCRIPT = "/usr/local/bin/asterisk-gui-firewall-watchdog.sh"
WATCHDOG_UNIT = "asterisk-gui-fw-watchdog.service"


def _schedule_safety_revert(delay=90):
    """Starts a one-shot systemd job that flushes our rules unless cancelled."""
    script = f"""#!/bin/bash
sleep {delay}
# If this marker still exists, nobody confirmed the change -> revert.
if [ -f /run/asterisk-gui-fw-pending ]; then
    nft delete table inet {NFT_TABLE} 2>/dev/null || true
    iptables -D INPUT -j {IPT_CHAIN} 2>/dev/null || true
    iptables -F {IPT_CHAIN} 2>/dev/null || true
    iptables -X {IPT_CHAIN} 2>/dev/null || true
    rm -f /run/asterisk-gui-fw-pending
fi
"""
    try:
        with open(WATCHDOG_SCRIPT, "w", encoding="utf-8") as f:
            f.write(script)
        os.chmod(WATCHDOG_SCRIPT, 0o755)
        with open("/run/asterisk-gui-fw-pending", "w") as f:
            f.write("pending")
        unit = f"""[Unit]
Description=Asterisk GUI firewall safety revert

[Service]
Type=oneshot
ExecStart={WATCHDOG_SCRIPT}
"""
        with open(f"/etc/systemd/system/{WATCHDOG_UNIT}", "w", encoding="utf-8") as f:
            f.write(unit)
        _run(["systemctl", "daemon-reload"])
        _run(["systemctl", "start", WATCHDOG_UNIT])
    except Exception as e:
        print(f"[firewall] watchdog schedule failed: {e}")


def confirm_rules():
    """Cancels the pending safety revert (admin confirmed access still works)."""
    try:
        if os.path.exists("/run/asterisk-gui-fw-pending"):
            os.remove("/run/asterisk-gui-fw-pending")
        return True, "Правила подтверждены. Авто-откат отменён."
    except Exception as e:
        return False, f"Не удалось подтвердить: {e}"


def has_pending_revert():
    return os.path.exists("/run/asterisk-gui-fw-pending")



def _apply_nft(rules):
    # Recreate our dedicated table from scratch so the state always matches config.
    # The input chain is default-DROP: only explicitly allowed ports get through.
    _run(["nft", "delete", "table", "inet", NFT_TABLE])
    _run(["nft", "add", "table", "inet", NFT_TABLE])
    _run(["nft", "add", "chain", "inet", NFT_TABLE, "input",
          "{ type filter hook input priority filter; policy drop; }"])

    # Never lock ourselves out: loopback, established flows and ICMP stay open
    _run(["nft", "add", "rule", "inet", NFT_TABLE, "input", "iif", "lo", "accept"])
    _run(["nft", "add", "rule", "inet", NFT_TABLE, "input", "ct", "state",
          "established,related", "accept"])
    _run(["nft", "add", "rule", "inet", NFT_TABLE, "input", "icmp", "type",
          "{ destination-unreachable, time-exceeded, echo-request }", "accept"])
    _run(["nft", "add", "rule", "inet", NFT_TABLE, "input", "ip", "protocol", "icmp", "accept"])
    # fail2ban inserts its own rules with a lower priority; do not interfere.

    errors = []
    for rule in rules:
        if rule.get("action") != "allow":
            continue
        expr = _nft_port_expr(rule)
        if not expr:
            continue
        src = (rule.get("source") or "any").strip()
        args = ["nft", "add", "rule", "inet", NFT_TABLE, "input"]
        if src and src != "any":
            args += ["ip", "saddr", src]
        args += expr.split() + ["accept"]
        res = _run(args)
        if res.returncode != 0:
            errors.append(f"{rule.get('name')}: {(res.stderr or '').strip()}")

    if errors:
        return False, "Часть правил не применена: " + "; ".join(errors)
    return True, "Правила применены (nftables, политика DROP для остальных портов)."


def _apply_iptables(rules):
    # Build a dedicated chain and make INPUT default-DROP, so only listed ports
    # are reachable. The chain ends with an explicit DROP for everything else.
    _run(["iptables", "-N", IPT_CHAIN])
    # Remove an existing hook (ignore errors), then re-add at the top
    _run(["iptables", "-D", "INPUT", "-j", IPT_CHAIN])
    res = _run(["iptables", "-I", "INPUT", "1", "-j", IPT_CHAIN])
    if res.returncode != 0:
        return False, (res.stderr or "Не удалось подключить цепочку iptables").strip()

    _run(["iptables", "-F", IPT_CHAIN])
    # Safety nets first: loopback, established connections, ICMP
    _run(["iptables", "-A", IPT_CHAIN, "-i", "lo", "-j", "RETURN"])
    _run(["iptables", "-A", IPT_CHAIN, "-m", "conntrack", "--ctstate",
          "ESTABLISHED,RELATED", "-j", "RETURN"])
    _run(["iptables", "-A", IPT_CHAIN, "-p", "icmp", "-j", "RETURN"])

    errors = []
    for rule in rules:
        if rule.get("action") != "allow":
            continue
        rng = _normalize_port(rule.get("port", ""))
        if not rng:
            continue
        proto = rule.get("proto", "tcp")
        start, end = rng
        args = ["iptables", "-A", IPT_CHAIN, "-p", proto, "--dport",
                str(start) if start == end else f"{start}:{end}"]
        src = (rule.get("source") or "any").strip()
        if src and src != "any":
            args += ["-s", src]
        args += ["-j", "RETURN"]
        res = _run(args)
        if res.returncode != 0:
            errors.append(f"{rule.get('name')}: {(res.stderr or '').strip()}")

    # Catch-all: drop everything not explicitly allowed above
    _run(["iptables", "-A", IPT_CHAIN, "-j", "DROP"])

    if errors:
        return False, "Часть правил не применена: " + "; ".join(errors)
    return True, "Правила применены (iptables, политика DROP для остальных портов)."


def set_enabled(enabled):
    state = load_state()
    state["enabled"] = bool(enabled)
    state["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_state(state)
    if enabled:
        # Initial enable: use the safety revert so a wrong rule cannot lock out admins
        return apply_rules(state, safe=True)
    return flush_rules()


def flush_rules():
    """Removes our rules (firewall management disabled)."""
    be = backend()
    if not be:
        return False, "Не найден ни nft, ни iptables."
    if be == "nft":
        _run(["nft", "delete", "table", "inet", NFT_TABLE])
    else:
        _run(["iptables", "-D", "INPUT", "-j", IPT_CHAIN])
        _run(["iptables", "-F", IPT_CHAIN])
        _run(["iptables", "-X", IPT_CHAIN])
    state = load_state()
    state["enabled"] = False
    state["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_state(state)
    return True, "Управление фаерволом отключено, правила сняты."


def add_rule(name, port, proto="tcp", action="allow", source="any"):
    rng = _normalize_port(port)
    if not rng:
        return False, "Некорректный порт. Примеры: 5060 или 10000-20000."
    proto = (proto or "tcp").lower()
    if proto not in ("tcp", "udp"):
        return False, "Протокол должен быть tcp или udp."
    source = (source or "any").strip()
    name = (name or f"Порт {port}").strip()

    state = load_state()
    rid = f"rule_{int(datetime.datetime.now().timestamp() * 1000)}"
    state.setdefault("rules", []).append({
        "id": rid,
        "name": name,
        "port": str(port).strip(),
        "proto": proto,
        "action": action if action in ("allow", "deny") else "allow",
        "source": source or "any",
        "builtin": False,
        "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    save_state(state)
    if state.get("enabled"):
        return apply_rules(state)
    return True, "Правило добавлено (фаервол выключен — применится после включения)."


def update_rule(rule_id, name=None, port=None, proto=None, source=None):
    state = load_state()
    rule = next((r for r in state.get("rules", []) if r.get("id") == rule_id), None)
    if not rule:
        return False, "Правило не найдено."
    if port is not None:
        if not _normalize_port(port):
            return False, "Некорректный порт. Примеры: 5060 или 10000-20000."
        rule["port"] = str(port).strip()
    if proto is not None:
        proto = proto.lower()
        if proto not in ("tcp", "udp"):
            return False, "Протокол должен быть tcp или udp."
        rule["proto"] = proto
    if name is not None and name.strip():
        rule["name"] = name.strip()
    if source is not None:
        rule["source"] = source.strip() or "any"
    save_state(state)
    if state.get("enabled"):
        return apply_rules(state)
    return True, "Правило обновлено."


def delete_rule(rule_id):
    state = load_state()
    rules = state.get("rules", [])
    rule = next((r for r in rules if r.get("id") == rule_id), None)
    if not rule:
        return False, "Правило не найдено."
    if rule.get("builtin"):
        return False, "Базовые правила удалять нельзя (можно только изменить порт/протокол)."
    state["rules"] = [r for r in rules if r.get("id") != rule_id]
    save_state(state)
    if state.get("enabled"):
        return apply_rules(state)
    return True, "Правило удалено."


def status():
    """Returns a status payload for the UI."""
    be = backend()
    state = load_state()
    active = False
    if be == "nft":
        res = _run(["nft", "list", "table", "inet", NFT_TABLE])
        active = res.returncode == 0
    elif be == "iptables":
        res = _run(["iptables", "-L", IPT_CHAIN, "-n"])
        active = res.returncode == 0
    return {
        "available": be is not None,
        "backend": be or "none",
        "enabled": bool(state.get("enabled")),
        "active": active,
        "pending_revert": has_pending_revert(),
        "rules": state.get("rules", []),
        "updated_at": state.get("updated_at", ""),
        "state_file": STATE_FILE,
    }
