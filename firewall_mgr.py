"""
Cross-platform firewall manager for the Asterisk PBX panel.

Detection order (respects an already-managed firewall):
    1. ufw        (Debian/Ubuntu, most common on VPS)
    2. firewalld  (RHEL/CentOS/Fedora)
    3. nftables   (raw nft)
    4. iptables   (legacy)
    5. none       -> ufw is installed automatically

The panel stores its own rule set in /etc/asterisk-gui/firewall.json and applies
it through the detected backend. It also reads the *live* state of that backend
so the UI can show the ports that are actually open on the server, including
rules the administrator created outside the panel.

All operations are defensive: missing tools produce a clear error instead of a
silent "success".
"""

import datetime
import json
import os
import re
import shutil
import subprocess

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
    {"id": "webrtc_wss", "name": "WebRTC софтфон (SIP over WSS, HTTPS)", "port": "1443", "proto": "tcp", "action": "allow", "source": "any", "builtin": False},
    {"id": "webrtc_wss_legacy", "name": "WebRTC софтфон (устаревший порт WSS)", "port": "8089", "proto": "tcp", "action": "allow", "source": "any", "builtin": False},
    {"id": "dhcp_provision", "name": "Автопровиженинг телефонов (HTTP)", "port": "8080", "proto": "tcp", "action": "allow", "source": "any", "builtin": False},
]

BACKEND_LABELS = {
    "ufw": "UFW",
    "firewalld": "firewalld",
    "nft": "nftables",
    "iptables": "iptables",
    "none": "—",
}

# Versioned default-rule migrations: (rule_id, old_default_port, new_default_port).
# Applied only when the stored port still equals the old default.
DEFAULT_RULE_MIGRATIONS = [
    ("webrtc_wss", "8089", "1443"),
    ("webrtc_wss", "443", "1443"),
]


def _run(cmd, timeout=15, input_text=None):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              input=input_text)
    except Exception as e:
        class _R:
            returncode = 1
            stdout = ""
            stderr = str(e)
        return _R()


def _has(cmd):
    return shutil.which(cmd) is not None


# ================= BACKEND DETECTION =================
def detect_backend():
    """Returns the firewall backend to use, respecting existing tools."""
    if _has("ufw"):
        return "ufw"
    if _has("firewall-cmd"):
        return "firewalld"
    if _has("nft"):
        return "nft"
    if _has("iptables"):
        return "iptables"
    return None


def install_ufw():
    if _has("ufw"):
        return True
    _run(["apt-get", "update"], timeout=180)
    res = _run(["apt-get", "install", "-y", "ufw"], timeout=300)
    return _has("ufw") and res.returncode == 0


def ensure_backend():
    """Returns (backend, message). Installs ufw when no firewall tool exists."""
    be = detect_backend()
    if be:
        return be, f"Используется {BACKEND_LABELS.get(be, be)}."
    if install_ufw():
        return "ufw", "UFW установлен и выбран как фаервол."
    return None, "Не удалось найти или установить фаервол (ufw)."


def is_available():
    return detect_backend() is not None or _has("apt-get")


def backend():
    """Kept for backward compatibility."""
    return detect_backend()


# ================= STATE =================
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data.setdefault("rules", [])
                data.setdefault("enabled", False)
                data.setdefault("installed_defaults", False)
                data.setdefault("applied", [])
                return data
        except Exception:
            pass
    return {"enabled": False, "installed_defaults": False, "rules": [], "applied": []}


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
    """Adds missing default rules and applies known migrations.

    Migrations update a default rule's port/proto only when its current value
    still matches the *old* default (i.e. the admin hasn't customised it), so
    user-edited rules are never silently overwritten.
    """
    if state is None:
        state = load_state()
    rules = state.setdefault("rules", [])
    existing = {r.get("id"): r for r in rules}
    changed = False
    for rule in DEFAULT_RULES:
        if rule["id"] not in existing:
            rules.append(dict(rule))
            changed = True
    # Versioned migrations: old default port -> new default port
    for rule_id, old_port, new_port in DEFAULT_RULE_MIGRATIONS:
        r = existing.get(rule_id)
        if r and str(r.get("port")) == str(old_port):
            r["port"] = str(new_port)
            changed = True
    if changed:
        state["installed_defaults"] = True
        save_state(state)
    return state


