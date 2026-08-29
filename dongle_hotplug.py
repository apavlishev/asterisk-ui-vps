#!/usr/bin/env python3
"""
Huawei Dongle Auto-Hotplug Handler for Asterisk PBX
Automatically detects USB insertion/removal, assigns dynamic ttyUSB ports to dongle.conf,
sets proper permissions, and reloads chan_dongle in Asterisk.
"""
import glob
import subprocess
import time
import os
import re
import datetime

DONGLE_CONF = "/etc/asterisk/dongle.conf"
LOG_FILE = "/var/log/asterisk/hotplug.log"

def log(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def set_permissions(ports):
    for p in ports:
        try:
            os.chmod(p, 0o666)
            subprocess.run(["chown", "asterisk:dialout", p], capture_output=True)
        except Exception as e:
            log(f"Permission error on {p}: {e}")

def detect_ports():
    ports = sorted(glob.glob("/dev/ttyUSB*"))
    if not ports:
        return None, None, []
    
    set_permissions(ports)
    
    n = len(ports)
    if n >= 3:
        # Standard Huawei mapping (e.g. 0=modem, 1=audio, 2=data or 3, 4, 5)
        audio = ports[-2]
        data = ports[-1]
    elif n == 2:
        audio = ports[0]
        data = ports[1]
    else:
        audio = ports[0]
        data = ports[0]
        
    return audio, data, ports

def update_dongle_conf(audio_port, data_port):
    if not os.path.exists(DONGLE_CONF):
        log(f"Config {DONGLE_CONF} does not exist.")
        return False
    
    with open(DONGLE_CONF, "r", encoding="utf-8") as f:
        content = f.read()
        
    # Replace audio and data in dongle0 section
    new_content = re.sub(r'audio\s*=\s*/dev/ttyUSB\d+', f'audio={audio_port}', content)
    new_content = re.sub(r'data\s*=\s*/dev/ttyUSB\d+', f'data={data_port}', new_content)
    
    # Make sure exten is set to 's'
    new_content = re.sub(r'exten\s*=\s*\+?79000000000', 'exten=s', new_content)
    
    if new_content != content:
        with open(DONGLE_CONF, "w", encoding="utf-8") as f:
            f.write(new_content)
        log(f"Updated {DONGLE_CONF}: audio={audio_port}, data={data_port}")
        return True
    return False

def reload_asterisk():
    try:
        res = subprocess.run(["asterisk", "-rx", "module reload chan_dongle.so"], capture_output=True, text=True, timeout=5)
        log(f"Asterisk reload: {res.stdout.strip()}")
        time.sleep(1)
        res2 = subprocess.run(["asterisk", "-rx", "dongle restart now dongle0"], capture_output=True, text=True, timeout=5)
        log(f"Dongle restart: {res2.stdout.strip()}")
    except Exception as e:
        log(f"Asterisk reload error: {e}")

def main():
    # Allow USB devices 2 seconds to settle
    time.sleep(2)
    
    audio, data, ports = detect_ports()
    if not ports:
        log("Hotplug event: All USB modems disconnected.")
        try:
            subprocess.run(["asterisk", "-rx", "dongle stop dongle0"], capture_output=True, timeout=3)
        except Exception:
            pass
        return
        
    log(f"Hotplug event: Found {len(ports)} USB serial ports: {ports}. Mapped audio={audio}, data={data}")
    updated = update_dongle_conf(audio, data)
    reload_asterisk()

if __name__ == "__main__":
    main()
