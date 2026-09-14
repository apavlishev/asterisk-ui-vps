import requests


def init_plugin(app, config):
    pass


def on_call_hangup(call_info, config):
    """Push a completed-call record into Zoho CRM via the given webhook/API."""
    zoho = config.get("zoho", {})
    if not zoho.get("enabled"):
        return None

    webhook_url = (zoho.get("webhook_url") or zoho.get("api_url") or "").strip()
    token = (zoho.get("token") or "").strip()
    if not webhook_url:
        return None

    duration = int(call_info.get("duration", 0) or 0)
    disposition = call_info.get("disposition", "ANSWERED")
    payload = {
        "call_id": call_info.get("call_id"),
        "phone": call_info.get("src") if call_info.get("direction") == "inbound" else call_info.get("dst"),
        "direction": call_info.get("direction", "outbound"),
        "duration": duration,
        "status": "Answered" if disposition == "ANSWERED" else "No Answer",
        "recording_url": call_info.get("recording_url", ""),
        "start_time": call_info.get("timestamp", ""),
    }
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        r = requests.post(webhook_url, json=payload, headers=headers, timeout=8)
        return r.status_code in [200, 201, 202]
    except Exception as e:
        print(f"Error posting call to Zoho: {e}")
        return False