# ================= HELPERS =================
def _normalize_port(port):
    """Accepts '5060', '10000-20000' and returns (start, end) or None."""
    port = str(port).strip()
    if not port:
        return None
    if "-" in port or ":" in port:
        parts = re.split(r"[-:]", port, maxsplit=1)
        if len(parts) == 2 and parts[0].strip().isdigit() and parts[1].strip().isdigit():
            return int(parts[0]), int(parts[1])
        return None
    if port.isdigit():
        return int(port), int(port)
    return None


def _port_spec(rule, sep="-"):
    rng = _normalize_port(rule.get("port", ""))
    if not rng:
        return None
    start, end = rng
    return str(start) if start == end else f"{start}{sep}{end}"


def _validate(name, port, proto, action="allow", source="any"):
    if not _normalize_port(port):
        return None, "Некорректный порт. Примеры: 5060 или 10000-20000."
    proto = (proto or "tcp").lower()
    if proto not in ("tcp", "udp"):
        return None, "Протокол должен быть tcp или udp."
    if action not in ("allow", "deny"):
        action = "allow"
    return {
        "name": (name or f"Порт {port}").strip(),
        "port": str(port).strip(),
        "proto": proto,
        "action": action,
        "source": (source or "any").strip() or "any",
    }, None


# ================= UFW =================
def _ufw_rule_args(rule):
    """Builds the ufw rule spec (without the leading 'allow')."""
    src = (rule.get("source") or "any").strip()
    proto = rule.get("proto", "tcp")
    rng = _normalize_port(rule.get("port", ""))
    if not rng:
        return None
    start, end = rng
    portstr = str(start) if start == end else f"{start}:{end}"
    if src and src != "any":
        return ["from", src, "to", "any", "port", portstr, "proto", proto]
    return [f"{portstr}/{proto}"]


def _ufw_spec_from_stored(spec):
    """Normalizes a stored `applied` entry to a list of ufw rule args.

    Older versions stored UFW specs as plain strings in an nft-like form
    (e.g. "tcp dport 22 any"), while the current format is a list such as
    ["22/tcp"] or ["from","10.0.0.1","to","any","port","5060","proto","tcp"].
    This keeps `apply_rules`/`flush_rules` from crashing on legacy state.
    """
    if isinstance(spec, (list, tuple)):
        return list(spec)
    s = str(spec or "").strip()
    if not s:
        return None
    # Legacy "proto dport PORT [source]" form
    m = re.match(r"^(tcp|udp)\s+dport\s+(\S+)(?:\s+(.*))?$", s)
    if m:
        proto, port, src = m.group(1), m.group(2), (m.group(3) or "any").strip()
        # Stored separators may be '-' but ufw expects ':'
        port = port.replace("-", ":")
        if src and src != "any":
            return ["from", src, "to", "any", "port", port, "proto", proto]
        return [f"{port}/{proto}"]
    # Already a plain "PORT/proto" string
    if re.match(r"^[\d:-]+/(tcp|udp)$", s):
        return [s]
    return None


