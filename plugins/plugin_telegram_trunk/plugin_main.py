"""
Telegram SIP Trunk plugin entry point.

The heavy lifting (PJSIP endpoint, dialplan context, gateway lifecycle) lives in
the core modules, but this plugin exposes lifecycle hooks so the trunk is
registered/unregistered together with the plugin state.
"""

import os
import sys

GUI_DIR = os.environ.get("ASTERISK_GUI_DIR", "/opt/asterisk-gui")
if os.path.isdir(GUI_DIR) and GUI_DIR not in sys.path:
    sys.path.insert(0, GUI_DIR)


def init_plugin(app, config):
    """Called when the plugin is loaded."""
    return True


def on_call_hangup(call_info, config):
    """Telegram calls are handled by the SIP gateway; nothing to post-process here."""
    return None


def on_enable(config):
    """Regenerate core config so the Telegram endpoint/dialplan become active."""
    try:
        import app as core
        core.generate_pjsip_conf()
        core.generate_dialplan_from_tree()
        return True
    except Exception as e:
        print(f"[telegram_trunk] on_enable failed: {e}")
        return False


def on_disable(config):
    """Regenerate core config after the trunk is switched off."""
    try:
        import app as core
        core.generate_pjsip_conf()
        core.generate_dialplan_from_tree()
        return True
    except Exception as e:
        print(f"[telegram_trunk] on_disable failed: {e}")
        return False
