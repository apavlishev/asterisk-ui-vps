"""
Migration 2.6.1 — ensures the SIP contact IP history store exists.

Idempotent: safe to run multiple times.
"""

import json
import os

HISTORY_FILE = "/opt/sip_contact_history.json"


def run_migration(cfg):
    if not isinstance(cfg, dict):
        return

    if not os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump({}, f, ensure_ascii=False, indent=2)
            print(f"[migration 2.6.1] created {HISTORY_FILE}")
        except Exception as e:
            print(f"[migration 2.6.1] could not create history file: {e}")
    else:
        print(f"[migration 2.6.1] history file already present at {HISTORY_FILE}")
