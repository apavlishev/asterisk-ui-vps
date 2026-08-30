import requests
import json
import os

def init_plugin(app, config):
    pass

def on_call_hangup(call_info, config):
    zoho = config.get("zoho", {})
    if not zoho.get("enabled"):
        return None
    # Handler for Zoho CRM Events
    return True