def _apply_ufw(rules, state):
    _run(["ufw", "--force", "enable"], timeout=30)
    new_specs = []
    for r in rules:
        if r.get("action") != "allow":
            continue
        args = _ufw_rule_args(r)
        if args:
            new_specs.append(args)

    old_specs = state.get("applied", []) or []
    errors = []
    # Remove stale rules we added previously (tolerate legacy string specs)
    for raw in old_specs:
        spec = _ufw_spec_from_stored(raw)
        if not spec:
            continue
        if spec not in new_specs:
            res = _run(["ufw", "--force", "delete", "allow"] + spec, timeout=20)
            if res.returncode != 0 and "Could not delete" not in (res.stdout + res.stderr):
                errors.append(f"delete {' '.join(spec)}: {(res.stderr or res.stdout).strip()}")
    # Add new rules
    for spec in new_specs:
        if spec not in old_specs:
            res = _run(["ufw", "allow"] + spec, timeout=20)
            if res.returncode != 0:
                errors.append(f"allow {' '.join(spec)}: {(res.stderr or res.stdout).strip()}")

    state["applied"] = new_specs
    if errors:
        return False, "Часть правил UFW не применена: " + "; ".join(errors)
    return True, "Правила применены (UFW)."


def _ufw_open_ports():
    """Reads the live UFW status and returns the open ports."""
    res = _run(["ufw", "status"], timeout=15)
    ports = []
    for line in res.stdout.splitlines():
        line = line.strip()
        m = re.match(r"^(\d+)(?::(\d+))?(?:/(tcp|udp))?\s+ALLOW\s+(.*)$", line, re.IGNORECASE)
        if not m:
            continue
        start, end, proto, src = m.groups()
        port = start if not end else f"{start}-{end}"
        ports.append({
            "port": port,
            "proto": proto or "any",
            "source": src.strip() or "Anywhere",
            "action": "allow",
        })
    return ports


def _ufw_active():
    res = _run(["ufw", "status"], timeout=10)
    return "Status: active" in res.stdout


# ================= firewalld =================
def _fw_rule_spec(rule):
    src = (rule.get("source") or "any").strip()
    proto = rule.get("proto", "tcp")
    port = _port_spec(rule, sep="-")
    if not port:
        return None
    if src and src != "any":
        return f"rule family=ipv4 source address={src} port port={port.replace('-', '-')} protocol={proto} accept"
    return f"{port}/{proto}"


def _apply_firewalld(rules, state):
    _run(["firewall-cmd", "--set-default-zone=public"], timeout=15)
    new_specs = []
    for r in rules:
        if r.get("action") != "allow":
            continue
        spec = _fw_rule_spec(r)
        if spec:
            new_specs.append(spec)

    old_specs = state.get("applied", []) or []
    errors = []
    for spec in old_specs:
        if spec not in new_specs:
            if spec.startswith("rule "):
                _run(["firewall-cmd", "--permanent", f"--remove-rich-rule={spec}"], timeout=15)
            else:
                _run(["firewall-cmd", "--permanent", f"--remove-port={spec}"], timeout=15)
    for spec in new_specs:
        if spec not in old_specs:
            if spec.startswith("rule "):
                res = _run(["firewall-cmd", "--permanent", f"--add-rich-rule={spec}"], timeout=15)
            else:
                res = _run(["firewall-cmd", "--permanent", f"--add-port={spec}"], timeout=15)
            if res.returncode != 0:
                errors.append(f"{spec}: {(res.stderr or res.stdout).strip()}")

    _run(["firewall-cmd", "--reload"], timeout=15)
    _run(["systemctl", "enable", "--now", "firewalld"], timeout=20)
    state["applied"] = new_specs
    if errors:
        return False, "Часть правил firewalld не применена: " + "; ".join(errors)
    return True, "Правила применены (firewalld)."


def _firewalld_open_ports():
    res = _run(["firewall-cmd", "--list-all"], timeout=15)
    ports = []
    m = re.search(r"ports:\s*(.*)", res.stdout)
    if m and m.group(1).strip():
        for item in m.group(1).split():
            if "/" in item:
                port, proto = item.rsplit("/", 1)
                ports.append({"port": port, "proto": proto, "source": "Anywhere", "action": "allow"})
    return ports


def _firewalld_active():
    res = _run(["firewall-cmd", "--state"], timeout=10)
    return res.stdout.strip() == "running"


# ================= nftables =================
def _nft_port_expr(rule):
    proto = rule.get("proto", "tcp")
    port = _port_spec(rule, sep="-")
    if not port:
        return None
    return f"{proto} dport {port}"


