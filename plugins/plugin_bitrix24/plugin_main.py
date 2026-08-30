import requests
import json
import os

def init_plugin(app, config):
    pass

def on_call_hangup(call_info, config):
    b24 = config.get("bitrix24", {})
    if not b24.get("enabled"):
        return None
    webhook_url = b24.get("webhook_url", "").strip()
    if not webhook_url:
        return None
        
    call_id = call_info.get("call_id")
    src = call_info.get("src")
    dst = call_info.get("dst")
    duration = int(call_info.get("duration", 0))
    disposition = call_info.get("disposition", "ANSWERED")
    rec_url = call_info.get("recording_url", "")
    
    # Register & Finish external call in Bitrix24 Telephony API
    finish_url = webhook_url.rstrip("/") + "/telephony.externalcall.finish"
    payload = {
        "CALL_ID": call_id,
        "USER_PHONE_INNER": dst if call_info.get("direction") == "inbound" else src,
        "USER_ID": b24.get("user_mapping", {}).get(dst if call_info.get("direction") == "inbound" else src, 1),
        "DURATION": duration,
        "STATUS_CODE": "200" if disposition == "ANSWERED" else "304",
        "RECORD_URL": rec_url,
        "TYPE": 1 if call_info.get("direction") == "inbound" else 2
    }
    try:
        r = requests.post(finish_url, json=payload, timeout=8)
        return r.status_code == 200
    except Exception as e:
        print(f"Error posting call to Bitrix24: {e}")
        return False
