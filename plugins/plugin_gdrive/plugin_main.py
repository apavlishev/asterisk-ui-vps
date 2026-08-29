import requests

def init_plugin(app, config):
    pass

def on_call_hangup(call_info, config):
    gd_cfg = config.get("gdrive", {})
    if not gd_cfg.get("enabled"):
        return None
    return True
