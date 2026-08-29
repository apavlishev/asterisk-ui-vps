import requests

def init_plugin(app, config):
    pass

def on_call_hangup(call_info, config):
    yd_cfg = config.get("yandex_disk", {})
    if not yd_cfg.get("enabled"):
        return None
    return True