def _apply_nft(rules, state):
    _run(["nft", "delete", "table", "inet", NFT_TABLE])
    _run(["nft", "add", "table", "inet", NFT_TABLE])
    _run(["nft", "add", "chain", "inet", NFT_TABLE, "input",
          "{ type filter hook input priority filter; policy drop; }"])
    _run(["nft", "add", "rule", "inet", NFT_TABLE, "input", "iif", "lo", "accept"])
    _run(["nft", "add", "rule", "inet", NFT_TABLE, "input", "ct", "state",
          "established,related", "accept"])
    _run(["nft", "add", "rule", "inet", NFT_TABLE, "input", "ip", "protocol", "icmp", "accept"])

    errors = []
    applied = []
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
        else:
            applied.append(f"{expr} {src}")

    state["applied"] = applied
    if errors:
        return False, "Часть правил не применена: " + "; ".join(errors)
    return True, "Правила применены (nftables, политика DROP для остальных портов)."


def _nft_open_ports():
    res = _run(["nft", "list", "table", "inet", NFT_TABLE], timeout=15)
    ports = []
    for line in res.stdout.splitlines():
        m = re.search(r"(tcp|udp) dport (\d+)(?:-(\d+))? accept", line)
        if m:
            proto, a, b = m.groups()
            ports.append({"port": a if not b else f"{a}-{b}", "proto": proto,
                          "source": "Anywhere", "action": "allow"})
    return ports


# ================= iptables =================
def _apply_iptables(rules, state):
    _run(["iptables", "-N", IPT_CHAIN])
    _run(["iptables", "-D", "INPUT", "-j", IPT_CHAIN])
    res = _run(["iptables", "-I", "INPUT", "1", "-j", IPT_CHAIN])
    if res.returncode != 0:
        return False, (res.stderr or "Не удалось подключить цепочку iptables").strip()

    _run(["iptables", "-F", IPT_CHAIN])
    _run(["iptables", "-A", IPT_CHAIN, "-i", "lo", "-j", "RETURN"])
    _run(["iptables", "-A", IPT_CHAIN, "-m", "conntrack", "--ctstate",
          "ESTABLISHED,RELATED", "-j", "RETURN"])
    _run(["iptables", "-A", IPT_CHAIN, "-p", "icmp", "-j", "RETURN"])

    errors = []
    applied = []
    for rule in rules:
        if rule.get("action") != "allow":
            continue
        rng = _normalize_port(rule.get("port", ""))
        if not rng:
            continue
        proto = rule.get("proto", "tcp")
        start, end = rng
        portarg = str(start) if start == end else f"{start}:{end}"
        args = ["iptables", "-A", IPT_CHAIN, "-p", proto, "--dport", portarg]
        src = (rule.get("source") or "any").strip()
        if src and src != "any":
            args += ["-s", src]
        args += ["-j", "RETURN"]
        res = _run(args)
        if res.returncode != 0:
            errors.append(f"{rule.get('name')}: {(res.stderr or '').strip()}")
        else:
            applied.append(f"{proto} {portarg} {src}")

    _run(["iptables", "-A", IPT_CHAIN, "-j", "DROP"])
    state["applied"] = applied
    if errors:
        return False, "Часть правил не применена: " + "; ".join(errors)
    return True, "Правила применены (iptables, политика DROP для остальных портов)."


def _iptables_open_ports():
    res = _run(["iptables", "-L", IPT_CHAIN, "-n", "--line-numbers"], timeout=15)
    ports = []
    for line in res.stdout.splitlines():
        m = re.search(r"(tcp|udp)\s+dpt:(\d+(?::\d+)?)", line)
        if m:
            proto, dpt = m.groups()
            ports.append({"port": dpt.replace(":", "-"), "proto": proto,
                          "source": "Anywhere", "action": "allow"})
    return ports


