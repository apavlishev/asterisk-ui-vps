"""
Migration 2.6.0 — prepares config for the Telegram SIP trunk integration.

Idempotent: safe to run multiple times.
"""


def run_migration(cfg):
    if not isinstance(cfg, dict):
        return

    tg = cfg.get("telegram_trunk")
    if not isinstance(tg, dict):
        tg = {}

    tg.setdefault("enabled", False)
    tg.setdefault("installed", False)
    tg.setdefault("port", 5062)
    tg.setdefault("inbound_target", "ALL")
    tg.setdefault("api_id", "")
    tg.setdefault("api_hash", "")
    tg.setdefault("phone", "")
    cfg["telegram_trunk"] = tg

    # Dedicated IVR route slot for Telegram inbound calls
    trees = cfg.get("ivr_trees")
    if not isinstance(trees, dict):
        trees = {}
        cfg["ivr_trees"] = trees

    print("[migration 2.6.0] telegram_trunk config prepared.")
