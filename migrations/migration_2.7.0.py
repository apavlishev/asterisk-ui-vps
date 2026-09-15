"""
Migration 2.7.0 — prepares the firewall manager state and default port rules.

Idempotent: safe to run multiple times.
"""

import json
import os

CONFIG_DIR = "/etc/asterisk-gui"
STATE_FILE = os.path.join(CONFIG_DIR, "firewall.json")


def run_migration(cfg):
    if not isinstance(cfg, dict):
        return

    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
    except Exception as e:
        print(f"[migration 2.7.0] cannot create {CONFIG_DIR}: {e}")
        return

    if not os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump({"enabled": False, "installed_defaults": False, "rules": []},
                          f, ensure_ascii=False, indent=2)
            print(f"[migration 2.7.0] created {STATE_FILE}")
        except Exception as e:
            print(f"[migration 2.7.0] cannot create state file: {e}")
    else:
        print(f"[migration 2.7.0] firewall state already present at {STATE_FILE}")