# ================= DISPATCH =================
def list_open_ports():
    """Returns the ports currently open, according to the active backend."""
    be = detect_backend()
    try:
        if be == "ufw":
            return _ufw_open_ports()
        if be == "firewalld":
            return _firewalld_open_ports()
        if be == "nft":
            return _nft_open_ports()
        if be == "iptables":
            return _iptables_open_ports()
    except Exception as e:
        print(f"[firewall] list_open_ports error: {e}")
    return []


def _backend_active(be=None):
    be = be or detect_backend()
    if be == "ufw":
        return _ufw_active()
    if be == "firewalld":
        return _firewalld_active()
    if be == "nft":
        res = _run(["nft", "list", "table", "inet", NFT_TABLE], timeout=10)
        return res.returncode == 0
    if be == "iptables":
        res = _run(["iptables", "-L", IPT_CHAIN, "-n"], timeout=10)
        return res.returncode == 0
    return False


# ================= BOOT-TIME APPLY =================
BOOT_APPLY_SCRIPT = "/usr/local/bin/asterisk-gui-firewall-apply.sh"
BOOT_APPLY_UNIT = "asterisk-gui-firewall.service"


def install_boot_apply_unit():
    """Installs a systemd unit that re-applies the stored firewall rules on boot.

    UFW/firewalld persist their own rules, but the panel's rule *set* lives in
    firewall.json. If an admin (or an update) resets the backend, this unit
    guarantees the panel's intended rules come back after a reboot.
    """
    try:
        script = f"""#!/bin/bash
# Re-apply Asterisk GUI firewall rules after boot.
sleep 5
python3 - <<'PYEOF'
import sys
sys.path.insert(0, "/opt/asterisk-gui")
try:
    import firewall_mgr
    state = firewall_mgr.load_state()
    if state.get("enabled"):
        firewall_mgr.apply_rules(state, safe=False)
        print("[firewall] boot rules applied")
    else:
        print("[firewall] disabled, skipping")
except Exception as e:
    print("[firewall] boot apply failed:", e)
PYEOF
"""
        with open(BOOT_APPLY_SCRIPT, "w", encoding="utf-8") as f:
            f.write(script)
        os.chmod(BOOT_APPLY_SCRIPT, 0o755)
        unit = f"""[Unit]
Description=Asterisk GUI firewall rules (apply at boot)
After=network-online.target ufw.service firewalld.service
Wants=network-online.target
Before=asterisk-gui.service

[Service]
Type=oneshot
ExecStart={BOOT_APPLY_SCRIPT}
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"""
        with open(f"/etc/systemd/system/{BOOT_APPLY_UNIT}", "w", encoding="utf-8") as f:
            f.write(unit)
        _run(["systemctl", "daemon-reload"])
        _run(["systemctl", "enable", BOOT_APPLY_UNIT])
        return True
    except Exception as e:
        print(f"[firewall] boot apply unit install failed: {e}")
        return False


# ================= WATCHDOG =================
WATCHDOG_SCRIPT = "/usr/local/bin/asterisk-gui-firewall-watchdog.sh"
WATCHDOG_UNIT = "asterisk-gui-fw-watchdog.service"
PENDING_MARKER = "/run/asterisk-gui-fw-pending"


def _schedule_safety_revert(delay=90):
    script = f"""#!/bin/bash
sleep {delay}
if [ -f {PENDING_MARKER} ]; then
    ufw --force disable 2>/dev/null || true
    nft delete table inet {NFT_TABLE} 2>/dev/null || true
    iptables -D INPUT -j {IPT_CHAIN} 2>/dev/null || true
    iptables -F {IPT_CHAIN} 2>/dev/null || true
    iptables -X {IPT_CHAIN} 2>/dev/null || true
    rm -f {PENDING_MARKER}
fi
"""
    try:
        with open(WATCHDOG_SCRIPT, "w", encoding="utf-8") as f:
            f.write(script)
        os.chmod(WATCHDOG_SCRIPT, 0o755)
        with open(PENDING_MARKER, "w") as f:
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
    try:
        if os.path.exists(PENDING_MARKER):
            os.remove(PENDING_MARKER)
        return True, "Правила подтверждены. Авто-откат отменён."
    except Exception as e:
        return False, f"Не удалось подтвердить: {e}"


