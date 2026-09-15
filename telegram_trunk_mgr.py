"""
Telegram SIP Trunk gateway manager (tg2sip-webrtc).

Handles:
- status detection of the local tg2sip-webrtc gateway and its systemd unit
- idempotent build/install of the gateway (long-running, background job)
- generation of /etc/tg2sip-webrtc/tg2sip.conf from the panel settings
- start/stop/restart via systemctl

The gateway is an optional, heavy dependency: a fork of tg2sip ported to
WebRTC (ntgcalls). It must be built with Clang + libc++, takes a long time
and needs a swap file on small VPS instances.
"""

import os
import json
import shutil
import subprocess
import time
import socket

GATEWAY_DIR = "/opt/tg2sip-webrtc"
BUILD_DIR = os.path.join(GATEWAY_DIR, "build-clang")
BINARY_PATH = os.path.join(BUILD_DIR, "tg2sip-webrtc")
REPO_URL = "https://github.com/vladonv/tg2sip-webrtc.git"
CONF_DIR = "/etc/tg2sip-webrtc"
CONF_PATH = os.path.join(CONF_DIR, "tg2sip.conf")
SERVICE_NAME = "tg2sip-webrtc.service"
BUILD_LOG = "/var/log/tg2sip-build.log"
BUILD_STATE = "/opt/tg2sip-build.state"

SERVICE_UNIT = """[Unit]
Description=Telegram to SIP WebRTC Gateway (tg2sip-webrtc)
After=network.target asterisk.service
Wants=network-online.target

[Service]
Type=simple
ExecStart={binary} {conf}
Restart=on-failure
RestartSec=5
WorkingDirectory={workdir}

[Install]
WantedBy=multi-user.target
"""


def _run(cmd, timeout=10):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        class _R:
            returncode = 1
            stdout = ""
            stderr = str(e)
        return _R()


def is_built():
    return os.path.isfile(BINARY_PATH) and os.access(BINARY_PATH, os.X_OK)


def service_exists():
    return os.path.exists(f"/etc/systemd/system/{SERVICE_NAME}") or \
        os.path.exists(f"/lib/systemd/system/{SERVICE_NAME}")


def service_active():
    if not service_exists():
        return False
    res = _run(["systemctl", "is-active", SERVICE_NAME], timeout=5)
    return res.stdout.strip() == "active"


def port_listening(port):
    """True when something is already bound to the given local UDP port."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.bind(("127.0.0.1", int(port)))
        return False
    except OSError:
        return True
    except Exception:
        return False


def read_build_state():
    if not os.path.exists(BUILD_STATE):
        return {"state": "idle"}
    try:
        with open(BUILD_STATE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"state": "idle"}


def _write_build_state(state, message="", started_at=None):
    payload = {
        "state": state,
        "message": message,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if started_at:
        payload["started_at"] = started_at
    try:
        os.makedirs(os.path.dirname(BUILD_STATE), exist_ok=True)
        with open(BUILD_STATE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return payload


def get_status(cfg=None):
    """Aggregated gateway status for the UI."""
    port = 5062
    if cfg:
        tg = cfg.get("telegram_trunk", {}) or {}
        try:
            port = int(tg.get("port", 5062) or 5062)
        except Exception:
            pass

    state = read_build_state()
    return {
        "installed": is_built(),
        "service_exists": service_exists(),
        "service_active": service_active(),
        "port": port,
        "port_busy": port_listening(port),
        "binary": BINARY_PATH,
        "build_state": state.get("state", "idle"),
        "build_message": state.get("message", ""),
        "build_updated_at": state.get("updated_at", ""),
        "build_started_at": state.get("started_at", ""),
    }


def build_log_tail(lines=60):
    if not os.path.exists(BUILD_LOG):
        return ""
    try:
        res = _run(["tail", "-n", str(int(lines)), BUILD_LOG], timeout=5)
        return res.stdout
    except Exception:
        return ""


def _ensure_swap(min_mb=2048):
    """Creates a swap file when the host has none (tg2sip build is memory hungry)."""
    try:
        with open("/proc/meminfo", "r") as f:
            content = f.read()
        swap_total = 0
        for line in content.splitlines():
            if line.startswith("SwapTotal:"):
                swap_total = int(line.split()[1]) // 1024
                break
        if swap_total >= min_mb or os.path.exists("/swapfile"):
            return
        subprocess.run(["fallocate", "-l", "4G", "/swapfile"], capture_output=True, timeout=120)
        subprocess.run(["chmod", "600", "/swapfile"], capture_output=True, timeout=10)
        subprocess.run(["mkswap", "/swapfile"], capture_output=True, timeout=60)
        subprocess.run(["swapon", "/swapfile"], capture_output=True, timeout=30)
        with open("/etc/fstab", "a") as f:
            f.write("\n/swapfile none swap sw 0 0\n")
    except Exception:
        pass


def start_build():
    """Kicks off the long build in a detached process. Idempotent."""
    current = read_build_state()
    if current.get("state") == "building":
        # Verify the worker is actually alive; otherwise allow a restart.
        pid = current.get("pid")
        if pid:
            try:
                os.kill(int(pid), 0)
                return False, "Сборка уже выполняется."
            except Exception:
                pass
        else:
            return False, "Сборка уже выполняется."

    script = os.path.join(GATEWAY_DIR, "_build_worker.sh")
    os.makedirs(GATEWAY_DIR, exist_ok=True)
    with open(script, "w", encoding="utf-8") as f:
        f.write(build_worker_script())
    os.chmod(script, 0o755)

    started_at = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        proc = subprocess.Popen(
            ["/bin/bash", script],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        return False, f"Не удалось запустить сборку: {e}"

    _write_build_state("building", "Сборка запущена...", started_at=started_at)
    # record pid for liveness checks
    try:
        st = read_build_state()
        st["pid"] = proc.pid
        with open(BUILD_STATE, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return True, "Сборка tg2sip-webrtc запущена в фоне. Это может занять 30+ минут."


def build_worker_script():
    return f"""#!/bin/bash
