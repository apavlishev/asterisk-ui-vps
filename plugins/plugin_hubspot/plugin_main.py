import requests
import json
import os
import datetime

def init_plugin(app, config):
    pass

def on_call_hangup(call_info, config):
    hs = config.get("hubspot", {})
    if not hs.get("enabled"):
        return None
    token = hs.get("token", "").strip()
    if not token:
        return None

    src = call_info.get("src")
    dst = call_info.get("dst")
    duration = int(call_info.get("duration", 0)) * 1000 # ms in HubSpot
    disposition = call_info.get("disposition", "ANSWERED")
    rec_url = call_info.get("recording_url", "")
    now_iso = datetime.datetime.utcnow().isoformat() + "Z"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    url = "https://api.hubapi.com/crm/v3/objects/calls"
    payload = {
        "properties": {
            "hs_timestamp": now_iso,
            "hs_call_title": f"Звонок: {src} -> {dst}",
            "hs_call_duration": duration,
            "hs_call_status": "COMPLETED" if disposition == "ANSWERED" else "NO_ANSWER",
            "hs_call_direction": "INBOUND" if call_info.get("direction") == "inbound" else "OUTBOUND",
            "hs_call_recording_url": rec_url
        }
    }
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=8)
        return r.status_code in [200, 201]
    except Exception as e:
        print(f"Error posting call to HubSpot: {e}")
        return False
