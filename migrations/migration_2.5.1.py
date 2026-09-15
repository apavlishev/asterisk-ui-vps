"""
Migration 2.5.1 — ensures new module config defaults exist after update.

Idempotent: safe to run multiple times.
"""


def run_migration(cfg):
    if not isinstance(cfg, dict):
        return

    # Neural Speech AI / Whisper plugin settings (new form route)
    ai = cfg.get("ai_whisper")
    if not isinstance(ai, dict):
        ai = {}
    ai.setdefault("enabled", True)
    ai.setdefault("diarization", True)
    ai.setdefault("send_to_crm", True)
    ai.setdefault("stt_engine", "openai_whisper")
    ai.setdefault("llm_model", "gpt-4o-mini")
    ai.setdefault("min_duration", 5)
    cfg["ai_whisper"] = ai

    # Live transcription settings (daemon + settings.html)
    lt = cfg.get("live_transcribe")
    if not isinstance(lt, dict):
        lt = {}
    lt.setdefault("primary_language", "ru-RU")
    lt.setdefault("language_codes", ["ru-RU"])
    lt.setdefault("custom_vocabulary", [])
    cfg["live_transcribe"] = lt

    # Storage / CRM containers used by newer code paths
    cfg.setdefault("yandex_disk", {})
    cfg.setdefault("ftp", {})
    cfg.setdefault("sip_trunks", [])
    cfg.setdefault("plugins_disabled", [])

    print("[migration 2.5.1] config defaults ensured.")