# Auto-generated tg2sip-webrtc build worker. Do not edit manually.
LOG="{BUILD_LOG}"
STATE="{BUILD_STATE}"
GATEWAY_DIR="{GATEWAY_DIR}"
REPO_URL="{REPO_URL}"
BINARY="{BINARY_PATH}"
SERVICE_NAME="{SERVICE_NAME}"

write_state() {{
    python3 - "$1" "$2" <<'PY'
import json, sys, time, os
state, msg = sys.argv[1], sys.argv[2]
path = os.environ.get("STATE")
data = {{"state": state, "message": msg, "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")}}
try:
    old = json.load(open(path))
    data["started_at"] = old.get("started_at", "")
except Exception:
    pass
try:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
except Exception:
    pass
PY
}}
export STATE="$STATE"

mkdir -p "$(dirname "$LOG")"
exec > >(tee -a "$LOG") 2>&1
echo "===== tg2sip-webrtc build started at $(date) ====="

if [ "$(EUID)" -ne 0 ]; then
    echo "Build must run as root."
    write_state error "Сборка требует прав root."
    exit 1
fi

if [ ! -d "$GATEWAY_DIR/.git" ]; then
    rm -rf "$GATEWAY_DIR"
    git clone --recurse-submodules "$REPO_URL" "$GATEWAY_DIR" || {{ write_state error "git clone failed"; exit 1; }}
else
    git -C "$GATEWAY_DIR" fetch origin && git -C "$GATEWAY_DIR" reset --hard origin/master || true
    git -C "$GATEWAY_DIR" submodule update --init --recursive || true
fi

write_state building "Установка пакетов сборки..."
apt-get update
apt-get install -y clang cmake build-essential git libssl-dev pkg-config ninja-build libjansson-dev libopus-dev python3 python3-pip || \
    {{ write_state error "Ошибка установки пакетов сборки"; exit 1; }}

cd "$GATEWAY_DIR" || {{ write_state error "Нет каталога сборки"; exit 1; }}

write_state building "Сборка зависимостей (PJSIP, TDLib, spdlog)... это долго"
./buildenv/build-clang-libcxx-deps.sh || {{ write_state error "Ошибка сборки зависимостей"; exit 1; }}

write_state building "Конфигурация cmake..."
cmake --preset clang-libcxx || {{ write_state error "Ошибка cmake configure"; exit 1; }}

write_state building "Компиляция tg2sip-webrtc..."
cmake --build build-clang || {{ write_state error "Ошибка компиляции"; exit 1; }}

if [ ! -x "$BINARY" ]; then
    write_state error "Бинарник не найден после сборки."
    exit 1
fi

