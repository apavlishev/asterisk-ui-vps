import requests
import json
import os

def init_plugin(app, config):
    pass

def on_call_hangup(call_info, config):
    pipe = config.get("pipedrive", {})
    if not pipe.get("enabled"):
        return None
    token = pipe.get("token", "").strip()
    subdomain = pipe.get("subdomain", "").strip()
    if not token:
        return None

    src = call_info.get("src")
    dst = call_info.get("dst")
    duration = int(call_info.get("duration", 0))
    disposition = call_info.get("disposition", "ANSWERED")
    rec_url = call_info.get("recording_url", "")

    url = f"https://api.pipedrive.com/v1/callLogs?api_token={token}"
    payload = {
        "to_phone_number": dst,
        "from_phone_number": src,
        "start_time": call_info.get("timestamp", ""),
        "duration": duration,
        "outcome": "connected" if disposition == "ANSWERED" else "no_answer",
        "recording_url": rec_url
    }
    try:
        r = requests.post(url, json=payload, timeout=8)
        return r.status_code in [200, 201]
    except Exception as e:
        print(f"Error posting call to Pipedrive: {e}")
        return False
