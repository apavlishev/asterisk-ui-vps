"""
Logic Core License Server — FastAPI application.

Client endpoints (called by the PBX panel):
    POST /api/v1/activate     {license_key, fingerprint, hostname}
    POST /api/v1/validate     {license_key, fingerprint}
    POST /api/v1/deactivate   {license_key, fingerprint}
    GET  /api/v1/public-key   -> Ed25519 public key (base64)

Admin endpoints (Bearer ADMIN_TOKEN):
    POST /admin/licenses                      create a license
    GET  /admin/licenses                      list licenses
    GET  /admin/licenses/{key}/activations    list activations
    POST /admin/licenses/{key}/revoke/{fp}    revoke one activation
"""

import time
from collections import defaultdict, deque

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .licensing import activate, deactivate, issue_license, issue_nonce, validate
from .models import Activation, License, get_db, init_db
from .signing import public_key_base64

app = FastAPI(title="Logic Core License Server", version="1.0.0")

# ---- simple in-memory rate limiter (per IP) ----
_hits: dict[str, deque] = defaultdict(deque)


def rate_limit(request: Request):
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    q = _hits[ip]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= settings.RATE_LIMIT_PER_MIN:
        raise HTTPException(status_code=429, detail="Слишком много запросов")
    q.append(now)


def require_admin(authorization: str = Header(default="")):
    if not settings.ADMIN_TOKEN:
        raise HTTPException(status_code=503, detail="ADMIN_TOKEN не настроен")
    token = authorization.replace("Bearer", "").strip()
    if token != settings.ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Недостаточно прав")
    return True


@app.on_event("startup")
def _startup():
    init_db()
    # Touch the key so it exists and can be printed for the client build.
    print("[license] public key:", public_key_base64())


# ================= CLIENT API =================
class ChallengeIn(BaseModel):
    fingerprint: str = Field(min_length=4, max_length=128)


class ActivateIn(BaseModel):
    license_key: str = Field(min_length=4, max_length=128)
    fingerprint: str = Field(min_length=4, max_length=128)
    hostname: str = ""
    nonce: str = ""
    device_pubkey: str = ""
    device_sig: str = ""


class ValidateIn(BaseModel):
    license_key: str
    fingerprint: str
    nonce: str = ""
    device_pubkey: str = ""
    device_sig: str = ""


class DeactivateIn(BaseModel):
    license_key: str
    fingerprint: str


@app.get("/health")
def health():
    return {"status": "ok", "service": "logic-core-license"}


@app.get("/api/v1/public-key")
def get_public_key():
    return {"algorithm": "ed25519", "public_key": public_key_base64()}


@app.post("/api/v1/challenge")
def api_challenge(body: ChallengeIn, request: Request, db: Session = Depends(get_db)):
    """Issues a single-use nonce the device must sign for activate/validate."""
    rate_limit(request)
    nonce = issue_nonce(db, body.fingerprint)
    return {
        "status": "ok",
        "nonce": nonce,
        "ttl": 120,
        "message_format": "{nonce}|{fingerprint}|{license_key}",
    }


@app.post("/api/v1/activate")
def api_activate(body: ActivateIn, request: Request, db: Session = Depends(get_db)):
    rate_limit(request)
    ip = request.client.host if request.client else ""
    payload, err, code = activate(
        db, body.license_key, body.fingerprint, ip, body.hostname,
        nonce=body.nonce, device_pubkey=body.device_pubkey, device_sig=body.device_sig,
    )
    if err:
        raise HTTPException(status_code=code, detail=err)
    return {"status": "ok", "license": payload}


@app.post("/api/v1/validate")
def api_validate(body: ValidateIn, request: Request, db: Session = Depends(get_db)):
    rate_limit(request)
    ip = request.client.host if request.client else ""
    payload, err, code = validate(
        db, body.license_key, body.fingerprint, ip,
        nonce=body.nonce, device_pubkey=body.device_pubkey, device_sig=body.device_sig,
    )
    if err:
        raise HTTPException(status_code=code, detail=err)
    return {"status": "ok", "license": payload}


@app.post("/api/v1/deactivate")
def api_deactivate(body: DeactivateIn, request: Request, db: Session = Depends(get_db)):
    rate_limit(request)
    ok, msg = deactivate(db, body.license_key, body.fingerprint)
    if not ok:
        raise HTTPException(status_code=404, detail=msg)
    return {"status": "ok", "message": msg}


# ================= ADMIN API =================
class IssueIn(BaseModel):
    tier: str = "Pro"
    max_users: int = 50
    max_activations: int = 1
    customer: str = ""
    notes: str = ""
    days: int | None = None
    features: dict = Field(default_factory=dict)


@app.post("/admin/licenses", dependencies=[Depends(require_admin)])
def admin_issue(body: IssueIn, db: Session = Depends(get_db)):
    row = issue_license(
        db, tier=body.tier, max_users=body.max_users,
        max_activations=body.max_activations, customer=body.customer,
        notes=body.notes, days=body.days, features=body.features,
    )
    return {
        "status": "ok",
        "license_key": row.license_key,
        "tier": row.tier,
        "max_users": row.max_users,
        "max_activations": row.max_activations,
        "expires_at": row.expires_at,
    }


@app.get("/admin/licenses", dependencies=[Depends(require_admin)])
def admin_list(db: Session = Depends(get_db)):
    rows = db.scalars(select(License).order_by(License.created_at.desc())).all()
    return {"status": "ok", "licenses": [
        {
            "license_key": r.license_key, "tier": r.tier, "max_users": r.max_users,
            "active": r.active, "customer": r.customer,
            "expires_at": r.expires_at, "created_at": r.created_at,
        } for r in rows
    ]}


@app.get("/admin/licenses/{key}/activations", dependencies=[Depends(require_admin)])
def admin_activations(key: str, db: Session = Depends(get_db)):
    row = db.scalar(select(License).where(License.license_key == key))
    if not row:
        raise HTTPException(status_code=404, detail="Лицензия не найдена")
    acts = db.scalars(
        select(Activation).where(Activation.license_id == row.id)
    ).all()
    return {"status": "ok", "activations": [
        {
            "fingerprint": a.fingerprint, "ip": a.ip, "hostname": a.hostname,
            "revoked": a.revoked, "last_seen": a.last_seen, "activated_at": a.activated_at,
        } for a in acts
    ]}


@app.post("/admin/licenses/{key}/revoke/{fingerprint}", dependencies=[Depends(require_admin)])
def admin_revoke(key: str, fingerprint: str, db: Session = Depends(get_db)):
    ok, msg = deactivate(db, key, fingerprint)
    if not ok:
        raise HTTPException(status_code=404, detail=msg)
    return {"status": "ok", "message": msg}


@app.get("/", response_class=HTMLResponse)
def index():
    return (
        "<html><head><title>Logic Core License Server</title></head>"
        "<body style='font-family:system-ui;max-width:640px;margin:60px auto'>"
        "<h1>Logic Core License Server</h1>"
        "<p>API работает. См. <code>/docs</code> для интерактивной документации.</p>"
        f"<p>Public key (ed25519):<br><code style='word-break:break-all'>{public_key_base64()}</code></p>"
        "</body></html>"
    )
