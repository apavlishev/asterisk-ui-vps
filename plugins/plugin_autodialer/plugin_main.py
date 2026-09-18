"""
Auto Dialer & Base Cleanser Plugin Entry Point
"""
import os
import sys

GUI_DIR = os.environ.get("ASTERISK_GUI_DIR", "/opt/asterisk-gui")
if os.path.isdir(GUI_DIR) and GUI_DIR not in sys.path:
    sys.path.insert(0, GUI_DIR)

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

from autodialer_engine import AutoDialerEngine

def init_plugin(app, config):
    """Called on core initialization."""
    engine = AutoDialerEngine()
    return True

def on_enable(config):
    """Regenerates Asterisk dialplan so autodialer contexts become active."""
    try:
        import app as core
        core.generate_dialplan_from_tree()
        return True
    except Exception as e:
        print(f"[autodialer] on_enable error: {e}")
        return False

def on_disable(config):
    """Stops any active campaigns when the plugin is turned off."""
    try:
        engine = AutoDialerEngine()
        for camp in engine.get_all_campaigns():
            if camp.get('status') == 'running':
                engine.stop_campaign(camp['id'])
        import app as core
        core.generate_dialplan_from_tree()
        return True
    except Exception as e:
        print(f"[autodialer] on_disable error: {e}")
        return False

def on_call_hangup(call_info, config):
    """Standard hook - autodialer uses dedicated hangup_handler script."""
    return None
