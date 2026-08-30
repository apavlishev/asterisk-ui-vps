import requests
import json
import os

def init_plugin(app, config):
    pass

def on_call_hangup(call_info, config):
    ghl = config.get("gohighlevel", {})
    if not ghl.get("enabled"):
        return None
    webhook_url = ghl.get("webhook_url", "").strip()
    if not webhook_url:
        return None

    try:
        r = requests.post(webhook_url, json=call_info, timeout=8)
        return r.status_code in [200, 201]
    except Exception as e:
        print(f"Error posting call to GHL: {e}")
        return False
