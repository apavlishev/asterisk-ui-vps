"""
Authentication, roles, TOTP 2FA and audit log for the Asterisk GUI.

Storage:
- Users live in the integration config under `users` as a list of dicts:
    {username, password_hash, role, totp_secret, totp_enabled, created_at}
- The legacy single-user `security_auth` block is migrated transparently on
  first load, so existing installations keep working.

Roles:
- admin    : full access (settings, security, marketplace, plugins, users)
- operator : day-to-day telephony (calls, recordings, live control, sip view)
- readonly : view-only (dashboards, call log, analytics)

2FA: RFC-6238 TOTP implemented with the standard library only.
Audit: append-only JSON-lines log at /opt/asterisk-gui/audit.log.
"""

import base64
import datetime
import hashlib
import hmac
import json
import os
import struct
import time

try:
    from werkzeug.security import generate_password_hash, check_password_hash
except Exception:  # pragma: no cover
    generate_password_hash = None
    check_password_hash = None

AUDIT_FILE = "/opt/asterisk-gui/audit.log"

ROLES = {
    "admin": "Администратор",
    "operator": "Оператор",
    "readonly": "Только просмотр",
}

# Which role is allowed to perform a privileged action.
PRIVILEGED_ROLES = {"admin"}


def hash_password(password):
    if generate_password_hash:
        return generate_password_hash(password)
    # Fallback: salted sha256 (only if werkzeug unavailable)
    salt = os.urandom(16).hex()
    digest = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"sha256${salt}${digest}"


def verify_password(password, stored):
    if not stored:
        return False
    if stored.startswith("sha256$") and check_password_hash is None:
        try:
            _, salt, digest = stored.split("$", 2)
            return hmac.compare_digest(hashlib.sha256((salt + password).encode()).hexdigest(), digest)
        except Exception:
            return False
    if generate_password_hash and (stored.startswith("pbkdf2:") or stored.startswith("scrypt:")):
        return check_password_hash(stored, password)
    # Plain-text legacy value
    return hmac.compare_digest(str(stored), str(password))


# ================= TOTP (RFC 6238) =================
def generate_totp_secret(length=20):
    return base64.b32encode(os.urandom(length)).decode("utf-8").rstrip("=")


def _totp_at(secret, timestamp, digits=6, period=30, algo=hashlib.sha1):
    key = base64.b32decode(secret + "=" * ((8 - len(secret) % 8) % 8), casefold=True)
    counter = int(timestamp // period)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, algo).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10 ** digits)).zfill(digits)


def verify_totp(secret, code, window=1):
    if not secret or not code:
        return False
    code = str(code).strip().replace(" ", "")
    now = time.time()
    for w in range(-window, window + 1):
        if hmac.compare_digest(_totp_at(secret, now + w * 30), code):
            return True
    return False


def otpauth_url(secret, username, issuer="AsteriskPBX"):
    from urllib.parse import quote
    return (f"otpauth://totp/{quote(issuer)}:{quote(username)}"
            f"?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30")


# ================= USERS =================
def get_users(cfg):
    users = cfg.get("users")
    if isinstance(users, list) and users:
        return users

    # Migrate legacy single user
    sec = cfg.get("security_auth", {}) or {}
    username = sec.get("username", "admin")
    password = sec.get("password", "admin")
    migrated = [{
        "username": username,
        "password_hash": hash_password(password),
        "role": "admin",
        "totp_secret": "",
        "totp_enabled": False,
        "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "legacy": True,
    }]
    cfg["users"] = migrated
    return migrated


def find_user(cfg, username):
    for u in get_users(cfg):
        if u.get("username") == username:
            return u
    return None


def authenticate(cfg, username, password, totp_code=""):
    """Returns (ok, user_or_error). Verifies password and TOTP when enabled."""
    user = find_user(cfg, username)
    if not user:
        return False, "Пользователь не найден"
    if not verify_password(password, user.get("password_hash", "")):
        return False, "Неверный пароль"
    if user.get("totp_enabled"):
        if not verify_totp(user.get("totp_secret", ""), totp_code):
            return False, "Неверный код 2FA"
    return True, user


def upsert_user(cfg, username, password=None, role="operator", totp_enabled=None, totp_secret=None):
    username = (username or "").strip()
    if not username:
        return False, "Пустое имя пользователя"
    if role not in ROLES:
        role = "operator"
    users = get_users(cfg)
    user = find_user(cfg, username)
    if user is None:
        user = {
            "username": username,
            "role": role,
            "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "totp_secret": "",
            "totp_enabled": False,
        }
        users.append(user)
    user["role"] = role
    if password:
        user["password_hash"] = hash_password(password)
    if totp_secret is not None:
        user["totp_secret"] = totp_secret
    if totp_enabled is not None:
        user["totp_enabled"] = bool(totp_enabled)
    cfg["users"] = users
    return True, "Пользователь сохранён"


def delete_user(cfg, username):
    users = get_users(cfg)
    # Never allow removing the last admin
    admins = [u for u in users if u.get("role") == "admin" and u.get("username") != username]
    target = find_user(cfg, username)
    if target and target.get("role") == "admin" and not admins:
        return False, "Нельзя удалить последнего администратора"
    cfg["users"] = [u for u in users if u.get("username") != username]
    return True, "Пользователь удалён"


# ================= AUDIT =================
def audit(username, action, details=""):
    line = json.dumps({
        "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "user": username or "anonymous",
        "action": action,
        "details": details,
    }, ensure_ascii=False)
    try:
        os.makedirs(os.path.dirname(AUDIT_FILE), exist_ok=True)
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def read_audit(limit=200):
    if not os.path.exists(AUDIT_FILE):
        return []
    try:
        with open(AUDIT_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()[-limit:]
        out = []
        for ln in reversed(lines):
            try:
                out.append(json.loads(ln))
            except Exception:
                pass
        return out
    except Exception:
        return []


# ================= PERMISSIONS =================
# Areas operators/readonly are NOT allowed to change.
ADMIN_ONLY_AREAS = (
    "/settings/", "/api/security/", "/api/vpn/", "/plugins/", "/plugin/",
    "/action/", "/sip/", "/api/telegram/", "/api/oauth/",
)

# Read-only users may not POST anywhere except login/logout/language.
READONLY_ALLOWED_POST = ("/login", "/logout", "/set-language")


def can_execute(role, path, method):
    if role == "admin":
        return True
    if role == "operator":
        # operators may use call control but not change system config
        if path.startswith("/api/calls/"):
            return True
        if method == "GET":
            return True
        return not any(path.startswith(a) for a in ADMIN_ONLY_AREAS)
    if role == "readonly":
        if method == "GET":
            return True
        return any(path.startswith(a) for a in READONLY_ALLOWED_POST)
    return False
