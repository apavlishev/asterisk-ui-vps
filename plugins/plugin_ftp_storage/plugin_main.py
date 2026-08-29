import ftplib

def init_plugin(app, config):
    pass

def on_call_hangup(call_info, config):
    ftp_cfg = config.get("ftp", {})
    if not ftp_cfg.get("enabled"):
        return None
    return True
