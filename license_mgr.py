import os
import hashlib
import json
import time

LICENSE_FILE = '/opt/license.json'

def get_server_fingerprint():
    """Generates immutable hardware/server fingerprint."""
    components = []
    
    # 1. CPU Serial for Raspberry Pi
    try:
        if os.path.exists('/proc/cpuinfo'):
            with open('/proc/cpuinfo', 'r') as f:
                for line in f:
                    if line.startswith('Serial'):
                        val = line.split(':')[1].strip()
                        if val and val != '0000000000000000':
                            components.append(f"CPU:{val}")
                            break
    except Exception:
        pass

    # 2. DMI System UUID for VPS / Physical Servers
    try:
        for p in ['/sys/class/dmi/id/product_uuid', '/etc/machine-id']:
            if os.path.exists(p):
                with open(p, 'r') as f:
                    val = f.read().strip()
                    if val and val != "None":
                        components.append(f"DMI:{val}")
                        break
    except Exception:
        pass

    # 3. MAC address of primary network interface
    try:
        for iface in ['eth0', 'ens3', 'enp0s3', 'wlan0', 'en0']:
            mac_path = f"/sys/class/net/{iface}/address"
            if os.path.exists(mac_path):
                with open(mac_path, 'r') as f:
                    mac = f.read().strip()
                    if mac:
                        components.append(f"MAC:{mac}")
                        break
    except Exception:
        pass

    raw_id = ":".join(components) if components else "DEFAULT_SERVER_ID"
    h = hashlib.sha256(raw_id.encode()).hexdigest().upper()
    return f"LGC-{h[0:4]}-{h[4:8]}-{h[8:12]}-{h[12:16]}"

def load_license():
    """Loads active license status or returns Free Core tier."""
    fp = get_server_fingerprint()
    if os.path.exists(LICENSE_FILE):
        try:
            with open(LICENSE_FILE, 'r') as f:
                data = json.load(f)
            # Verify license fingerprint binding
            if data.get('server_id') == fp or data.get('server_id') == 'UNLIMITED':
                return data
        except Exception:
            pass
            
    # Default Free Core License
    return {
        "tier": "Core Free (Community)",
        "server_id": fp,
        "status": "Active (Free Core)",
        "max_users": 2,
        "active_plugins": [],
        "expires_at": None,
        "is_free_core": True
    }

def get_max_allowed_users():
    lic = load_license()
    return lic.get('max_users', 2)

def is_plugin_active(plugin_id):
    lic = load_license()
    if lic.get('tier') in ['Enterprise', 'Ultimate', 'Pro']:
        return True
    return plugin_id in lic.get('active_plugins', [])

if __name__ == '__main__':
    print("Server Fingerprint:", get_server_fingerprint())
    print("Current License:", load_license())
