import requests
import json
import os

def init_plugin(app, config):
    pass

def on_call_hangup(call_info, config):
    zd = config.get("zendesk", {})
    if not zd.get("enabled"):
        return None
    return True