def has_pending_revert():
    return os.path.exists(PENDING_MARKER)


# ================= APPLY / FLUSH =================
def apply_rules(state=None, safe=True):
    """Applies the stored rules through the detected backend. Idempotent."""
    if state is None:
        state = load_state()

    be, msg = ensure_backend()
    if not be:
        return False, msg

    if be == "ufw":
        ok, res = _apply_ufw(state.get("rules", []), state)
    elif be == "firewalld":
        ok, res = _apply_firewalld(state.get("rules", []), state)
    elif be == "nft":
        ok, res = _apply_nft(state.get("rules", []), state)
    else:
        ok, res = _apply_iptables(state.get("rules", []), state)

    save_state(state)
    if ok:
        # Ensure the panel's rules are re-applied after a reboot.
        install_boot_apply_unit()
    if ok and safe:
        _schedule_safety_revert()
        res += " Авто-откат через 90 секунд, если не подтвердить."
    return ok, res


def flush_rules():
    """Disables firewall management / removes our rules."""
    be = detect_backend()
    state = load_state()
    if be == "ufw":
        for raw in state.get("applied", []) or []:
            spec = _ufw_spec_from_stored(raw)
            if spec:
                _run(["ufw", "--force", "delete", "allow"] + spec, timeout=20)
        _run(["ufw", "--force", "disable"], timeout=20)
    elif be == "firewalld":
        for spec in state.get("applied", []) or []:
            if spec.startswith("rule "):
                _run(["firewall-cmd", "--permanent", f"--remove-rich-rule={spec}"], timeout=15)
            else:
                _run(["firewall-cmd", "--permanent", f"--remove-port={spec}"], timeout=15)
        _run(["firewall-cmd", "--reload"], timeout=15)
    elif be == "nft":
        _run(["nft", "delete", "table", "inet", NFT_TABLE])
    elif be == "iptables":
        _run(["iptables", "-D", "INPUT", "-j", IPT_CHAIN])
        _run(["iptables", "-F", IPT_CHAIN])
        _run(["iptables", "-X", IPT_CHAIN])
    state["enabled"] = False
    state["applied"] = []
    state["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_state(state)
    return True, "Управление фаерволом отключено, правила сняты."


def set_enabled(enabled):
    state = load_state()
    state["enabled"] = bool(enabled)
    state["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_state(state)
    if enabled:
        return apply_rules(state, safe=True)
    return flush_rules()


# ================= CRUD =================
def add_rule(name, port, proto="tcp", action="allow", source="any"):
    rule, err = _validate(name, port, proto, action, source)
    if err:
        return False, err
    rule["id"] = f"rule_{int(datetime.datetime.now().timestamp() * 1000)}"
    rule["builtin"] = False
    rule["created_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    state = load_state()
    state.setdefault("rules", []).append(rule)
    save_state(state)
    if state.get("enabled"):
        return apply_rules(state, safe=True)
    return True, "Правило добавлено (фаервол выключен — нажмите «Применить правила»)."


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
        return apply_rules(state, safe=True)
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
        return apply_rules(state, safe=True)
    return True, "Правило удалено."


# ================= STATUS =================
def status():
    """Returns a status payload for the UI."""
    be = detect_backend()
    state = load_state()
    return {
        "available": be is not None,
        "backend": be or "none",
        "backend_label": BACKEND_LABELS.get(be or "none", be or "—"),
        "enabled": bool(state.get("enabled")),
        "active": _backend_active(be),
        "pending_revert": has_pending_revert(),
        "rules": state.get("rules", []),
        "open_ports": list_open_ports(),
        "updated_at": state.get("updated_at", ""),
        "state_file": STATE_FILE,
    }
