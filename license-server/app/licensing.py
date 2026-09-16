"""
Core license business logic: activation, validation, deactivation.
"""

import datetime
import json
import secrets

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .models import Activation, AuditLog, License
from .signing import public_key_base64, sign_payload


def now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _aware(dt: datetime.datetime | None) -> datetime.datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def generate_license_key() -> str:
    return "LGC-" + secrets.token_hex(12).upper()


def audit(db: Session, action: str, license_key="", fingerprint="", ip="", detail=""):
    db.add(AuditLog(
        action=action, license_key=license_key, fingerprint=fingerprint,
        ip=ip, detail=detail,
    ))
    db.commit()


def build_payload(license_row: License, fingerprint: str, lease_days: int) -> dict:
    issued = now_utc()
    expires = issued + datetime.timedelta(days=lease_days)
    features = {}
    try:
        features = json.loads(license_row.features_json or "{}")
    except Exception:
        pass
    return {
        "license_key": license_row.license_key,
        "tier": license_row.tier,
        "server_fingerprint": fingerprint,
        "max_users": license_row.max_users,
        "features": features,
        "customer": license_row.customer,
        "issued_at": issued.isoformat(),
        "expires_at": expires.isoformat(),
        "hard_expires_at": _aware(license_row.expires_at).isoformat() if license_row.expires_at else None,
    }


def issue_license(db: Session, *, tier="Pro", max_users=50, max_activations=None,
                  customer="", notes="", days=None, features=None) -> License:
    key = generate_license_key()
    while db.scalar(select(License).where(License.license_key == key)):
        key = generate_license_key()
    row = License(
        license_key=key,
        tier=tier,
        max_users=max_users,
        max_activations=max_activations or settings.DEFAULT_MAX_ACTIVATIONS,
        customer=customer,
        notes=notes,
        features_json=json.dumps(features or {}, ensure_ascii=False),
        expires_at=(now_utc() + datetime.timedelta(days=days)) if days else None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def activate(db: Session, license_key: str, fingerprint: str, ip: str, hostname: str):
    """Returns (payload_dict, None) or (None, error_message, status_code)."""
    row = db.scalar(select(License).where(License.license_key == license_key))
    if not row:
        return None, "Лицензионный ключ не найден", 404
    if not row.active:
        return None, "Лицензия деактивирована", 403
    hard_exp = _aware(row.expires_at)
    if hard_exp and hard_exp < now_utc():
        return None, "Срок действия лицензии истёк", 403

    existing = db.scalar(
        select(Activation).where(
            Activation.license_id == row.id,
            Activation.fingerprint == fingerprint,
            Activation.revoked == False,  # noqa: E712
        )
    )
    if existing:
        existing.ip = ip
        existing.hostname = hostname
        existing.last_seen = now_utc()
    else:
        active_count = db.scalar(
            select(func.count()).select_from(Activation).where(
                Activation.license_id == row.id,
                Activation.revoked == False,  # noqa: E712
            )
        ) or 0
        if row.max_activations and active_count >= row.max_activations:
            return None, f"Достигнут лимит активаций ({row.max_activations}) для этой лицензии", 409
        db.add(Activation(
            license_id=row.id, fingerprint=fingerprint, ip=ip,
            hostname=hostname, last_seen=now_utc(),
        ))

    payload = build_payload(row, fingerprint, settings.LEASE_DAYS)
    payload["signature"] = sign_payload(payload)
    payload["public_key"] = public_key_base64()
    db.commit()
    return payload, None, 200


def validate(db: Session, license_key: str, fingerprint: str, ip: str):
    row = db.scalar(select(License).where(License.license_key == license_key))
    if not row or not row.active:
        return None, "Лицензия неактивна", 403
    hard_exp = _aware(row.expires_at)
    if hard_exp and hard_exp < now_utc():
        return None, "Срок действия лицензии истёк", 403
    act = db.scalar(
        select(Activation).where(
            Activation.license_id == row.id,
            Activation.fingerprint == fingerprint,
            Activation.revoked == False,  # noqa: E712
        )
    )
    if not act:
        return None, "Устройство не активировано для этой лицензии", 404
    act.last_seen = now_utc()
    act.ip = ip
    payload = build_payload(row, fingerprint, settings.LEASE_DAYS)
    payload["signature"] = sign_payload(payload)
    payload["public_key"] = public_key_base64()
    db.commit()
    return payload, None, 200


def deactivate(db: Session, license_key: str, fingerprint: str):
    row = db.scalar(select(License).where(License.license_key == license_key))
    if not row:
        return False, "Лицензионный ключ не найден"
    act = db.scalar(
        select(Activation).where(
            Activation.license_id == row.id,
            Activation.fingerprint == fingerprint,
        )
    )
    if not act:
        return False, "Активация не найдена"
    act.revoked = True
    db.commit()
    return True, "Активация отозвана"