mkdir -p "/etc/tg2sip-webrtc"
if [ ! -f "/etc/tg2sip-webrtc/tg2sip.conf" ] && [ -f "$GATEWAY_DIR/build-clang/tg2sip.conf.sample" ]; then
    cp "$GATEWAY_DIR/build-clang/tg2sip.conf.sample" "/etc/tg2sip-webrtc/tg2sip.conf"
fi

cat > "/etc/systemd/system/$SERVICE_NAME" <<EOF
[Unit]
Description=Telegram to SIP WebRTC Gateway (tg2sip-webrtc)
After=network.target asterisk.service
Wants=network-online.target

[Service]
Type=simple
ExecStart=$BINARY /etc/tg2sip-webrtc/tg2sip.conf
Restart=on-failure
RestartSec=5
WorkingDirectory=$GATEWAY_DIR

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME" || true

echo "===== build finished at $(date) ====="
write_state built "Шлюз успешно собран и зарегистрирован как systemd-сервис."
"""


def write_config(cfg):
    """Generates /etc/tg2sip-webrtc/tg2sip.conf from panel settings.

    Preserves any existing config as a template so we do not lose comments or
    options we do not manage.
    """
    tg = (cfg or {}).get("telegram_trunk", {}) or {}
    api_id = str(tg.get("api_id", "") or "").strip()
    api_hash = str(tg.get("api_hash", "") or "").strip()
    try:
        port = int(tg.get("port", 5062) or 5062)
    except Exception:
        port = 5062

    os.makedirs(CONF_DIR, exist_ok=True)

    if os.path.exists(CONF_PATH):
        try:
            with open(CONF_PATH, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            content = ""
    else:
        content = ""

    def set_key(text, key, value):
        lines = text.splitlines()
        out = []
        found = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                out.append(line)
                continue
            k = stripped.split("=", 1)[0].strip()
            if k == key:
                out.append(f"{key}={value}")
                found = True
            else:
                out.append(line)
        if not found:
            out.append(f"{key}={value}")
        return "\n".join(out)

    if not content.strip():
        content = (
            "# tg2sip-webrtc configuration (managed by Asterisk GUI)\n"
            "log_level=3\n"
        )

    content = set_key(content, "api_id", api_id)
    content = set_key(content, "api_hash", api_hash)
    content = set_key(content, "callback_uri", f"sip:{port}@127.0.0.1:5060")
    content = set_key(content, "port", port)

    tmp = CONF_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, CONF_PATH)
    return CONF_PATH


def start_service():
    write_config(load_gateway_cfg())
    if not is_built():
        return False, "Шлюз ещё не собран. Сначала выполните установку."
    res = _run(["systemctl", "start", SERVICE_NAME], timeout=20)
    if res.returncode != 0:
        return False, (res.stderr or res.stdout or "Не удалось запустить сервис").strip()
    return True, "Шлюз запущен."


def stop_service():
    res = _run(["systemctl", "stop", SERVICE_NAME], timeout=20)
    if res.returncode != 0:
        return False, (res.stderr or res.stdout or "Не удалось остановить сервис").strip()
    return True, "Шлюз остановлен."


def restart_service():
    write_config(load_gateway_cfg())
    res = _run(["systemctl", "restart", SERVICE_NAME], timeout=20)
    if res.returncode != 0:
        return False, (res.stderr or res.stdout or "Не удалось перезапустить сервис").strip()
    return True, "Шлюз перезапущен."


def load_gateway_cfg():
    """Reads integrations config without importing app (avoids circular import)."""
    cfg_path = "/opt/integrations_config.json"
    if not os.path.exists(cfg_path):
        cfg_path = os.path.join(os.path.dirname(__file__), "integrations_config.json")
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def uninstall():
    """Stops and removes the gateway service and binary directory."""
    _run(["systemctl", "stop", SERVICE_NAME], timeout=20)
    _run(["systemctl", "disable", SERVICE_NAME], timeout=20)
    service_path = f"/etc/systemd/system/{SERVICE_NAME}"
    if os.path.exists(service_path):
        try:
            os.remove(service_path)
        except Exception:
            pass
    _run(["systemctl", "daemon-reload"], timeout=15)
    if os.path.isdir(GATEWAY_DIR):
        shutil.rmtree(GATEWAY_DIR, ignore_errors=True)
    _write_build_state("idle", "Шлюз удалён.")
    return True, "Шлюз Telegram SIP удалён."
