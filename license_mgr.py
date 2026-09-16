"""
License client for the Asterisk GUI panel.

Model
-----
- The panel identifies itself with a stable hardware fingerprint.
- The License Server returns a **signed** license (Ed25519) bound to that
  fingerprint. The panel verifies the signature offline using the public key
  embedded below (LICENSE_PUBLIC_KEY) or shipped in the license file itself.
- If the server is unreachable, a previously stored license keeps working
  until its `expires_at` (offline grace). Without a valid license the panel
  falls back to the Free Core tier.

Environment
-----------
LICENSE_SERVER_URL   e.g. https://license.example.com
LICENSE_KEY          license key issued by the server (stored on activation)
LICENSE_PUBLIC_KEY   overrides the embedded Ed25519 public key (base64)
"""

import base64
import datetime
import hashlib
import json
import os
import time

LICENSE_FILE = '/opt/license.json'

# Ed25519 public key (base64) used to verify signed licenses.
# Replace with the key printed by the license server (`manage.py public-key`).
LICENSE_PUBLIC_KEY = os.environ.get('LICENSE_PUBLIC_KEY', '')

LICENSE_SERVER_URL = os.environ.get('LICENSE_SERVER_URL', '')

try:
    import requests
except Exception:  # pragma: no cover
    requests = None


def get_server_fingerprint():
    """Generates immutable hardware/server fingerprint."""
    components = []

    # 1. CPU Serial for Raspberry Pi
    try:
        if os.path.exists('/proc/cpuinfo'):
            with open('/proc/cpuinfo', 'r') as f:
                for line in f:
                    if line.startswith('Serial'):
                        val = line.split(':')[1].strip()
                        if val and val != '0000000000000000':
                            components.append(f"CPU:{val}")
                            break
    except Exception:
        pass

    # 2. DMI System UUID for VPS / Physical Servers
    try:
        for p in ['/sys/class/dmi/id/product_uuid', '/etc/machine-id']:
            if os.path.exists(p):
                with open(p, 'r') as f:
                    val = f.read().strip()
                    if val and val != "None":
                        components.append(f"DMI:{val}")
                        break
    except Exception:
        pass

    # 3. MAC address of primary network interface
    try:
        for iface in ['eth0', 'ens3', 'enp0s3', 'wlan0', 'en0']:
            mac_path = f"/sys/class/net/{iface}/address"
            if os.path.exists(mac_path):
                with open(mac_path, 'r') as f:
                    mac = f.read().strip()
                    if mac:
                        components.append(f"MAC:{mac}")
                        break
    except Exception:
        pass

    raw_id = ":".join(components) if components else "DEFAULT_SERVER_ID"
    h = hashlib.sha256(raw_id.encode()).hexdigest().upper()
    return f"LGC-{h[0:4]}-{h[4:8]}-{h[8:12]}-{h[12:16]}"


# ================= SIGNATURE VERIFICATION =================
def _canonical(payload):
    return json.dumps(payload, separators=(",", ":"), sort_keys=True,
                      ensure_ascii=False).encode("utf-8")


def verify_license_signature(license_data, public_key_b64=None):
    """Verifies an Ed25519 signature over the license payload (minus meta)."""
    sig = license_data.get("signature")
    if not sig:
        return False
    key_b64 = public_key_b64 or license_data.get("public_key") or LICENSE_PUBLIC_KEY
    if not key_b64:
        return False
    payload = {k: v for k, v in license_data.items()
               if k not in ("signature", "public_key")}
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(key_b64))
        pub.verify(base64.b64decode(sig), _canonical(payload))
        return True
    except Exception:
        return False


def _is_expired(license_data):
    exp = license_data.get("expires_at")
    if not exp:
        return False
    try:
        dt = datetime.datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt < datetime.datetime.now(datetime.timezone.utc)
    except Exception:
        return False


def free_core(fp):
    return {
        "tier": "Core Free (Community)",
        "server_id": fp,
        "status": "Active (Free Core)",
        "max_users": 2,
        "active_plugins": [],
        "expires_at": None,
        "is_free_core": True,
    }


