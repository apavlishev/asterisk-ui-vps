#!/usr/bin/env python3
"""
License Server admin CLI.

Usage:
    python manage.py issue --tier Pro --max-users 50 --customer "ACME" [--days 365]
    python manage.py list
    python manage.py activations --key LGC-XXXX
    python manage.py revoke --key LGC-XXXX --fingerprint LGC-....
    python manage.py public-key
"""

import argparse
import json
import sys

from app.config import settings
from app.licensing import issue_license
from app.models import Activation, License, SessionLocal, init_db
from app.signing import public_key_base64


def cmd_issue(args):
    init_db()
    db = SessionLocal()
    try:
        features = json.loads(args.features) if args.features else {}
        row = issue_license(
            db, tier=args.tier, max_users=args.max_users,
            max_activations=args.max_activations, customer=args.customer,
            notes=args.notes, days=args.days, features=features,
        )
        print(json.dumps({
            "license_key": row.license_key, "tier": row.tier,
            "max_users": row.max_users, "max_activations": row.max_activations,
            "expires_at": str(row.expires_at),
        }, ensure_ascii=False, indent=2))
    finally:
        db.close()


def cmd_list(_args):
    init_db()
    db = SessionLocal()
    try:
        rows = db.query(License).order_by(License.created_at.desc()).all()
        for r in rows:
            print(f"{r.license_key}  {r.tier:8}  users={r.max_users:<5} "
                  f"active={r.active}  customer={r.customer!r}  expires={r.expires_at}")
    finally:
        db.close()


def cmd_activations(args):
    init_db()
    db = SessionLocal()
    try:
        row = db.query(License).filter(License.license_key == args.key).first()
        if not row:
            print("Лицензия не найдена"); return
        for a in db.query(Activation).filter(Activation.license_id == row.id).all():
            print(f"{a.fingerprint}  ip={a.ip}  host={a.hostname}  "
                  f"revoked={a.revoked}  last_seen={a.last_seen}")
    finally:
        db.close()


def cmd_revoke(args):
    from app.licensing import deactivate
    init_db()
    db = SessionLocal()
    try:
        print(deactivate(db, args.key, args.fingerprint))
    finally:
        db.close()


def cmd_public_key(_args):
    print(public_key_base64())


def main():
    p = argparse.ArgumentParser(description="Logic Core License Server CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("issue")
    s.add_argument("--tier", default="Pro")
    s.add_argument("--max-users", type=int, default=50)
    s.add_argument("--max-activations", type=int, default=1)
    s.add_argument("--customer", default="")
    s.add_argument("--notes", default="")
    s.add_argument("--days", type=int, default=None)
    s.add_argument("--features", default="")
    s.set_defaults(func=cmd_issue)

    s = sub.add_parser("list")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("activations")
    s.add_argument("--key", required=True)
    s.set_defaults(func=cmd_activations)

    s = sub.add_parser("revoke")
    s.add_argument("--key", required=True)
    s.add_argument("--fingerprint", required=True)
    s.set_defaults(func=cmd_revoke)

    s = sub.add_parser("public-key")
    s.set_defaults(func=cmd_public_key)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
