import requests


def init_plugin(app, config):
    pass


def on_call_hangup(call_info, config):
    """Create a call ticket/event in Zendesk via the Talk API or a webhook."""
    zd = config.get("zendesk", {})
    if not zd.get("enabled"):
        return None

    webhook_url = (zd.get("webhook_url") or "").strip()
    subdomain = (zd.get("subdomain") or "").strip()
    token = (zd.get("token") or "").strip()
    email = (zd.get("email") or "").strip()

    direction = call_info.get("direction", "outbound")
    duration = int(call_info.get("duration", 0) or 0)
    disposition = call_info.get("disposition", "ANSWERED")
    src = call_info.get("src")
    dst = call_info.get("dst")

    payload = {
        "call_id": call_info.get("call_id"),
        "phone": src if direction == "inbound" else dst,
        "direction": direction,
        "duration": duration,
        "status": "completed" if disposition == "ANSWERED" else "missed",
        "recording_url": call_info.get("recording_url", ""),
        "timestamp": call_info.get("timestamp", ""),
    }

    try:
        if webhook_url:
            r = requests.post(webhook_url, json=payload, timeout=8)
            return r.status_code in [200, 201, 202]

        if subdomain and token:
            url = f"https://{subdomain}.zendesk.com/api/v2/calls"
            headers = {"Content-Type": "application/json"}
            if email:
                headers["Authorization"] = f"Bearer {token}"
            else:
                headers["Authorization"] = f"Bearer {token}"
            r = requests.post(url, json=payload, headers=headers, timeout=8)
            return r.status_code in [200, 201]

        return None
    except Exception as e:
        print(f"Error posting call to Zendesk: {e}")
        return False