def load_license():
    """Loads the stored license, verifying signature, binding and expiry."""
    fp = get_server_fingerprint()
    if os.path.exists(LICENSE_FILE):
        try:
            with open(LICENSE_FILE, 'r') as f:
                data = json.load(f)
            bound = data.get("server_fingerprint") or data.get("server_id")
            ok_bind = (bound == fp) or (bound == 'UNLIMITED')
            ok_sig = verify_license_signature(data)
            # Accept legacy unsigned licenses only if bound (backwards compat).
            if ok_bind and (ok_sig or not data.get("signature")):
                if _is_expired(data):
                    data["status"] = "Expired"
                    return data
                data.setdefault("status", "Active")
                data.setdefault("is_free_core", False)
                return data
        except Exception:
            pass
    return free_core(fp)


# ================= REMOTE ACTIVATION / VALIDATION =================
def activate(license_key=None, server_url=None, timeout=8):
    """Activates this server against the license server. Returns (ok, data)."""
    if requests is None:
        return False, {"error": "Библиотека requests недоступна"}
    server_url = (server_url or LICENSE_SERVER_URL or '').rstrip('/')
    license_key = license_key or os.environ.get('LICENSE_KEY', '')
    if not server_url or not license_key:
        return False, {"error": "Не заданы LICENSE_SERVER_URL или LICENSE_KEY"}
    fp = get_server_fingerprint()
    try:
        r = requests.post(f"{server_url}/api/v1/activate", json={
            "license_key": license_key,
            "fingerprint": fp,
            "hostname": os.uname().nodename if hasattr(os, "uname") else "",
        }, timeout=timeout)
        data = r.json() if r.content else {}
        if r.status_code == 200 and data.get("license"):
            lic = data["license"]
            if not verify_license_signature(lic, LICENSE_PUBLIC_KEY or lic.get("public_key")):
                return False, {"error": "Подпись лицензии недействительна"}
            _store(license_key, lic)
            return True, lic
        return False, {"error": data.get("detail") or f"HTTP {r.status_code}"}
    except Exception as e:
        return False, {"error": str(e)}


def revalidate(server_url=None, timeout=8):
    """Re-validates the stored license (extends the lease)."""
    lic = load_license()
    if lic.get("is_free_core") or not lic.get("license_key"):
        return False, {"error": "Нет активной лицензии"}
    if requests is None:
        return False, {"error": "Библиотека requests недоступна"}
    server_url = (server_url or LICENSE_SERVER_URL or '').rstrip('/')
    if not server_url:
        return False, {"error": "Не задан LICENSE_SERVER_URL"}
    try:
        r = requests.post(f"{server_url}/api/v1/validate", json={
            "license_key": lic["license_key"],
            "fingerprint": get_server_fingerprint(),
        }, timeout=timeout)
        data = r.json() if r.content else {}
        if r.status_code == 200 and data.get("license"):
            new = data["license"]
            if verify_license_signature(new, LICENSE_PUBLIC_KEY or new.get("public_key")):
                _store(lic["license_key"], new)
                return True, new
        return False, {"error": data.get("detail") or f"HTTP {r.status_code}"}
    except Exception as e:
        return False, {"error": str(e)}


def _store(license_key, lic):
    lic = dict(lic)
    lic["license_key"] = license_key
    lic.setdefault("server_fingerprint", get_server_fingerprint())
    try:
        os.makedirs(os.path.dirname(LICENSE_FILE), exist_ok=True)
        with open(LICENSE_FILE, 'w') as f:
            json.dump(lic, f, ensure_ascii=False, indent=2)
        os.chmod(LICENSE_FILE, 0o600)
    except Exception:
        pass


def maybe_revalidate_daily(state_file='/opt/.license_last_check'):
    """Re-validates at most once per 24h. Safe to call on a schedule."""
    try:
        last = 0
        if os.path.exists(state_file):
            last = float(open(state_file).read().strip() or 0)
        if time.time() - last < 86400:
            return False, {"skipped": True}
        ok, data = revalidate()
        if ok:
            with open(state_file, 'w') as f:
                f.write(str(time.time()))
        return ok, data
    except Exception as e:
        return False, {"error": str(e)}


def get_max_allowed_users():
    lic = load_license()
    return lic.get('max_users', 2)


def is_plugin_active(plugin_id):
    lic = load_license()
    if lic.get('tier') in ['Enterprise', 'Ultimate', 'Pro']:
        return True
    return plugin_id in lic.get('active_plugins', [])


if __name__ == '__main__':
    print("Server Fingerprint:", get_server_fingerprint())
    print("Current License:", load_license())
