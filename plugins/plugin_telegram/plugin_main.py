import requests

def init_plugin(app, config):
    pass

def on_call_hangup(call_info, config):
    tg_cfg = config.get("telegram", {})
    if not tg_cfg.get("enabled"):
        return None
    return True
