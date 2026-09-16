"""
Ed25519 signing / verification helpers for license payloads.

The server holds the private key and signs the canonical JSON of the license.
The panel (client) ships with the matching public key and verifies offline.
"""

import base64
import json
import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .config import settings


def _ensure_key_dir():
    d = os.path.dirname(os.path.abspath(settings.SIGNING_KEY_PATH))
    os.makedirs(d, exist_ok=True)


def load_or_create_private_key() -> Ed25519PrivateKey:
    _ensure_key_dir()
    path = settings.SIGNING_KEY_PATH
    if os.path.exists(path):
        with open(path, "rb") as f:
            return serialization.load_pem_private_key(f.read(), password=None)
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    with open(path, "wb") as f:
        f.write(pem)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass
    return key


def public_key_base64() -> str:
    key = load_or_create_private_key()
    raw = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode()


def canonical_payload(payload: dict) -> bytes:
    """Deterministic serialization used for signing and verification."""
    return json.dumps(payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode("utf-8")


def sign_payload(payload: dict) -> str:
    key = load_or_create_private_key()
    sig = key.sign(canonical_payload(payload))
    return base64.b64encode(sig).decode()


def verify_signature(payload: dict, signature_b64: str, public_key_b64: str) -> bool:
    try:
        pub_raw = base64.b64decode(public_key_b64)
        pub = Ed25519PublicKey.from_public_bytes(pub_raw)
        sig = base64.b64decode(signature_b64)
        pub.verify(sig, canonical_payload(payload))
        return True
    except Exception:
        return False
