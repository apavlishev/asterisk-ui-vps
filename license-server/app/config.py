"""
Logic Core License Server — configuration.

All settings come from environment variables so the service can be deployed
from a container or systemd unit without code changes. Secrets (JWT signing,
admin token) must be provided via the environment in production.
"""

import os


def _bool(name, default=False):
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


class Settings:
    # Database (PostgreSQL in production, SQLite for local dev)
    DATABASE_URL = os.environ.get(
        "LICENSE_DATABASE_URL",
        "sqlite:///./license.db",
    )

    # Base URL advertised to clients
    PUBLIC_BASE_URL = os.environ.get("LICENSE_PUBLIC_BASE_URL", "http://localhost:8000")

    # Ed25519 private key (PEM) used to sign issued licenses.
    # If missing, a key is generated at startup and stored on disk (dev only).
    SIGNING_KEY_PATH = os.environ.get("LICENSE_SIGNING_KEY", "./keys/license_ed25519.pem")

    # Admin API token (Bearer). REQUIRED in production.
    ADMIN_TOKEN = os.environ.get("LICENSE_ADMIN_TOKEN", "")

    # Default lease lifetime for an activation (days). Clients re-validate.
    LEASE_DAYS = int(os.environ.get("LICENSE_LEASE_DAYS", "30"))

    # Hard cap on activations per license (0 = unlimited)
    DEFAULT_MAX_ACTIVATIONS = int(os.environ.get("LICENSE_MAX_ACTIVATIONS", "1"))

    # Rate limiting
    RATE_LIMIT_PER_MIN = int(os.environ.get("LICENSE_RATE_LIMIT_PER_MIN", "30"))

    DEBUG = _bool("LICENSE_DEBUG", False)


settings = Settings()
