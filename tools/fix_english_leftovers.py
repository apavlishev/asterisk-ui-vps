#!/usr/bin/env python3
"""
Targeted fixer for locale entries that were left as English fallbacks by an
earlier manual pass. These have no Cyrillic, so the automatic detector skips
them; here we translate them explicitly from their English source.

Usage: python3 tools/fix_english_leftovers.py [--dry-run]
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from translate_locales import translate, LANG_MAP  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCALES_DIR = os.path.join(BASE, "locales")

EN_SOURCE_OVERRIDES = {
    # IVR editor
    "btn_delete_node": "Delete Node",
    "btn_cancel": "Cancel",
    "btn_save_node": "Save Node",
    "ivr_title": "Interactive IVR Designer",
    "ivr_subtitle": "Visual design of voice scenarios, direct dial, queues and schedules.",
    "ivr_btn_apply": "Apply IVR scheme to Asterisk",
    "ivr_tab_canvas": "Canvas view",
    "ivr_tab_table": "Table view",
    "ivr_tab_schedule": "Schedule",
    "ivr_label_trunk": "Configure voice scenario for line:",
    "ivr_opt_default": "Global scenario (Default)",
    "ivr_btn_move": "Move / Edit",
    "ivr_btn_link": "Draw link (Arrow)",
    "ivr_btn_center": "Center canvas",
    "ivr_btn_add": "Add node",
    "ivr_modal_title": "Edit node:",
    "ivr_modal_name": "Node name",
    "ivr_modal_type": "Node type",
    "ivr_modal_dtmf": "DTMF key (0-9, *, #, i, t)",
    "ivr_modal_target": "Target extension / SIP peer",
    # GSM guide
    "gsm_guide_1_title": "1. Voice call support (Voice-enabled firmware)",
    "gsm_guide_1_text": "Regular 4G internet modems (HiLink / NDIS) <b>cannot transmit voice</b> in Asterisk. You must use <b>Stick (Serial/Modem)</b> mode with active voice AT commands and PCM 8kHz audio stream support.",
    "gsm_guide_2_title": "2. Powered USB hub (Active USB 2.0 hub)",
    "gsm_guide_2_text": "During call setup a modem can draw up to <b>1.5-2.0 Amps</b> at peak. When connecting more than 1-2 modems to a server, use an <b>active USB hub with an external power supply</b> to prevent <code>/dev/ttyUSB*</code> ports from dropping.",
    "gsm_guide_3_title": "Verified 3G / 4G modems and voice cellular modules for Asterisk",
    "gsm_table_col1": "Model / Chipset",
    "gsm_table_col2": "Network standards",
    "gsm_table_col3": "PBX driver",
    "gsm_table_col4": "Support status",
    # Call log table
    "data_i_vremya": "Date & time",
    "dlitel_nost": "Duration",
    "komu": "To",
    "ot_kogo": "From",
    "tip": "Type",
    "zapis": "Recording",
    # Security section
    "k_avtorizatsia_paneli": "Panel authorization",
    "k_fail2ban_antifrod": "Fail2ban & Antifraud",
    "k_kvoty_i_logi": "Quotas & Logs",
}

# These locales already contain a correct translation (English is fine for en).
TARGETS = ["es", "ar", "de", "fr", "hi", "ja", "pt", "zh"]


def main():
    dry = "--dry-run" in sys.argv
    cache = {}
    total = 0
    for lang in TARGETS:
        path = os.path.join(LOCALES_DIR, f"{lang}.json")
        data = json.load(open(path, encoding="utf-8"))
        target = LANG_MAP[lang]
        fixed = 0
        for key, source in EN_SOURCE_OVERRIDES.items():
            # Translate English -> target (source='en')
            translated = translate(source, target, source="en")
            if not translated or translated.strip() == source.strip():
                print(f"   [!] {lang}:{key} -> unchanged")
                continue
            data[key] = translated
            fixed += 1
            time.sleep(0.25)
        print(f"{lang}: fixed {fixed}/{len(EN_SOURCE_OVERRIDES)}")
        total += fixed
        if not dry:
            json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("TOTAL fixed:", total)


if __name__ == "__main__":
    main()
