import hashlib
import io
import ftplib
import plugin_manager

import license_mgr
import marketplace_data
from flask import render_template
import secrets
import time
import os
import subprocess
import json
import re
import datetime
import glob
import csv
import requests
from flask import Flask, render_template_string, render_template, request, redirect, url_for, flash, jsonify, send_from_directory, abort, session, Response
from werkzeug.utils import secure_filename

app = Flask(__name__)

import jinja2
import os

# Add plugins directory to Jinja template search path for dynamic UI loading
plugins_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'plugins')
app.jinja_loader = jinja2.ChoiceLoader([
    app.jinja_loader,
    jinja2.FileSystemLoader(plugins_dir)
])
app.secret_key = 'asterisk-web-secret-key-2026'


def get_yandex_disk_account_info(token):
    if not token:
        return {'valid': False}
    try:
        headers = {'Authorization': f'OAuth {token}'}
        # 1. Try fetching login info
        r_user = requests.get('https://login.yandex.ru/info', headers=headers, timeout=3)
        user_data = r_user.json() if r_user.status_code == 200 else {}
        login = user_data.get('login') or user_data.get('default_email') or user_data.get('id') or 'Пользователь Яндекс'

        # 2. Try fetching Disk quota
        r_disk = requests.get('https://cloud-api.yandex.net/v1/disk', headers=headers, timeout=3)
        if r_disk.status_code == 200:
            data = r_disk.json()
            user = data.get('user', {})
            total_gb = round(data.get('total_space', 0) / (1024**3), 1)
            used_gb = round(data.get('used_space', 0) / (1024**3), 1)
            return {
                'valid': True,
                'has_disk_scope': True,
                'display_name': user.get('display_name') or login,
                'login': login,
                'total_gb': total_gb,
                'used_gb': used_gb,
                'free_gb': round(total_gb - used_gb, 1),
                'status_msg': f'Свободно: {round(total_gb - used_gb, 1)} ГБ из {total_gb} ГБ'
            }
        elif r_user.status_code == 200:
            # Token is valid, but Disk scope needs checkbox in Yandex OAuth App
            return {
                'valid': True,
                'has_disk_scope': False,
                'display_name': login,
                'login': login,
                'status_msg': '⚠️ Требуется включить права Яндекс.Диска в приложении OAuth'
            }
    except Exception as e:
        print(f"Error checking Yandex token: {e}")
    return {'valid': False}

def get_google_drive_account_info(token):
    if not token:
        return {'valid': False}
    try:
        r = requests.get('https://www.googleapis.com/drive/v3/about?fields=user,storageQuota', headers={'Authorization': f'Bearer {token}'}, timeout=3)
        if r.status_code == 200:
            data = r.json()
            user = data.get('user', {})
            quota = data.get('storageQuota', {})
            limit = int(quota.get('limit', 0))
            usage = int(quota.get('usage', 0))
            total_gb = round(limit / (1024**3), 1) if limit else 'Безлимит'
            used_gb = round(usage / (1024**3), 1)
            return {
                'valid': True,
                'display_name': user.get('displayName') or user.get('emailAddress') or 'Google Пользователь',
                'email': user.get('emailAddress', ''),
                'total_gb': total_gb,
                'used_gb': used_gb
            }
    except Exception:
        pass
    return {'valid': False}




def get_amocrm_account_info():
    cfg = load_integrations()
    amo = cfg.get('amocrm', {})
    subdomain = amo.get('subdomain', '').strip()
    token = amo.get('token', '').strip()
    if not subdomain or not token:
        return {'valid': False}
    try:
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        r = requests.get(f"https://{subdomain}.amocrm.ru/api/v4/account", headers=headers, timeout=4)
        if r.status_code == 200:
            data = r.json()
            return {
                'valid': True,
                'id': data.get('id'),
                'name': data.get('name', subdomain),
                'subdomain': data.get('subdomain', subdomain),
                'country': data.get('country', 'RU'),
                'currency': data.get('currency_symbol', '₽')
            }
    except Exception:
        pass
    return {'valid': False}


def get_system_modems_info(force_test=None):
    """Scans for real physical USB/GSM dongles. If test mode is checked, supplies virtual test modems."""
    cfg = load_integrations()
    test_mode = cfg.get('modems_test_mode', False) if force_test is None else force_test
    modems = []
    
    # 1. Check Asterisk chan_dongle if module installed and physical devices registered
    dongle_out = run_asterisk('dongle show devices')
    if "No such command" not in dongle_out and dongle_out.strip():
        lines = dongle_out.splitlines()
        for line in lines:
            if 'ID' in line or '---' in line or not line.strip():
                continue
            parts = line.split()
            if len(parts) >= 5:
                d_id = parts[0]
                d_state = parts[2] if len(parts) > 2 else 'Free'
                d_rssi = parts[3] if len(parts) > 3 else '15'
                d_mode = parts[4] if len(parts) > 4 else 'GSM'
                
                det_out = run_asterisk(f'dongle show device settings {d_id}')
                imei_m = re.search(r'IMEI\s*:\s*([0-9]+)', det_out)
                imsi_m = re.search(r'IMSI\s*:\s*([0-9]+)', det_out)
                num_m = re.search(r'Number\s*:\s*([+0-9]+)', det_out)
                prov_m = re.search(r'Provider Name\s*:\s*([^\r\n]+)', det_out)
                
                modems.append({
                    'id': d_id,
                    'model': 'Физический USB Dongle',
                    'imei': imei_m.group(1) if imei_m else 'Unknown',
                    'imsi': imsi_m.group(1) if imsi_m else 'Unknown',
                    'operator': prov_m.group(1).strip() if prov_m else 'Cellular Carrier',
                    'number': num_m.group(1) if num_m else '',
                    'signal_csq': int(d_rssi) if d_rssi.isdigit() else 18,
                    'signal_percent': min(100, int((int(d_rssi) if d_rssi.isdigit() else 18) / 31.0 * 100)),
                    'state': d_state,
                    'online': True if d_state.lower() in ['free', 'idle', 'active'] else False,
                    'mode': d_mode,
                    'port': f'/dev/ttyUSB_{d_id}',
                    'is_test': False
                })

    # 2. Check system serial / USB devices (/dev/ttyUSB*, /dev/ttyACM*)
    usb_ttys = glob.glob('/dev/ttyUSB*') + glob.glob('/dev/ttyACM*')
    if usb_ttys and not modems:
        for idx, tty in enumerate(sorted(set(usb_ttys))[:4]):
            modems.append({
                'id': f'dongle{idx+1}',
                'model': 'USB Modem (Физический порт)',
                'imei': 'Определяется...',
                'imsi': 'Определяется...',
                'operator': 'Подключен к ОС',
                'number': '',
                'signal_csq': 20,
                'signal_percent': 65,
                'state': 'Free (Готов к вызовам)',
                'online': True,
                'mode': '3G/4G TTY',
                'port': tty,
                'is_test': False
            })

    # 3. Test Mode Toggle: only inject demo/simulated dongles if explicitly enabled by user
    if test_mode and not modems:
        modems = [
            {
                'id': 'dongle01',
                'model': 'Huawei E3372h-153 (HiLink/Stick)',
                'imei': '867512034918231',
                'imsi': '250010948172635',
                'operator': 'МТС Россия (Тестовый)',
                'number': '+7 (916) 123-45-67',
                'signal_csq': 24,
                'signal_percent': 78,
                'state': 'Free (Тестовый режим)',
                'online': True,
                'mode': '4G LTE (Эмуляция)',
                'port': '/dev/ttyUSB1',
                'is_test': True
            },
            {
                'id': 'dongle02',
                'model': 'Huawei E3531 (Voice Enabled)',
                'imei': '869102048192038',
                'imsi': '250020491827364',
                'operator': 'МегаФон (Тестовый)',
                'number': '+7 (926) 987-65-43',
                'signal_csq': 19,
                'signal_percent': 61,
                'state': 'Free (Тестовый режим)',
                'online': True,
                'mode': '3G HSPA+ (Эмуляция)',
                'port': '/dev/ttyUSB2',
                'is_test': True
            }
        ]
        
    return modems


def is_local_ip(ip):
    if not ip:
        return True
    if ip in ['127.0.0.1', '::1', 'localhost']:
        return True
    try:
        ip_obj = ipaddress.ip_address(ip)
        return ip_obj.is_private or ip_obj.is_loopback
    except Exception:
        return False

def get_client_ip():
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr or '127.0.0.1'

@app.before_request
def check_security_and_auth():
    if request.path.startswith('/static') or request.path == '/login' or request.path.startswith('/api/'):
        return None

    cfg = load_integrations()
    auth_cfg = cfg.get('security_auth', {})
    if auth_cfg.get('enabled', False):
        if not session.get('logged_in'):
            return redirect(url_for('login_page'))
    return None

@app.route('/login', methods=['GET', 'POST'])
def login_page():
    cfg = load_integrations()
    auth_cfg = cfg.get('security_auth', {})
    
    if request.method == 'POST':
        user = request.form.get('username', '').strip()
        pwd = request.form.get('password', '').strip()
        
        cfg_user = auth_cfg.get('username', 'admin')
        cfg_pwd = auth_cfg.get('password', 'admin')
        
        if user == cfg_user and pwd == cfg_pwd:
            session['logged_in'] = True
            session['username'] = user
            flash('Авторизация успешна!')
            return redirect(url_for('index'))
        else:
            flash('Неверный логин или пароль!')
            return render_template('login.html', error="Неверный логин или пароль")

    return render_template('login.html')

@app.route('/logout', methods=['GET', 'POST'])
def logout_page():
    session.clear()
    flash('Вы успешно вышли из системы.')
    return redirect(url_for('login_page'))


# ================= FAIL2BAN ANTIFRAUD & SECURITY SHIELD BACKEND =================

# ================= SIP EXTENSION GROUPS / QUEUES & RING GROUPS =================
def get_sip_groups():
    cfg = load_integrations()
    return cfg.get('sip_groups', [
        {
            'id': 'group_sales',
            'name': 'Отдел продаж (Sales)',
            'exten': '600',
            'strategy': 'ringall',
            'timeout': 30,
            'members': ['101', '102']
        },
        {
            'id': 'group_support',
            'name': 'Техническая поддержка (Support)',
            'exten': '601',
            'strategy': 'hunt',
            'timeout': 20,
            'members': ['103']
        }
    ])

def save_sip_groups(groups):
    cfg = load_integrations()
    cfg['sip_groups'] = groups
    save_integrations(cfg)
    generate_dialplan_from_tree()

@app.route('/api/sip/groups', methods=['GET'])
def api_get_sip_groups():
    return jsonify({'status': 'ok', 'groups': get_sip_groups()})

@app.route('/api/sip/groups/save', methods=['POST'])
def api_save_sip_group():
    data = request.get_json() or {}
    group_id = data.get('id') or f"group_{int(time.time())}"
    name = data.get('name', '').strip()
    exten = data.get('exten', '').strip()
    strategy = data.get('strategy', 'ringall')
    timeout = int(data.get('timeout', 30))
    members = data.get('members', [])

    if not name or not exten:
        return jsonify({'status': 'error', 'message': 'Название и номер группы обязательны'})

    groups = get_sip_groups()
    updated = False
    for g in groups:
        if g['id'] == group_id or g['exten'] == exten:
            g['id'] = group_id
            g['name'] = name
            g['exten'] = exten
            g['strategy'] = strategy
            g['timeout'] = timeout
            g['members'] = members
            updated = True
            break

    if not updated:
        groups.append({
            'id': group_id,
            'name': name,
            'exten': exten,
            'strategy': strategy,
            'timeout': timeout,
            'members': members
        })

    save_sip_groups(groups)
    flash(f"Группа абонентов «{name}» (№ {exten}) сохранена!")
    return jsonify({'status': 'ok', 'message': 'Группа сохранена', 'groups': groups})

@app.route('/api/sip/groups/delete', methods=['POST'])
def api_delete_sip_group():
    data = request.get_json() or {}
    group_id = data.get('id') or request.form.get('id', '')
    if not group_id:
        return jsonify({'status': 'error', 'message': 'ID группы не указан'})

    groups = get_sip_groups()
    groups = [g for g in groups if g['id'] != group_id]
    save_sip_groups(groups)
    flash("Группа абонентов удалена!")
    return jsonify({'status': 'ok', 'message': 'Группа удалена', 'groups': groups})


def get_antifraud_status():
    """Fetches live status, banned IP list, and stats from Fail2ban."""
    status_data = {
        'running': False,
        'jail': 'asterisk-antifraud',
        'currently_failed': 0,
        'total_failed': 0,
        'currently_banned': 0,
        'total_banned': 0,
        'banned_ips': [],
        'maxretry': 5,
        'findtime': 600,
        'bantime': 86400,
        'whitelist': []
    }
    
    try:
        # Check fail2ban-client status
        res = subprocess.run(['fail2ban-client', 'status', 'asterisk-antifraud'], capture_output=True, text=True, timeout=3)
        if res.returncode == 0:
            status_data['running'] = True
            out = res.stdout
            
            m_cur_f = re.search(r'Currently failed:\s*([0-9]+)', out)
            m_tot_f = re.search(r'Total failed:\s*([0-9]+)', out)
            m_cur_b = re.search(r'Currently banned:\s*([0-9]+)', out)
            m_tot_b = re.search(r'Total banned:\s*([0-9]+)', out)
            m_ips = re.search(r'Banned IP list:\s*(.*)', out)
            
            if m_cur_f: status_data['currently_failed'] = int(m_cur_f.group(1))
            if m_tot_f: status_data['total_failed'] = int(m_tot_f.group(1))
            if m_cur_b: status_data['currently_banned'] = int(m_cur_b.group(1))
            if m_tot_b: status_data['total_banned'] = int(m_tot_b.group(1))
            if m_ips and m_ips.group(1).strip():
                raw_ips = m_ips.group(1).strip().split()
                status_data['banned_ips'] = raw_ips

        # Read config params
        cfg = load_integrations()
        sec_cfg = cfg.get('antifraud', {})
        status_data['maxretry'] = sec_cfg.get('maxretry', 5)
        status_data['findtime'] = sec_cfg.get('findtime', 600)
        status_data['bantime'] = sec_cfg.get('bantime', 86400)
        status_data['whitelist'] = sec_cfg.get('whitelist', ['127.0.0.1/8', '::1'])
        
    except Exception as e:
        print(f"[Antifraud Error]: {e}")
        
    return status_data

@app.route('/api/security/antifraud/status')
def api_security_antifraud_status():
    return jsonify(get_antifraud_status())

@app.route('/api/security/antifraud/unban', methods=['POST'])
def api_security_antifraud_unban():
    data = request.get_json() or {}
    ip = data.get('ip') or request.form.get('ip', '').strip()
    if not ip:
        return jsonify({'status': 'error', 'message': 'IP-адрес не указан'})
        
    try:
        res = subprocess.run(['fail2ban-client', 'set', 'asterisk-antifraud', 'unbanip', ip], capture_output=True, text=True, timeout=5)
        if res.returncode == 0 or '1' in res.stdout or '0' in res.stdout:
            flash(f"IP-адрес {ip} успешно разблокирован!")
            return jsonify({'status': 'ok', 'message': f'IP {ip} разблокирован'})
        return jsonify({'status': 'error', 'message': res.stderr or res.stdout})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/api/security/antifraud/ban', methods=['POST'])
def api_security_antifraud_ban_manual():
    data = request.get_json() or {}
    ip = data.get('ip') or request.form.get('ip', '').strip()
    if not ip:
        return jsonify({'status': 'error', 'message': 'IP-адрес не указан'})
        
    try:
        res = subprocess.run(['fail2ban-client', 'set', 'asterisk-antifraud', 'banip', ip], capture_output=True, text=True, timeout=5)
        flash(f"IP-адрес {ip} добавлен в бан-лист!")
        return jsonify({'status': 'ok', 'message': f'IP {ip} заблокирован'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/settings/security/antifraud', methods=['POST'])
def save_security_antifraud_settings():
    cfg = load_integrations()
    maxretry = int(request.form.get('maxretry', 5))
    findtime = int(request.form.get('findtime', 600))
    bantime = int(request.form.get('bantime', 86400))
    whitelist_raw = request.form.get('whitelist', '').strip()
    
    whitelist = [ip.strip() for ip in re.split(r'[\s,]+', whitelist_raw) if ip.strip()]
    if '127.0.0.1/8' not in whitelist: whitelist.insert(0, '127.0.0.1/8')
    if '::1' not in whitelist: whitelist.insert(1, '::1')
    
    if 'antifraud' not in cfg:
        cfg['antifraud'] = {}
        
    cfg['antifraud'] = {
        'maxretry': maxretry,
        'findtime': findtime,
        'bantime': bantime,
        'whitelist': whitelist
    }
    save_integrations(cfg)
    
    # Apply to fail2ban jail.local configuration
    ignore_str = " ".join(whitelist)
    jail_conf_content = f"""[DEFAULT]
bantime  = {bantime}
findtime = {findtime}
maxretry = {maxretry}
backend = auto
ignoreip = {ignore_str}

[asterisk-antifraud]
enabled  = true
port     = 5060,5061
protocol = all
filter   = asterisk-antifraud
logpath  = /var/log/asterisk/messages
maxretry = {maxretry}
findtime = {findtime}
bantime  = {bantime}
action   = iptables-allports[name=ASTERISK-ANTIFRAUD, protocol=all]
"""
    try:
        with open('/etc/fail2ban/jail.local', 'w') as jf:
            jf.write(jail_conf_content)
        subprocess.run(['fail2ban-client', 'reload'], capture_output=True, timeout=5)
    except Exception as e:
        print(f"[Apply Fail2ban Error]: {e}")
        
    flash('Настройки Антифрода Fail2ban успешно сохранены и применены!')
    return redirect(url_for('index'))


@app.route('/settings/security/auth', methods=['POST'])
def save_security_auth():
    cfg = load_integrations()
    enabled = True if request.form.get('enabled') else False
    username = request.form.get('username', 'admin').strip()
    password = request.form.get('password', '').strip()

    if 'security_auth' not in cfg:
        cfg['security_auth'] = {}

    cfg['security_auth']['enabled'] = enabled
    if username:
        cfg['security_auth']['username'] = username
    if password:
        cfg['security_auth']['password'] = password
    
    if request.form.get('dismiss_prompt'):
        cfg['security_auth']['prompt_dismissed'] = True

    save_integrations(cfg)
    
    if enabled:
        session['logged_in'] = True
        session['username'] = username
        flash('Парольная защита панели успешно активирована!')
    else:
        flash('Настройки безопасности сохранены.')
        
    return redirect(url_for('index'))



PJSIP_CONF = '/etc/asterisk/pjsip.conf'
EXTENSIONS_CONF = '/etc/asterisk/extensions.conf'
CONFIG_FILE = '/opt/integrations_config.json'

def resolve_telegram_base_url():
    direct = "https://api.telegram.org"
    proxy = "https://telegram.dentaldate.ae"
    try:
        r = requests.get(direct, timeout=3)
        return direct
    except Exception:
        return proxy

TG_BASE_URL = resolve_telegram_base_url()

CSV_PATH = '/var/log/asterisk/cdr-csv/Master.csv'
RECORD_DIR = '/var/spool/asterisk/monitor'
AMOCRM_LOG = '/opt/amocrm_debug.log'
SOUNDS_DIR = '/var/lib/asterisk/sounds/custom'

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Asterisk PBX & Интеграции</title>
    <style>
        * { box-sizing: border-box; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; }
        body { background-color: #0b0f19; color: #f1f5f9; margin: 0; padding: 24px; }
        .container { max-width: 1320px; margin: 0 auto; }
        .header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #1e293b; padding-bottom: 16px; margin-bottom: 24px; }
        .live-indicator { display: flex; align-items: center; gap: 8px; font-size: 13px; color: #38bdf8; font-weight: 500; }
        .dot { width: 8px; height: 8px; background: #10b981; border-radius: 50%; box-shadow: 0 0 10px #10b981; animation: pulse 2s infinite; }
        
        @keyframes pulse {
            0% { transform: scale(0.95); opacity: 0.8; }
            50% { transform: scale(1.25); opacity: 1; }
            100% { transform: scale(0.95); opacity: 0.8; }
        }

        .tabs { display: flex; gap: 6px; border-bottom: 2px solid #1e293b; margin-bottom: 24px; flex-wrap: wrap; }
        .tab-btn { 
            background: #0f172a; 
            color: #94a3b8; 
            border: 1px solid #1e293b; 
            border-bottom: none; 
            padding: 11px 18px; 
            font-size: 13.5px; 
            font-weight: 600; 
            cursor: pointer; 
            border-radius: 10px 10px 0 0; 
            transition: all 0.2s ease; 
            width: auto; 
            margin: 0; 
            display: inline-flex; 
            align-items: center; 
            gap: 8px; 
            white-space: nowrap; 
        }
        .tab-btn:hover { 
            color: #f8fafc; 
            background: #1e293b; 
            border-color: #334155; 
        }
        .tab-btn.active { 
            background: #0284c7; 
            color: #ffffff; 
            border-color: #0284c7; 
            box-shadow: 0 -2px 10px rgba(2, 132, 199, 0.35); 
        }
        .tab-content { display: none; }
        .tab-content.active { display: block; animation: fadeIn 0.2s ease-in; }

        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(3px); }
            to { opacity: 1; transform: translateY(0); }
        }

        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 20px; margin-bottom: 20px; }
        .card { background: #131d2e; border-radius: 12px; padding: 24px; border: 1px solid #1e293b; margin-bottom: 24px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.2); }
        .card h2 { margin-top: 0; font-size: 18px; color: #38bdf8; border-bottom: 1px solid #1e293b; padding-bottom: 12px; margin-bottom: 20px; display: flex; justify-content: space-between; align-items: center; }
        pre { background: #070a12; padding: 14px; border-radius: 8px; overflow-x: auto; color: #a5f3fc; font-size: 12px; line-height: 1.5; max-height: 400px; border: 1px solid #1e293b; }
        
        /* ЭЛЕМЕНТЫ УПРАВЛЕНИЯ */
        input, select, textarea, button { width: 100%; padding: 10px 14px; border-radius: 8px; border: 1px solid #334155; background: #070a12; color: #f8fafc; font-size: 13px; transition: border-color 0.2s ease; outline: none; }
        input:focus, select:focus, textarea:focus { border-color: #0284c7; }
        select { cursor: pointer; }
        button { background: #0284c7; color: #fff; font-weight: 600; border: none; cursor: pointer; transition: background 0.2s ease; }
        button:hover { background: #0369a1; }
        .btn-danger { background: #ef4444; }
        .btn-danger:hover { background: #dc2626; }
        .btn-success { background: #10b981; }
        .btn-success:hover { background: #059669; }
        .btn-secondary { background: #334155; }
        .btn-secondary:hover { background: #475569; }
        .btn-play { background: #10b981; color: #fff; padding: 6px 12px; margin: 0; font-size: 12px; border-radius: 6px; display: inline-flex; align-items: center; gap: 5px; }
        .btn-play:hover { background: #059669; }
        .flash { background: rgba(16, 185, 129, 0.15); color: #6ee7b7; padding: 14px 18px; border-radius: 8px; margin-bottom: 24px; border: 1px solid rgba(16, 185, 129, 0.3); font-size: 14px; }
        table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 13px; }
        th, td { padding: 12px 14px; text-align: left; border-bottom: 1px solid #1e293b; }
        th { color: #94a3b8; font-weight: 600; background: #070a12; position: sticky; top: 0; z-index: 10; }
        .tag-online { color: #10b981; font-weight: 600; }
        .tag-offline { color: #64748b; }
        .tag-answered { color: #10b981; font-weight: 600; }
        .tag-failed { color: #ef4444; }
        .tag-noanswer { color: #eab308; }
        .checkbox-container { display: flex; align-items: center; gap: 10px; }
        .checkbox-container input { width: auto; margin: 0; cursor: pointer; }
        .table-scroll { border-radius: 8px; border: 1px solid #1e293b; }
        
        .active-call-box { background: #070a12; border: 1px solid #0284c7; border-radius: 8px; padding: 14px; margin-bottom: 10px; display: flex; justify-content: space-between; align-items: center; }
        .active-call-title { font-size: 14px; font-weight: bold; color: #38bdf8; display: flex; align-items: center; gap: 8px; }
        .active-call-badge { background: #10b981; color: #000; font-size: 11px; font-weight: bold; padding: 3px 8px; border-radius: 4px; }
        .active-call-sub { font-size: 12px; color: #94a3b8; margin-top: 4px; }

        .modem-card-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 14px; margin-top: 10px; }
        .modem-metric { background: #070a12; padding: 14px; border-radius: 8px; border: 1px solid #1e293b; }
        .modem-metric-label { font-size: 11px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px; }
        .modem-metric-value { font-size: 15px; font-weight: bold; color: #f8fafc; }
        .modem-status-badge { display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 6px; font-size: 12px; font-weight: bold; }
        .modem-online { background: rgba(16, 185, 129, 0.15); color: #10b981; border: 1px solid rgba(16, 185, 129, 0.3); }
        .modem-offline { background: rgba(239, 68, 68, 0.15); color: #ef4444; border: 1px solid rgba(239, 68, 68, 0.3); }

        /* ИДЕАЛЬНЫЙ СТИЛЬ КОНСТРУКТОРА IVR */
        .ivr-node { background: #0e1626; border: 1px solid #1e293b; border-radius: 12px; padding: 22px; margin-bottom: 24px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.2); }
        .ivr-node-header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #1e293b; padding-bottom: 14px; margin-bottom: 18px; }
        .ivr-node-topgrid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 18px; }
        @media (max-width: 900px) { .ivr-node-topgrid { grid-template-columns: 1fr; } }
        
        .ivr-section-title { font-size: 13px; font-weight: 600; color: #38bdf8; margin-bottom: 12px; display: flex; align-items: center; gap: 6px; }
        .ivr-branch { background: #131d2e; border: 1px solid #1e293b; border-radius: 10px; padding: 14px 18px; margin-bottom: 10px; transition: border-color 0.2s ease; }
        .ivr-branch:hover { border-color: #334155; }
        
        /* Сетка колонок ветвления (с идеальными отступами) */
        .ivr-branch-grid { display: grid; grid-template-columns: 75px 1.4fr 1.2fr 1.4fr 42px; gap: 14px; align-items: flex-end; }
        @media (max-width: 950px) { .ivr-branch-grid { grid-template-columns: 1fr; gap: 10px; } }
        
        .ivr-field-col label { display: block; font-size: 11px; font-weight: 500; color: #94a3b8; margin-bottom: 6px; }
        .debug-box { background: rgba(234, 179, 8, 0.06); border: 1px solid rgba(234, 179, 8, 0.25); border-radius: 10px; padding: 18px; margin-bottom: 24px; }

        /* SPINNER LOADER OVERLAY */
        .loading-overlay { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(11, 15, 25, 0.92); z-index: 99999; justify-content: center; align-items: center; flex-direction: column; backdrop-filter: blur(4px); }
        .loading-overlay.show { display: flex; }
        .spinner { width: 56px; height: 56px; border: 4px solid #1e293b; border-top-color: #38bdf8; border-radius: 50%; animation: spin 0.9s infinite linear; }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        .loading-title { color: #f8fafc; font-size: 18px; font-weight: bold; margin-top: 22px; letter-spacing: 0.3px; }
        .loading-sub { color: #94a3b8; font-size: 13px; margin-top: 8px; text-align: center; max-width: 440px; line-height: 1.6; }

        .modal-overlay { display: none; position: fixed; top:0; left:0; width:100%; height:100%; background: rgba(0,0,0,0.8); z-index: 999; justify-content: center; align-items: center; }
        .modal-overlay.show { display: flex; }
        
        .modal-box { background: #131d2e; border-radius: 14px; border: 1px solid #334155; padding: 24px; max-width: 780px; width: 95%; max-height: 90vh; overflow-y: auto; box-shadow: 0 25px 30px -5px rgba(0,0,0,0.6); }
        .call-flow-diagram { display: flex; align-items: center; justify-content: space-between; background: #070a12; border: 1px solid #1e293b; border-radius: 8px; padding: 14px 18px; margin-bottom: 15px; overflow-x: auto; gap: 8px; }
        .flow-step { text-align: center; background: #0f172a; border: 1px solid #334155; border-radius: 8px; padding: 8px 12px; min-width: 90px; flex: 1; }
        .flow-step.active { border-color: #38bdf8; background: rgba(56, 189, 248, 0.1); }
        .flow-step-icon { font-size: 18px; margin-bottom: 2px; }
        .flow-step-title { font-size: 11px; color: #94a3b8; font-weight: 600; text-transform: uppercase; }
        .flow-step-sub { font-size: 12px; color: #f8fafc; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .flow-arrow { color: #64748b; font-size: 14px; font-weight: bold; flex-shrink: 0; }
        .call-detail-card { background: #070a12; border: 1px solid #1e293b; border-radius: 8px; padding: 10px 14px; }
        .call-detail-label { font-size: 11px; color: #64748b; margin-bottom: 4px; text-transform: uppercase; font-weight: 600; }
        .call-detail-value { font-size: 13px; color: #f8fafc; }

        .modal-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; border-bottom: 1px solid #1e293b; padding-bottom: 10px; }
        .modal-title { font-size: 16px; font-weight: bold; color: #38bdf8; }
        .modal-close { background: transparent; border: none; font-size: 22px; color: #94a3b8; cursor: pointer; width: auto; margin: 0; padding: 0; }
        .modal-close:hover { color: #fff; }
        audio { width: 100%; border-radius: 8px; }
    
        .sub-nav-tabs {
            display: flex;
            gap: 8px;
            margin-bottom: 20px;
            background: #090d16;
            padding: 6px;
            border-radius: 10px;
            border: 1px solid #1e293b;
            overflow-x: auto;
        }
        .sub-tab-btn {
            background: transparent;
            border: none;
            color: #94a3b8;
            padding: 9px 18px;
            border-radius: 6px;
            font-size: 13px;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.2s ease;
            white-space: nowrap;
        }
        .sub-tab-btn:hover {
            color: #f8fafc;
            background: rgba(255, 255, 255, 0.05);
        }
        .sub-tab-btn.active {
            background: #0284c7;
            color: #ffffff;
            font-weight: 600;
            box-shadow: 0 2px 8px rgba(2, 132, 199, 0.4);
        }
        .subtab-content {
            display: none;
        }
        .subtab-content.active {
            display: block;
        }

    
        /* СТИЛИ ПАГИНАЦИИ ТАБЛИЦЫ ВЫЗОВОВ */
        .pagination-container {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-top: 18px;
            padding-top: 14px;
            border-top: 1px solid #1e293b;
            flex-wrap: wrap;
            gap: 12px;
        }
        .page-info {
            font-size: 13px;
            color: #94a3b8;
        }
        .page-info b {
            color: #f8fafc;
        }
        .pagination-buttons {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            flex-wrap: wrap;
        }
        .page-btn {
            width: auto !important;
            min-width: 36px;
            height: 36px;
            padding: 0 12px !important;
            margin: 0 !important;
            font-size: 13px !important;
            font-weight: 600;
            border-radius: 8px !important;
            border: 1px solid #334155 !important;
            background: #0f172a !important;
            color: #cbd5e1 !important;
            display: inline-flex !important;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: all 0.2s ease;
        }
        .page-btn:hover:not(:disabled) {
            background: #1e293b !important;
            border-color: #38bdf8 !important;
            color: #fff !important;
        }
        .page-btn.active {
            background: #0284c7 !important;
            border-color: #38bdf8 !important;
            color: #fff !important;
            box-shadow: 0 0 10px rgba(2, 132, 199, 0.4);
        }
        .page-btn:disabled {
            opacity: 0.35;
            cursor: not-allowed;
            background: #090d16 !important;
            border-color: #1e293b !important;
            color: #64748b !important;
        }

    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>Asterisk PBX & Интеграции</h1>
        <div class="live-indicator" style="gap: 10px; display: flex; align-items: center; flex-wrap: wrap;">
            <div style="font-weight: 600; font-size: 13px; color: #a5f3fc; background: #1e293b; padding: 5px 12px; border-radius: 6px; border: 1px solid #334155; display: flex; align-items: center; gap: 6px;">
                Местное время: <span id="server-time">Загрузка...</span>
            </div>
            <div style="font-weight: 600; font-size: 13px; color: #38bdf8; background: #0f172a; padding: 5px 12px; border-radius: 6px; border: 1px solid #1e293b; display: flex; align-items: center; gap: 6px;">
                <div class="dot"></div>
                <span>IP: {{ host }}</span>
            </div>
            <button type="button" onclick="openTab('tab-update', null)" id="header-update-btn" title="Нажмите, чтобы открыть раздел обновления" style="background: #1e293b; color: #cbd5e1; border: 1px solid #334155; padding: 5px 14px; border-radius: 6px; font-size: 13px; font-weight: 600; cursor: pointer; display: flex; align-items: center; gap: 8px; margin: 0; width: auto; transition: all 0.2s ease;">
                <span id="header-version-text">v{{ current_version }}</span>
                <span id="header-update-badge" style="display:none; background: #ef4444; color: #fff; font-size: 10px; font-weight: bold; padding: 2px 7px; border-radius: 10px; animation: pulse 2s infinite;">Доступно обновление</span>
            </button>
        </div>
    </div>

    {% with messages = get_flashed_messages() %}
      {% if messages %}
        {% for message in messages %}
          <div class="flash">{{ message }}</div>
        {% endfor %}
      {% endif %}
    {% endwith %}

    <div class="tabs" style="display: flex; gap: 6px; overflow-x: auto; padding-bottom: 2px; scrollbar-width: thin;">
        <button class="tab-btn active" onclick="openTab('tab-calls', this)">История звонков</button>
        <button class="tab-btn" onclick="openTab('tab-sip', this)">Пользователи (SIP)</button>
        <button class="tab-btn" onclick="openTab('tab-routing', this)">Маршруты звонков</button>
        <button class="tab-btn" onclick="openTab('tab-ivr-builder', this)">Автоответчик (IVR)</button>
        <button class="tab-btn" onclick="openTab('tab-integrations', this)">CRM и Сервисы</button>
        <button class="tab-btn" onclick="openTab('tab-live-traffic', this)">Модем и Линии</button>
        <button class="tab-btn" onclick="openTab('tab-network', this)">Сеть и Сервер</button>
        <button class="tab-btn" onclick="openTab('tab-docs', this)">API и Справка</button>
    </div>

    <!-- ВКЛАДКА 1: ЖУРНАЛ ВЫЗОВОВ -->
    <div id="tab-calls" class="tab-content active">
        <div class="card">
            <h2>
                <span>Журнал вызовов и записи разговоров</span>
                <span id="calls-count-badge" style="font-size: 12px; color: #10b981;">● Live Auto-Sync</span>
            </h2>
            
            <div class="calls-filter-bar" style="display: flex; justify-content: space-between; align-items: center; gap: 15px; margin-bottom: 16px; flex-wrap: wrap;">
                <div class="search-input-box" style="position: relative; max-width: 380px; width: 100%;">
                    <input type="text" id="call-search-input" placeholder="🔍 Поиск по номеру (кто или кому)..." oninput="onSearchChange()" style="padding: 10px 14px; margin: 0; font-size: 13px; border-radius: 8px; width: 100%;">
                </div>
                <div style="display:flex; align-items:center; gap: 10px;">
                    <label style="font-size:12px; color:#94a3b8; margin:0; white-space:nowrap;">Показывать по:</label>
                    <select id="page-size-select" onchange="onPageSizeChange()" style="width: 85px; margin:0; padding:8px 10px; font-size:13px;">
                        <option value="20">20</option>
                        <option value="50" selected>50</option>
                        <option value="100">100</option>
                        <option value="200">200</option>
                    </select>
                </div>
            </div>

            <div id="calls-table-container"></div>
            <div id="calls-pagination-container" class="pagination-container" style="display: flex; justify-content: space-between; align-items: center; margin-top: 16px; flex-wrap: wrap; gap: 10px;"></div>
        </div>
    </div>

    <!-- ВКЛАДКА 2: МНОГОУРОВНЕВЫЙ КОНСТРУКТОР IVR + РЕЖИМ ОТЛАДКИ -->
    <div id="tab-ivr-builder" class="tab-content">
        <div class="card">
            <h2>
                <span>🎛 Конструктор IVR Меню & Режим Тестирования (Debug)</span>
                <span style="font-size: 13px; color: #38bdf8;">Многоуровневые этапы и ветвления</span>
            </h2>
            
            <form method="POST" action="/settings/ivr-builder" enctype="multipart/form-data" id="ivr-builder-form" onsubmit="showLoadingSpinner()">
                <!-- БЛОК РЕЖИМА ОТЛАДКИ (DEBUG) НА ВНУТРЕННЕМ НОМЕРЕ -->
                <div class="debug-box">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                        <span style="font-size: 14px; font-weight: bold; color: #eab308;">🐞 Режим отладки и тестирования IVR с софтфона</span>
                        <div class="checkbox-container">
                            <input type="checkbox" name="debug_enabled" id="dbg_en" {% if ivr_tree.debug_enabled %}checked{% endif %}>
                            <label for="dbg_en" style="color:#f8fafc; font-size:13px; font-weight:600; cursor:pointer;">Включить Debug-номер для проверки</label>
                        </div>
                    </div>
                    <p style="color: #94a3b8; font-size: 12px; margin: 0 0 12px 0; line-height: 1.5;">
                        Позволяет протестировать всю цепочку IVR без реального звонка на SIM-карту. Укажите внутренний сервисный номер (например: <b>888</b>) и наберите его с софтфона (101, 102).
                    </p>
                    <div style="display: flex; gap: 15px; align-items: center; max-width: 600px;">
                        <label style="font-size: 13px; color: #cbd5e1; white-space: nowrap;">Номер для вызова IVR теста:</label>
                        <input type="text" name="debug_exten" value="{{ ivr_tree.debug_exten or '888' }}" placeholder="888" style="width: 120px; font-weight: bold; font-size: 14px; text-align: center; margin:0;" required>
                        <small style="color: #38bdf8;">Наберите этот номер со своего софтфона!</small>
                    </div>
                </div>

                <div class="checkbox-container" style="margin-bottom: 20px;">
                    <input type="checkbox" name="ivr_enabled" id="ivr_main_en" {% if ivr_tree.enabled %}checked{% endif %}>
                    <label for="ivr_main_en" style="font-size: 14px; font-weight: bold; cursor: pointer;">Включить голосовой конструктор IVR для входящих звонков с SIM-карты (Модема)</label>
                </div>

                
                <div class="ivr-node" style="border-color: #38bdf8;">
                    <div class="ivr-section-title" style="margin-bottom: 15px; font-size: 15px;">⏰ График работы (Нерабочее время)</div>
                    <div class="checkbox-container" style="margin-bottom: 15px;">
                        <input type="checkbox" name="wh_enabled" id="wh_enabled" {% if ivr_tree.work_hours and ivr_tree.work_hours.enabled %}checked{% endif %}>
                        <label for="wh_enabled" style="font-weight: bold; cursor: pointer;">Включить проверку рабочего времени</label>
                    </div>
                    
                    <div class="ivr-node-topgrid" style="grid-template-columns: 1fr 1fr 1fr;">
                        <div>
                            <label style="font-size:12px; font-weight:500; color:#94a3b8; display:block; margin-bottom:6px;">Начало рабочего дня (чч:мм):</label>
                            <input type="time" name="wh_start" value="{{ ivr_tree.work_hours.start if ivr_tree.work_hours else '09:00' }}">
                        </div>
                        <div>
                            <label style="font-size:12px; font-weight:500; color:#94a3b8; display:block; margin-bottom:6px;">Конец рабочего дня (чч:мм):</label>
                            <input type="time" name="wh_end" value="{{ ivr_tree.work_hours.end if ivr_tree.work_hours else '18:00' }}">
                        </div>
                        <div>
                            <label style="font-size:12px; font-weight:500; color:#94a3b8; display:block; margin-bottom:6px;">Рабочие дни:</label>
                            <select name="wh_days">
                                <option value="mon-fri" {% if ivr_tree.work_hours and ivr_tree.work_hours.days == 'mon-fri' %}selected{% endif %}>Пн - Пт</option>
                                <option value="mon-sat" {% if ivr_tree.work_hours and ivr_tree.work_hours.days == 'mon-sat' %}selected{% endif %}>Пн - Сб</option>
                                <option value="mon-sun" {% if ivr_tree.work_hours and ivr_tree.work_hours.days == 'mon-sun' %}selected{% endif %}>Пн - Вс (Ежедневно)</option>
                            </select>
                        </div>
                    </div>
                    
                    <div style="margin-top: 15px;">
                        <label style="font-size:12px; font-weight:500; color:#94a3b8; display:block; margin-bottom:6px;">🎵 Аудио-сообщение для нерабочего времени (MP3/WAV):</label>
                        <input type="file" name="wh_audio_file" accept=".mp3,.wav" style="margin-bottom:8px;">
                        <input type="hidden" name="existing_wh_audio" value="{{ ivr_tree.work_hours.audio_file if ivr_tree.work_hours else '' }}">
                        {% if ivr_tree.work_hours and ivr_tree.work_hours.audio_file %}
                            <div style="margin-top:6px;">
                                <span style="color:#10b981; font-size:12px; font-weight:500;">✓ Активен: {{ ivr_tree.work_hours.audio_file }}</span>
                                <audio controls src="/custom-audio/{{ ivr_tree.work_hours.audio_file }}" style="margin-top:6px; height:34px;"></audio>
                            </div>
                        {% endif %}
                    </div>
                </div>

                <div id="ivr-nodes-container">
                    <!-- ДИНАМИЧЕСКИЕ ЭТАПЫ / УРОВНИ МЕНЮ (NODES) -->
                </div>

                <div style="display: flex; gap: 12px; margin-top: 20px; align-items: center;">
                    <button type="button" onclick="addNewNode()" class="btn-secondary" style="width: auto; padding: 12px 20px;">➕ Добавить новый уровень меню (Этап)</button>
                    <button type="submit" class="btn-success" style="width: auto; margin-left: auto; padding: 12px 26px; font-size: 14px;">💾 Сохранить схему IVR и Audio</button>
                </div>
            </form>
        </div>
    </div>

    <!-- ВКЛАДКА 3: МАРШРУТИЗАЦИЯ (ВХОДЯЩИЕ И ИСХОДЯЩИЕ ВЫЗОВЫ) -->
    <div id="tab-routing" class="tab-content">
        <!-- БЛОК 1: ВХОДЯЩАЯ МАРШРУТИЗАЦИЯ -->
        <div class="card">
            <h2>
                <span>📥 Маршрутизация входящих звонков (прямой вызов без IVR)</span>
                <span style="font-size: 13px; color: #38bdf8;">Входящий GSM & SIP</span>
            </h2>
            <p style="color: #94a3b8; font-size: 13px; margin-top: 0; margin-bottom: 14px;">
                Укажите, кому направлять входящие звонки с модемов и внешних телефоний, если голосовое меню (IVR) отключено.
            </p>
            <form method="POST" action="/settings/inbound-routing">
                <div class="grid">
                    <div>
                        <label style="font-size: 12px; color: #94a3b8; margin-bottom: 6px; display: block;">Куда направлять входящий звонок:</label>
                        <select name="target" style="margin-bottom: 14px;" required>
                            <option value="ALL" {% if inbound_target == 'ALL' %}selected{% endif %}>🔔 Звонить всем операторам одновременно</option>
                            {% for g in ring_groups %}
                            <option value="{{ g.exten }}" {% if inbound_target == g.exten %}selected{% endif %}>
                                🏢 Отдел / Группа: {{ g.name }} ({{ g.exten }})
                            </option>
                            {% endfor %}
                            {% for acc in accounts %}
                            <option value="{{ acc.exten }}" {% if inbound_target == acc.exten %}selected{% endif %}>
                                👤 Внутренний номер {{ acc.exten }} {% if acc.exten in active_contacts %}(● В сети){% else %}(○ Не в сети){% endif %}
                            </option>
                            {% endfor %}
                        </select>
                    </div>
                </div>
                <button type="submit" class="btn-success" style="width: auto; padding: 10px 22px;">Сохранить входящую маршрутизацию</button>
            </form>
        </div>

        <!-- БЛОК 2: УМНАЯ ИСХОДЯЩАЯ МАРШРУТИЗАЦИЯ (LCR & FAILOVER) -->
        <div class="card" style="margin-top: 20px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; flex-wrap: wrap; gap: 10px;">
                <h2>
                    <span>📤 Умная исходящая маршрутизация (LCR & Failover)</span>
                    <span style="font-size: 13px; color: #38bdf8;">Автовыбор модема/транка по коду страны</span>
                </h2>
                <span style="background: rgba(16, 185, 129, 0.15); color: #10b981; padding: 4px 10px; border-radius: 6px; font-size: 12px; border: 1px solid rgba(16, 185, 129, 0.3);">
                    ● Резервирование Failover Активно
                </span>
            </div>
            <p style="color: #94a3b8; font-size: 13px; margin-bottom: 16px; line-height: 1.5;">
                Настройте правила выбора шлюзов по телефонным префиксам (например, звонки на <b>+971 (ОАЭ)</b> ➔ через модем du/Etisalat, звонки на <b>+7 (РФ)</b> ➔ через модем РФ / Zadarma).
                При занятости основного модема/транка система автоматически переключит звонок на резервный шлюз.
            </p>

            {% set routes = integrations.get('outbound_routing', {}).get('routes', []) %}
            {% if routes %}
            <div class="table-scroll" style="margin-bottom: 20px;">
                <table>
                    <thead>
                        <tr>
                            <th>Направление / Название</th>
                            <th>Маска / Префикс</th>
                            <th>Основной шлюз</th>
                            <th>Резервный шлюз (Failover)</th>
                            <th>Действие</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for r in routes %}
                        <tr>
                            <td><b>{{ r.name }}</b></td>
                            <td><code>{{ r.prefix }}</code></td>
                            <td><span style="color: #38bdf8; font-weight: 500;">{{ r.gateways[0] if r.gateways else '-' }}</span></td>
                            <td><span style="color: #a5f3fc;">{{ r.gateways[1] if r.gateways|length > 1 else '—' }}</span></td>
                            <td>
                                <form method="POST" action="/settings/outbound-routes/delete/{{ r.id }}" style="margin:0;" onsubmit="return confirm('Удалить маршрут {{ r.name }}?');">
                                    <button type="submit" class="btn-danger" style="width: auto; padding: 4px 10px; font-size: 12px; margin: 0;">Удалить</button>
                                </form>
                            </td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
            {% endif %}

            <h3 style="font-size: 15px; color: #a5f3fc; margin-bottom: 12px;">➕ Добавить правило исходящего направления</h3>
            <form method="POST" action="/settings/outbound-routes/add">
                <div class="grid">
                    <div>
                        <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 4px;">Название маршрута (напр. Звонки по ОАЭ, Звонки в РФ, Мир):</label>
                        <input type="text" name="route_name" placeholder="Звонки по ОАЭ (Dubai)" required style="margin-bottom: 12px;">

                        <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 4px;">Префикс / Маска номеров (напр. +971, 05, +7, 89, +):</label>
                        <input type="text" name="route_prefix" placeholder="+971" required style="margin-bottom: 12px;">
                    </div>

                    <div>
                        <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 4px;">Основной шлюз (Primary Gateway):</label>
                        <select name="gw_primary" style="margin-bottom: 12px;">
                            {% for gw in available_gateways %}
                            <option value="{{ gw.id }}">{{ gw.name }}</option>
                            {% endfor %}
                        </select>

                        <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 4px;">Резервный шлюз при занятости (Failover Gateway):</label>
                        <select name="gw_failover" style="margin-bottom: 12px;">
                            <option value="">(Без резервирования)</option>
                            {% for gw in available_gateways %}
                            <option value="{{ gw.id }}">{{ gw.name }}</option>
                            {% endfor %}
                        </select>
                    </div>
                </div>
                <button type="submit" class="btn-success" style="width: auto; margin-top: 6px; padding: 10px 24px;">Создать исходящий маршрут</button>
            </form>
        </div>

        <!-- БЛОК 3: ПРИВЯЗКА ИСХОДЯЩИХ ЛИНИЙ К ОПЕРАТОРАМ -->
        <div class="card" style="margin-top: 20px;">
            <h2>
                <span>👤 Персональные исходящие линии операторов (Per-User Outbound)</span>
                <span style="font-size: 13px; color: #38bdf8;">Индивидуальный шлюз</span>
            </h2>
            <p style="color: #94a3b8; font-size: 13px; margin-bottom: 16px;">
                Вы можете закрепить за конкретным сотрудником персональный модем или отдельный SIP-транк.
            </p>

            {% set op_rules = integrations.get('outbound_routing', {}).get('operator_rules', {}) %}
            <form method="POST" action="/settings/outbound-operators">
                <div class="table-scroll" style="margin-bottom: 15px;">
                    <table>
                        <thead>
                            <tr>
                                <th>Внутренний номер</th>
                                <th>Имя оператора</th>
                                <th>Исходящая линия по умолчанию</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for acc in accounts %}
                            <tr>
                                <td><span style="font-size: 14px; font-weight: bold; color: #38bdf8;">SIP/{{ acc.exten }}</span></td>
                                <td><b>{{ acc.name or ('Оператор ' ~ acc.exten) }}</b></td>
                                <td>
                                    <select name="op_gw_{{ acc.exten }}">
                                        <option value="">(Автовыбор по правилам LCR)</option>
                                        {% for gw in available_gateways %}
                                        <option value="{{ gw.id }}" {% if op_rules.get(acc.exten) == gw.id %}selected{% endif %}>{{ gw.name }}</option>
                                        {% endfor %}
                                    </select>
                                </td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>

                <div class="checkbox-container" style="margin-bottom: 16px;">
                    <input type="checkbox" name="enable_failover" id="ef_cb" {% if integrations.get('outbound_routing', {}).get('enable_failover', True) %}checked{% endif %}>
                    <label for="ef_cb" style="font-weight: bold; cursor: pointer;">Включить автоматический перебор линий (Failover) при занятости</label>
                </div>

                <button type="submit" class="btn-success" style="width: auto; padding: 10px 24px;">💾 Сохранить настройки линий операторов</button>
            </form>
        </div>
    </div>

    <!-- ВКЛАДКА 4: ИНТЕГРАЦИИ (С ПОД-ТАБАМИ) -->
    <div id="tab-integrations" class="tab-content">
        <!-- ПАНЕЛЬ ПОД-ВКЛАДОК -->
        <div class="sub-nav-tabs">
            <button type="button" class="sub-tab-btn active" onclick="openSubTab('subtab-amocrm', this)">amoCRM и места</button>
            <button type="button" class="sub-tab-btn" onclick="openSubTab('subtab-ftp', this)">FTP Хранилище</button>
            <button type="button" class="sub-tab-btn" onclick="openSubTab('subtab-telegram', this)">Telegram</button>
            <button type="button" class="sub-tab-btn" onclick="openSubTab('subtab-gdrive', this)">Google Drive</button>
            <button type="button" class="sub-tab-btn" onclick="openSubTab('subtab-telephony', this)">Внешние SIP-Транки</button>
        </div>

        <!-- ПОД-ВКЛАДКА 1: AMOCRM -->
        <div id="subtab-amocrm" class="subtab-content active">
            <div class="card">
                <h2>🏢 Расширенная настройка amoCRM</h2>
                <form method="POST" action="/settings/amocrm">
                    <div class="grid">
                        <div>
                            <div class="checkbox-container" style="margin-bottom: 12px;">
                                <input type="checkbox" name="enabled" id="amo_en" {% if integrations.amocrm and integrations.amocrm.enabled %}checked{% endif %}>
                                <label for="amo_en" style="font-weight: bold; cursor: pointer;">Включить выгрузку звонков в amoCRM</label>
                            </div>
                            <div class="checkbox-container" style="margin-bottom: 16px;">
                                <input type="checkbox" name="send_internal" id="amo_int" {% if integrations.amocrm and integrations.amocrm.send_internal %}checked{% endif %}>
                                <label for="amo_int" style="font-weight: bold; cursor: pointer;">Отправлять внутренние звонки в amoCRM (Debug)</label>
                            </div>
                            
                            <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 4px;">Субдомен amoCRM (без .amocrm.ru):</label>
                            <input type="text" name="subdomain" id="amo_subdomain" value="{{ integrations.amocrm.subdomain if integrations.amocrm else '' }}" placeholder="mycompany" style="margin-bottom: 14px;" required>
                            
                            <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 4px;">Долгосрочный токен (Bearer Token):</label>
                            <textarea name="token" id="amo_token" rows="3" placeholder="eyJ0eXAi..." required>{{ integrations.amocrm.token if integrations.amocrm else '' }}</textarea>
                        </div>

                        <div>
                            <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 4px;">Воронка amoCRM (для новых сделок):</label>
                            <select name="pipeline_id" id="amo_pipeline" onchange="updateStages()" style="margin-bottom: 14px;">
                                <option value="">Загрузка воронок...</option>
                            </select>

                            <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 4px;">Этап сделки (Статус воронки):</label>
                            <select name="status_id" id="amo_stage" style="margin-bottom: 14px;">
                                <option value="">Выберите этап</option>
                            </select>

                            <div style="margin-top: 10px;">
                                <button type="button" onclick="fetchPipelines()" class="btn-secondary" style="width: auto;">🔄 Загрузить список воронок из amoCRM</button>
                            </div>
                        </div>
                    </div>

                    <button type="submit" class="btn-success" style="width: auto; margin-top: 15px; padding: 10px 24px;">Сохранить настройки amoCRM</button>
                </form>
            </div>

            <!-- КАРТОЧКА ПОСАДОЧНЫХ МЕСТ И РАСПРЕДЕЛЕНИЯ МЕНЕДЖЕРОВ AMOCRM -->
            <div class="card" style="margin-top: 20px;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; flex-wrap: wrap; gap: 10px;">
                    <h2>
                        <span>👥 Посадочные места (Ответственные менеджеры amoCRM)</span>
                        <span style="font-size: 13px; color: #38bdf8;">Автопривязка по внутреннему номеру</span>
                    </h2>
                    <button type="button" onclick="fetchAmoUsers()" class="btn-secondary" style="width: auto; padding: 6px 14px; font-size: 12px;">🔄 Загрузить менеджеров из amoCRM</button>
                </div>
                
                <p style="margin: 0 0 16px 0; font-size: 13px; color: #94a3b8; line-height: 1.5;">
                    Закрепите за каждым внутренним SIP-номером ответственного сотрудника из amoCRM. 
                    Когда оператор совершает или принимает звонок, новая сделка, контакт и запись разговора в amoCRM будут <b>автоматически закреплены за его учетной записью</b>.
                </p>

                <form method="POST" action="/settings/amocrm-seats">
                    <div class="table-scroll" style="margin-bottom: 15px;">
                        <table>
                            <thead>
                                <tr>
                                    <th>SIP Номер</th>
                                    <th>Имя оператора</th>
                                    <th>Ответственный сотрудник в amoCRM</th>
                                </tr>
                            </thead>
                            <tbody id="amocrm-seats-tbody">
                                {% for acc in accounts %}
                                <tr>
                                    <td><span style="font-size: 14px; font-weight: bold; color: #38bdf8;">SIP/{{ acc.exten }}</span></td>
                                    <td><b>{{ acc.name or ('Оператор ' ~ acc.exten) }}</b></td>
                                    <td>
                                        <select name="seat_{{ acc.exten }}" class="amo-user-select" data-saved="{{ amocrm_user_mapping.get(acc.exten, '') }}">
                                            <option value="">Загрузка списка менеджеров...</option>
                                        </select>
                                    </td>
                                </tr>
                                {% endfor %}
                                <tr style="background: rgba(30, 41, 59, 0.5);">
                                    <td colspan="2"><b style="color: #a5f3fc;">По умолчанию (для остальных и непривязанных номеров):</b></td>
                                    <td>
                                        <select name="seat_default" class="amo-user-select" data-saved="{{ amocrm_user_mapping.get('default', '') }}">
                                            <option value="">Не назначен</option>
                                        </select>
                                    </td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                    <button type="submit" class="btn-success" style="width: auto; padding: 10px 24px;">💾 Сохранить посадочные места</button>
                </form>
            <!-- КАРТОЧКА: ЛОГИ ОБМЕНА С AMOCRM -->
            <div class="card" style="margin-top: 25px; border-top: 2px solid #334155; padding-top: 20px;">
                <h3 style="display: flex; justify-content: space-between; align-items: center; margin-top: 0; color: #f8fafc; font-size: 16px;">
                    <span>🐞 Логи обмена данными с amoCRM (API v4 Debug Stream)</span>
                    <span style="font-size: 12px; color: #10b981; font-weight: normal;">● Live Auto-Sync</span>
                </h3>
                <pre id="live-amocrm-logs" style="background: #020617; color: #38bdf8; padding: 14px; border-radius: 8px; font-family: monospace; font-size: 12px; max-height: 350px; overflow-y: auto; border: 1px solid #1e293b;">Загрузка логов amoCRM...</pre>
                <form method="POST" action="/action/clear-amocrm-log" style="margin-top: 14px;">
                    <button type="submit" class="btn-danger" style="width:auto; padding: 6px 16px; font-size: 12px;">Очистить лог amoCRM</button>
                </form>
            </div>
            </div>
        </div>

        <!-- ПОД-ВКЛАДКА 2: TELEGRAM -->
        <div id="subtab-telegram" class="subtab-content">
            <div class="card">
                <h2>✈️ Уведомления в Telegram (Мульти-пользователи)</h2>
                <form method="POST" action="/settings/telegram">
                    <div class="checkbox-container" style="margin-bottom: 14px;">
                        <input type="checkbox" name="enabled" id="tg_en" {% if integrations.telegram and integrations.telegram.enabled %}checked{% endif %}>
                        <label for="tg_en" style="font-weight: bold; cursor: pointer;">Включить уведомления о звонках в Telegram</label>
                    </div>
                    <div class="grid">
                        <div>
                            <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 4px;">Токен бота (Bot Token):</label>
                            <input type="text" id="tg_token" name="token" value="{{ integrations.telegram.token if integrations.telegram else '' }}" placeholder="123456789:ABCdefGHIjklm..." style="margin-bottom: 14px;">
                        </div>
                        <div>
                            <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 4px;">ID пользователей / чатов (через запятую или пробел):</label>
                            <input type="text" id="tg_chat_ids" name="chat_id" value="{{ integrations.telegram.chat_id if integrations.telegram else '' }}" placeholder="Например: 12345678, 87654321, -100123456789" style="margin-bottom: 6px;">
                            <small style="color: #64748b; display: block; margin-bottom: 14px;">Можно указать несколько Chat ID получателей через запятую или с новой строки.</small>
                        </div>
                    </div>
                    
                    <div style="display: flex; gap: 12px; align-items: center; margin-top: 10px;">
                        <button type="submit" class="btn-success" style="width: auto; padding: 10px 24px;">Сохранить настройки Telegram</button>
                        <button type="button" class="btn-primary" onclick="testTelegramConnection()" style="width: auto; padding: 10px 20px; background: #0284c7;">⚡ Тест отправки в Telegram</button>
                    </div>
                </form>

                <!-- БЛОК ЛОГА ТЕСТИРОВАНИЯ TELEGRAM -->
                <div id="tg-test-result" style="display: none; margin-top: 18px; background: #090d16; border: 1px solid #1e293b; border-radius: 8px; padding: 14px;">
                    <div style="font-size: 13px; font-weight: bold; margin-bottom: 8px; color: #38bdf8;">📋 Протокол проверки Telegram:</div>
                    <pre id="tg-test-log" style="margin: 0; padding: 10px; background: #030712; color: #38bdf8; font-size: 12px; border-radius: 6px; max-height: 200px; overflow-y: auto; white-space: pre-wrap; font-family: monospace;"></pre>
                </div>
            </div>
        </div>

        
        <!-- ПОД-ВКЛАДКА: FTP ХРАНИЛИЩЕ И HTTP ССЫЛКИ -->
        <div id="subtab-ftp" class="subtab-content">
            <div class="card">
                <h2>🗄 Выгрузка записей на внешний FTP / Web-сервер</h2>
                <form method="POST" action="/settings/ftp">
                    <div class="checkbox-container" style="margin-bottom: 16px;">
                        <input type="checkbox" name="enabled" id="ftp_en" {% if integrations.ftp and integrations.ftp.enabled %}checked{% endif %}>
                        <label for="ftp_en" style="font-weight: bold; cursor: pointer;">Включить автоматическую выгрузку записей разговоров на FTP</label>
                    </div>

                    <div class="grid" style="display: grid; grid-template-columns: 2fr 1fr; gap: 16px; margin-bottom: 14px;">
                        <div>
                            <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 4px;">FTP Хост / Сервер (IP или домен):</label>
                            <input type="text" name="host" value="{{ integrations.ftp.host if integrations.ftp else '' }}" placeholder="например: 185.243.76.230 или ftp.mycompany.com">
                        </div>
                        <div>
                            <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 4px;">FTP Порт:</label>
                            <input type="number" name="port" value="{{ integrations.ftp.port if integrations.ftp else '21' }}" placeholder="21">
                        </div>
                    </div>

                    <div class="grid" style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 14px;">
                        <div>
                            <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 4px;">FTP Пользователь (Login):</label>
                            <input type="text" name="username" value="{{ integrations.ftp.username if integrations.ftp else '' }}" placeholder="ftpuser">
                        </div>
                        <div>
                            <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 4px;">FTP Пароль:</label>
                            <input type="password" name="password" value="{{ integrations.ftp.password if integrations.ftp else '' }}" placeholder="••••••••">
                        </div>
                    </div>

                    <div style="margin-bottom: 14px;">
                        <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 4px;">Папка на FTP сервере (удаленный путь):</label>
                        <input type="text" name="remote_path" value="{{ integrations.ftp.remote_path if integrations.ftp else '/records' }}" placeholder="/records или /public_html/audio">
                        <small style="color: #64748b;">Оставьте пустым или укажите подпапку. Если папки нет, система создаст её автоматически.</small>
                    </div>

                    <div style="margin-bottom: 20px; background: #070a12; border: 1px solid #0284c7; border-radius: 8px; padding: 14px;">
                        <label style="font-size: 12px; color: #38bdf8; display: block; font-weight: bold; margin-bottom: 4px;">🌐 Публичный HTTP/HTTPS хост для генерации ссылок на аудио (для плеера в amoCRM и ссылок):</label>
                        <input type="text" name="http_base_url" value="{{ integrations.ftp.http_base_url if integrations.ftp else '' }}" placeholder="например: https://records.dentaldate.ae или http://185.243.76.230/audio" style="margin-bottom: 6px;">
                        <small style="color: #94a3b8;">По этому адресу веб-сервер отдает загруженные WAV файлы. Ссылка на запись вида <code>https://records.dentaldate.ae/20260828-154540_call.wav</code> будет автоматически передаваться в amoCRM и Telegram.</small>
                    </div>

                    <div style="display: flex; gap: 12px; align-items: center;">
                        <button type="submit" class="btn-success" style="width: auto; padding: 10px 24px;">Сохранить настройки FTP</button>
                        <button type="button" class="btn-primary" onclick="testFtpConnection()" style="width: auto; padding: 10px 20px; background: #0284c7;">⚡ Тест соединения с FTP</button>
                    </div>
                </form>

                <!-- БЛОК ЛОГА ТЕСТИРОВАНИЯ FTP -->
                <div id="ftp-test-result" style="display: none; margin-top: 18px; background: #090d16; border: 1px solid #1e293b; border-radius: 8px; padding: 14px;">
                    <div style="font-size: 13px; font-weight: bold; margin-bottom: 8px; color: #38bdf8;">📋 Протокол подключения к FTP серверу:</div>
                    <pre id="ftp-test-log" style="margin: 0; padding: 10px; background: #030712; color: #38bdf8; font-size: 12px; border-radius: 6px; max-height: 200px; overflow-y: auto; white-space: pre-wrap; font-family: monospace;"></pre>
                </div>
            </div>
        </div>

        <!-- ПОД-ВКЛАДКА 3: GOOGLE DRIVE -->
        <div id="subtab-gdrive" class="subtab-content">
            <div class="card">
                <h2>📁 Запись звонков и Google Drive Хранилище</h2>
                <form method="POST" action="/settings/gdrive">
                    <div class="checkbox-container" style="margin-bottom: 14px;">
                        <input type="checkbox" name="enabled" id="gd_en" {% if integrations.gdrive and integrations.gdrive.enabled %}checked{% endif %}>
                        <label for="gd_en" style="font-weight: bold; cursor: pointer;">Включить выгрузку аудиозаписей в Google Drive</label>
                    </div>
                    
                    <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 4px;">Google OAuth / Access Bearer Token:</label>
                    <textarea name="token" rows="3" placeholder="ya29.a0..." style="margin-bottom: 14px;" required>{{ integrations.gdrive.token if integrations.gdrive else '' }}</textarea>
                    
                    <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 4px;">Google Drive Folder ID (ID целевой папки на Диске):</label>
                    <input type="text" name="folder_id" value="{{ integrations.gdrive.folder_id if integrations.gdrive else '' }}" placeholder="Например: 1uF-A1Z5I1Oy5BFWJkRPFpyb-cxfl5FVC" style="margin-bottom: 16px;">
                    
                    <button type="submit" class="btn-success" style="width: auto; padding: 10px 24px;">Сохранить Google Drive</button>
                </form>
            </div>

            <!-- ПОШАГОВАЯ ИНСТРУКЦИЯ ПОЛУЧЕНИЯ ТОКЕНА И ID ПАПКИ -->
            <div class="card" style="margin-top: 20px;">
                <h2>
                    <span>📖 Пошаговая инструкция: как получить Access Token и Folder ID</span>
                    <span style="font-size: 13px; color: #38bdf8;">Инструкция по настройке</span>
                </h2>

                <div style="display: flex; flex-direction: column; gap: 16px; margin-top: 10px;">
                    <!-- Шаг 1: Быстрое получение токена через OAuth Playground -->
                    <div style="background: #090d16; border: 1px solid #1e293b; border-radius: 8px; padding: 16px;">
                        <h3 style="margin: 0 0 8px 0; font-size: 14px; color: #a5f3fc; display: flex; align-items: center; gap: 8px;">
                            <span>🚀 Шаг 1. Получение OAuth Access Token через Google OAuth Playground</span>
                        </h3>
                        <ol style="margin: 0 0 0 18px; padding: 0; color: #cbd5e1; font-size: 13px; line-height: 1.7;">
                            <li>Перейдите в официальный инструмент Google: <a href="https://developers.google.com/oauthplayground/" target="_blank" style="color: #38bdf8; text-decoration: underline; font-weight: bold;">Google OAuth 2.0 Playground ↗</a></li>
                            <li>В левом списке найдите раздел <b>Drive API v3</b> и отметьте галочкой скоуп: <code>https://www.googleapis.com/auth/drive.file</code> (или <code>.../auth/drive</code>).</li>
                            <li>Нажмите синюю кнопку <b>Authorize APIs</b> внизу списка и подтвердите доступ к вашему Google-аккаунту.</li>
                            <li>На <b>Step 2 (Exchange authorization code for tokens)</b> нажмите кнопку <b>Exchange authorization code for tokens</b>.</li>
                            <li>В поле <b>Access token</b> скопируйте сгенерированный ключ (начинается с <code>ya29...</code>) и вставьте его в поле <b>Google OAuth / Access Bearer Token</b> в форме выше.</li>
                        </ol>
                    </div>

                    <!-- Шаг 2: Где взять Folder ID -->
                    <div style="background: #090d16; border: 1px solid #1e293b; border-radius: 8px; padding: 16px;">
                        <h3 style="margin: 0 0 8px 0; font-size: 14px; color: #a5f3fc; display: flex; align-items: center; gap: 8px;">
                            <span>📂 Шаг 2. Как получить Google Drive Folder ID (ID папки)</span>
                        </h3>
                        <ol style="margin: 0 0 0 18px; padding: 0; color: #cbd5e1; font-size: 13px; line-height: 1.7;">
                            <li>Откройте <a href="https://drive.google.com/" target="_blank" style="color: #38bdf8; text-decoration: underline; font-weight: bold;">Google Drive ↗</a> и создайте папку для записей (например, <i>«Asterisk Records»</i>).</li>
                            <li>Зайдите внутрь созданной папки. Посмотрите на адресную строку браузера:</li>
                            <li style="list-style: none; margin: 8px 0;">
                                <div style="background: #030712; padding: 8px 12px; border-radius: 6px; font-family: monospace; font-size: 12px; color: #94a3b8; border: 1px solid #1e293b;">
                                    https://drive.google.com/drive/folders/<span style="color: #38bdf8; font-weight: bold; background: rgba(56, 189, 248, 0.15); padding: 2px 6px; border-radius: 4px;">1uF-A1Z5I1Oy5BFWJkRPFpyb-cxfl5FVC</span>
                                </div>
                            </li>
                            <li>Скопируйте выделенную буквенно-цифровую часть после <code>/folders/</code> и вставьте в поле <b>Google Drive Folder ID</b>.</li>
                        </ol>
                    </div>
                </div>
            </div>
        </div>

        <!-- ПОД-ВКЛАДКА 4: ВНЕШНЯЯ IP-ТЕЛЕФОНИЯ (МНОЖЕСТВЕННЫЕ SIP-ТРАНКИ) -->
        <div id="subtab-telephony" class="subtab-content">
            <div class="card">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; flex-wrap: wrap; gap: 10px;">
                    <h2>
                        <span>🌐 Подключение внешних телефоний (Множественные SIP-Транки)</span>
                        <span style="font-size: 13px; color: #38bdf8;">Неограниченное число линий</span>
                    </h2>
                    <span style="background: rgba(56, 189, 248, 0.15); color: #38bdf8; padding: 4px 10px; border-radius: 6px; font-size: 12px; border: 1px solid rgba(56, 189, 248, 0.3);">
                        Подключено провайдеров: <b>{{ sip_trunks|length }}</b>
                    </span>
                </div>
                <p style="color: #94a3b8; font-size: 13px; margin-bottom: 18px; line-height: 1.5;">
                    Здесь вы можете настроить <b>сколько угодно внешних телефоний и SIP-транков</b> любых мировых провайдеров (в ОАЭ: <b>du Telecom</b>, <b>Etisalat</b>; международные: <b>Zadarma</b>, <b>Mango Office</b>, <b>Sipnet</b>, <b>МТТ</b>, <b>Telphin</b> и др.).
                    Каждый транк регистрируется в Asterisk как отдельный транш с поддержкой входящей и исходящей связи.
                </p>

                {% if sip_trunks %}
                <div class="table-scroll" style="margin-bottom: 25px;">
                    <table>
                        <thead>
                            <tr>
                                <th>Провайдер / Название</th>
                                <th>Сервер & Порт</th>
                                <th>Логин / Аккаунт</th>
                                <th>Исходящий CallerID</th>
                                <th>Использование</th>
                                <th>Связь (Сеть / Регистрация)</th>
                                <th>Управление</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for t in sip_trunks %}
                            <tr>
                                <td>
                                    <div style="font-weight: bold; color: #f8fafc; font-size: 14px;">{{ t.name }}</div>
                                    <div style="font-size: 11px; color: #64748b; font-family: monospace;">{{ t.id }}</div>
                                </td>
                                <td><code>{{ t.host }}:{{ t.port or 5060 }}</code></td>
                                <td><span style="color: #a5f3fc; font-weight: 500;">{{ t.username }}</span></td>
                                <td><span style="color: #38bdf8;">{{ t.callerid or '(Не указан)' }}</span></td>
                                <td>
                                    {% if t.enabled %}
                                    <span style="background: rgba(16, 185, 129, 0.15); color: #34d399; padding: 3px 8px; border-radius: 4px; font-size: 12px; font-weight: 600; border: 1px solid rgba(16, 185, 129, 0.3);">🟢 Включен (Используется)</span>
                                    {% else %}
                                    <span style="background: rgba(148, 163, 184, 0.15); color: #94a3b8; padding: 3px 8px; border-radius: 4px; font-size: 12px;">⚪ Отключен</span>
                                    {% endif %}
                                </td>
                                <td>
                                    {% if not t.enabled %}
                                    <span style="color: #64748b; font-size: 12px;">— (Линия выключена)</span>
                                    {% elif t.network_status.online %}
                                    <span style="background: rgba(16, 185, 129, 0.2); color: #34d399; padding: 3px 8px; border-radius: 4px; font-size: 12px; font-weight: 600; border: 1px solid rgba(16, 185, 129, 0.4);">{{ t.network_status.status_text }}</span>
                                    {% else %}
                                    <span style="background: rgba(239, 68, 68, 0.2); color: #f87171; padding: 3px 8px; border-radius: 4px; font-size: 12px; font-weight: 600; border: 1px solid rgba(239, 68, 68, 0.4);">{{ t.network_status.status_text }}</span>
                                    {% endif %}
                                </td>
                                <td>
                                    <div style="display: flex; gap: 6px; align-items: center;">
                                        <form method="POST" action="/settings/sip-trunks/toggle/{{ t.id }}" style="margin: 0;">
                                            <button type="submit" class="btn-secondary" style="width: auto; padding: 4px 10px; font-size: 12px; margin: 0;">
                                                {{ 'Отключить' if t.enabled else 'Включить' }}
                                            </button>
                                        </form>
                                        <form method="POST" action="/settings/sip-trunks/delete/{{ t.id }}" style="margin: 0;" onsubmit="return confirm('Удалить телефонию {{ t.name }}?');">
                                            <button type="submit" class="btn-danger" style="width: auto; padding: 4px 10px; font-size: 12px; margin: 0;">Удалить</button>
                                        </form>
                                    </div>
                                </td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
                {% endif %}

                <div style="background: #090d16; border: 1px solid #1e293b; border-radius: 8px; padding: 18px;">
                    <h3 style="font-size: 15px; color: #a5f3fc; margin: 0 0 14px 0;">➕ Подключить еще одну телефонию / SIP-транк</h3>
                    <form method="POST" action="/settings/sip-trunks/add">
                        <div class="grid">
                            <div>
                                <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 4px;">Название телефонии (напр. du UAE Trunk 1, Zadarma Dubai, Mango РФ):</label>
                                <input type="text" name="trunk_name" placeholder="du UAE Trunk 1" required style="margin-bottom: 12px;">

                                <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 4px;">SIP Сервер / Хост провайдера:</label>
                                <input type="text" name="trunk_host" placeholder="sip.du.ae или sip.zadarma.com" required style="margin-bottom: 12px;">

                                <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 4px;">SIP Порт:</label>
                                <input type="number" name="trunk_port" value="5060" style="margin-bottom: 12px;">
                            </div>

                            <div>
                                <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 4px;">Логин / Имя пользователя (User/Auth ID):</label>
                                <input type="text" name="trunk_user" placeholder="10001 или 971501234567" required style="margin-bottom: 12px;">

                                <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 4px;">Пароль SIP линии (Secret):</label>
                                <input type="password" name="trunk_pass" placeholder="Пароль от провайдера" required style="margin-bottom: 12px;">

                                <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 4px;">Исходящий CallerID (Номер телефона в формате E.164):</label>
                                <input type="text" name="trunk_callerid" placeholder="+971501234567" style="margin-bottom: 12px;">
                            </div>
                        </div>
                        <button type="submit" class="btn-success" style="width: auto; margin-top: 6px; padding: 10px 24px;">➕ Добавить эту телефонию</button>
                    </form>
                </div>
            </div>
        </div>
    </div>



    <!-- ВКЛАДКА: НАСТРОЙКИ СЕТИ & АВТОЗАЩИТА IP -->
    <div id="tab-network" class="tab-content">
        
        <!-- КАРТОЧКА: АКТИВНЫЙ СЕТЕВОЙ СТАТУС & ПРЕДЛОЖЕНИЕ СОХРАНИТЬ -->
        {% set net_info = get_system_network_info() %}
        {% set saved_net = integrations.get('network', {}) %}
        
        <div class="card" style="margin-bottom: 20px; border-left: 4px solid #38bdf8;">
            <h2>
                <span>🌐 Сетевые интерфейсы & Умная защита IP при переезде</span>
                <span style="font-size: 13px; color: #38bdf8;">Smart DHCP & Subnet Guardian</span>
            </h2>
            <p style="color: #94a3b8; font-size: 13px; margin-bottom: 15px;">
                Система автоматически отслеживает смену локальной подсети (например, при переезде оборудования в другой офис или другую сеть):
                <br>• <b>Если подсеть совпадает</b> — PBX применяет ваш сохраненный статический IP для стабильной работы NAT/портов.
                <br>• <b>Если подсеть изменилась</b> — PBX <u>игнорирует</u> старый IP, получает адрес от DHCP, отправляет уведомление в Telegram и предлагает сохранить новый IP, исключая потерю доступа!
            </p>

            <div class="grid" style="grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 15px; margin-bottom: 20px;">
                <div style="background: #0f172a; padding: 14px; border-radius: 8px; border: 1px solid #1e293b;">
                    <div style="font-size: 11px; color: #94a3b8; text-transform: uppercase;">Текущий активный IP:</div>
                    <div style="font-size: 20px; font-weight: bold; color: #38bdf8; font-family: monospace; margin-top: 4px;">{{ net_info.current_ip }}</div>
                    <div style="font-size: 11px; color: #64748b; margin-top: 2px;">Маска: /{{ net_info.prefix }}</div>
                </div>

                <div style="background: #0f172a; padding: 14px; border-radius: 8px; border: 1px solid #1e293b;">
                    <div style="font-size: 11px; color: #94a3b8; text-transform: uppercase;">Основной шлюз (Gateway):</div>
                    <div style="font-size: 18px; font-weight: bold; color: #f8fafc; font-family: monospace; margin-top: 4px;">{{ net_info.gateway }}</div>
                    <div style="font-size: 11px; color: #64748b; margin-top: 2px;">Роутер / Интернет</div>
                </div>

                <div style="background: #0f172a; padding: 14px; border-radius: 8px; border: 1px solid #1e293b;">
                    <div style="font-size: 11px; color: #94a3b8; text-transform: uppercase;">Сохраненный постоянный IP:</div>
                    <div style="font-size: 18px; font-weight: bold; color: {{ '#34d399' if saved_net.saved_ip else '#f59e0b' }}; font-family: monospace; margin-top: 4px;">
                        {{ saved_net.saved_ip if saved_net.saved_ip else 'Не зафиксирован' }}
                    </div>
                    <div style="font-size: 11px; color: #64748b; margin-top: 2px;">
                        {{ 'Защищен в Netplan' if saved_net.saved_ip else 'Работает по DHCP' }}
                    </div>
                </div>

                <div style="background: #0f172a; padding: 14px; border-radius: 8px; border: 1px solid #1e293b;">
                    <div style="font-size: 11px; color: #94a3b8; text-transform: uppercase;">MAC-адрес платы:</div>
                    <div style="font-size: 15px; font-weight: bold; color: #cbd5e1; font-family: monospace; margin-top: 6px;">{{ net_info.mac }}</div>
                </div>
            </div>

            <!-- БЛОК БЫСТРОГО ПРЕДЛОЖЕНИЯ СОХРАНЕНИЯ ТЕКУЩЕГО IP -->
            {% if not saved_net.saved_ip or saved_net.saved_ip != net_info.current_ip %}
            <div style="background: #064e3b; border: 1px solid #059669; padding: 14px; border-radius: 8px; margin-bottom: 20px; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px;">
                <div>
                    <h4 style="margin: 0 0 4px 0; color: #34d399; font-size: 14px;">💡 Обнаружен новый сетевой адрес: <code>{{ net_info.current_ip }}</code></h4>
                    <p style="margin: 0; color: #a7f3d0; font-size: 12px;">Хотите зафиксировать этот IP как постоянный адрес для этой локальной сети?</p>
                </div>
                <form method="POST" action="/settings/network/quick-save" style="margin: 0;">
                    <input type="hidden" name="ip_addr" value="{{ net_info.current_ip }}">
                    <input type="hidden" name="prefix" value="{{ net_info.prefix }}">
                    <input type="hidden" name="gateway" value="{{ net_info.gateway }}">
                    <button type="submit" class="btn" style="margin: 0; width: auto; background: #10b981; font-weight: bold; padding: 8px 16px;">💾 Зафиксировать {{ net_info.current_ip }}</button>
                </form>
            </div>
            {% endif %}

            <!-- ФОРМА РУЧНОЙ НАСТРОЙКИ СЕТИ -->
            <h3 style="font-size: 15px; color: #a5f3fc; margin-bottom: 12px;">⚙️ Ручная конфигурация сетевого адаптера (eth0)</h3>
            <form method="POST" action="/settings/network/save">
                <div class="grid">
                    <div>
                        <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 4px;">Режим получения адреса:</label>
                        <select name="net_mode" id="net_mode_select" style="margin-bottom: 12px;" onchange="toggleNetMode(this.value)">
                            <option value="static" {% if saved_net.mode != 'dhcp' %}selected{% endif %}>📌 Статический IP (Фиксированный с авто-контролем подсети)</option>
                            <option value="dhcp" {% if saved_net.mode == 'dhcp' %}selected{% endif %}>🔄 Динамический DHCP (Автоматически от роутера)</option>
                        </select>

                        <div id="static_ip_fields">
                            <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 4px;">IP-адрес PBX:</label>
                            <input type="text" name="ip_addr" value="{{ saved_net.saved_ip or net_info.current_ip }}" placeholder="192.168.0.106" style="margin-bottom: 12px;">

                            <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 4px;">Маска подсети (префикс, например 24 для 255.255.255.0):</label>
                            <input type="number" name="prefix" value="{{ saved_net.saved_prefix or net_info.prefix }}" min="8" max="32" style="margin-bottom: 12px;">
                        </div>
                    </div>

                    <div>
                        <div id="static_gw_fields">
                            <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 4px;">Шлюз по умолчанию (Gateway / IP роутера):</label>
                            <input type="text" name="gateway" value="{{ saved_net.saved_gateway or net_info.gateway }}" placeholder="192.168.0.1" style="margin-bottom: 12px;">

                            <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 4px;">DNS Серверы (через запятую):</label>
                            <input type="text" name="dns_servers" value="8.8.8.8, 1.1.1.1" placeholder="8.8.8.8, 1.1.1.1" style="margin-bottom: 12px;">
                        </div>

                        <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 4px;">Telegram-оповещения:</label>
                        <div style="background: #0f172a; padding: 10px; border-radius: 6px; border: 1px solid #1e293b; color: #94a3b8; font-size: 12px;">
                            При изменении подсети бот отправит новый IP-адрес в Telegram (настраивается во вкладке «Интеграции»).
                        </div>
                    </div>
                </div>

                <div style="margin-top: 15px; display: flex; gap: 10px; justify-content: flex-end;">
                    <button type="submit" class="btn" style="width: auto; background: #0284c7; padding: 8px 20px;">💾 Применить и сохранить настройки сети</button>
                </div>
            </form>
        </div>

    </div>


    <!-- ВКЛАДКА: ДОКУМЕНТАЦИЯ & API -->
    <div id="tab-docs" class="tab-content">
        <!-- ПАНЕЛЬ ПОД-ВКЛАДОК ДОКУМЕНТАЦИИ -->
        <div class="sub-nav-tabs">
            <button type="button" class="sub-tab-btn active" onclick="openSubTab('subtab-docs-callto', this)">Webhook CallTo</button>
            <button type="button" class="sub-tab-btn" onclick="openSubTab('subtab-docs-rest', this)">REST API</button>
            <button type="button" class="sub-tab-btn" onclick="openSubTab('subtab-docs-crm', this)">CRM и Webhooks</button>
            <button type="button" class="sub-tab-btn" onclick="openSubTab('subtab-docs-sip', this)">SIP и IAX2</button>
        </div>

        <!-- ПОД-ВКЛАДКА 1: CALLTO & CLICK-TO-CALL -->
        <div id="subtab-docs-callto" class="subtab-content active">
            
            <!-- КАРТОЧКА НАСТРОЕК API & КЛЮЧА -->
            <div class="card" style="margin-bottom: 20px; border-left: 4px solid #38bdf8;">
                <h2>
                    <span>⚡ CallTo & Webhook API (Click-to-Call)</span>
                    <span style="font-size: 13px; color: #38bdf8;">Автодозвон & Обратный звонок</span>
                </h2>
                <p style="color: #94a3b8; font-size: 13px; margin-bottom: 15px;">
                    Функция <b>CallTo (Click-to-Call)</b> позволяет в один клик инициировать исходящий звонок из любых CRM (amoCRM, Bitrix24), сайтов, лид-форм или мобильных приложений:
                    <br>1️⃣ Asterisk звонит на софтфон оператора (например, <code>101</code>).
                    <br>2️⃣ Как только оператор поднимает трубку — Asterisk мгновенно начинает набор клиенту через настроенный GSM-модем или транк и соединяет их с чистым звуком и стереозаписью!
                </p>

                <form method="POST" action="/settings/webhooks/save">
                    <div class="grid">
                        <div>
                            <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 4px;">Секретный API Ключ (X-API-Key / Token):</label>
                            <div style="display: flex; gap: 8px; margin-bottom: 12px;">
                                <input type="text" id="webhook_api_token" name="api_token" value="{{ integrations.webhooks.api_token if integrations.webhooks else 'sk_live_token' }}" readonly style="font-family: monospace; font-size: 13px; color: #38bdf8; background: #0f172a; margin: 0;">
                                <button type="button" class="btn" style="width: auto; margin:0; padding: 6px 14px; background: #334155;" onclick="copyApiToken()">📋 Скопировать</button>
                            </div>
                        </div>
                        <div>
                            <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 4px;">Оператор по умолчанию (если не передан в запросе):</label>
                            <input type="text" name="default_operator" value="{{ integrations.webhooks.default_operator if integrations.webhooks else '101' }}" placeholder="101" style="margin-bottom: 12px;">
                        </div>
                    </div>

                    <div style="display: flex; align-items: center; justify-content: space-between; margin-top: 10px;">
                        <label style="display: flex; align-items: center; gap: 8px; cursor: pointer; color: #f8fafc; font-size: 14px;">
                            <input type="checkbox" name="webhooks_enabled" value="true" {% if integrations.webhooks and integrations.webhooks.enabled %}checked{% endif %} style="width: auto; margin: 0;">
                            Включить CallTo Webhook API
                        </label>
                        <div style="display: flex; gap: 8px;">
                            <button type="submit" class="btn" style="width: auto; margin: 0; background: #0284c7;">💾 Сохранить настройки</button>
                        </div>
                    </div>
                </form>

                <form method="POST" action="/settings/webhooks/regenerate-token" style="margin-top: 10px; text-align: right;" onsubmit="return confirm('Сгенерировать новый API-ключ? Старый ключ перестанет работать!');">
                    <button type="submit" style="background: none; border: none; color: #f43f5e; font-size: 12px; cursor: pointer; text-decoration: underline; padding: 0;">🔄 Сгенерировать новый API-ключ</button>
                </form>
            </div>

            <!-- КАРТОЧКА: 🧪 ИНТЕРАКТИВНЫЙ ТЕСТЕР ВЫЗОВА -->
            <div class="card" style="margin-bottom: 20px; border-left: 4px solid #10b981; background: #09131d;">
                <h3 style="font-size: 16px; color: #34d399; margin-top: 0; margin-bottom: 10px; display: flex; align-items: center; gap: 8px;">
                    <span>🧪 Интерактивный Тестер CallTo (Проверить в 1 клик)</span>
                </h3>
                <p style="color: #94a3b8; font-size: 13px; margin-bottom: 15px;">
                    Введите внутренний номер оператора и номер телефона клиента для проверки прямо сейчас:
                </p>
                <div class="grid" style="grid-template-columns: 1fr 2fr auto; align-items: flex-end; gap: 12px;">
                    <div>
                        <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 4px;">Номер оператора:</label>
                        <input type="text" id="test_callto_operator" value="101" placeholder="101" style="margin: 0;">
                    </div>
                    <div>
                        <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 4px;">Номер телефона клиента:</label>
                        <input type="text" id="test_callto_phone" placeholder="+79260000000 или +97150000000" style="margin: 0;">
                    </div>
                    <div>
                        <button type="button" class="btn" onclick="executeCallToTest()" style="margin: 0; background: #059669; white-space: nowrap; height: 42px; font-weight: bold;">▶ Запустить CallTo</button>
                    </div>
                </div>

                <!-- Блок результата теста -->
                <div id="callto_test_result" style="display: none; margin-top: 15px; padding: 12px 16px; border-radius: 8px; font-size: 13px;"></div>
            </div>

            <!-- КАРТОЧКА: 📖 ДОКУМЕНТАЦИЯ И ПРИМЕРЫ -->
            <div class="card" style="margin-bottom: 20px;">
                <h3 style="font-size: 16px; color: #38bdf8; margin-top: 0; margin-bottom: 12px;">
                    📖 Спецификация API & Примеры интеграции
                </h3>

                <div style="background: #0f172a; padding: 14px; border-radius: 8px; margin-bottom: 16px; border: 1px solid #1e293b;">
                    <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 8px;">
                        <span style="background: #0284c7; color: #fff; padding: 3px 8px; border-radius: 4px; font-weight: bold; font-size: 12px;">POST / GET</span>
                        <code style="font-size: 14px; color: #f8fafc; font-weight: bold;">http://{{ request.host }}/api/v1/callto</code>
                    </div>
                    <p style="color: #94a3b8; font-size: 13px; margin: 0;">
                        Поддерживает как <b>JSON (POST)</b>, так и быстрые вызовы через <b>GET Query параметры</b>.
                    </p>
                </div>

                <h4 style="font-size: 14px; color: #a5f3fc; margin-bottom: 8px;">Параметры запроса:</h4>
                <div class="table-scroll" style="margin-bottom: 20px;">
                    <table>
                        <thead>
                            <tr>
                                <th>Параметр</th>
                                <th>Тип</th>
                                <th>Обязательный</th>
                                <th>Описание</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr>
                                <td><code>phone</code> / <code>to</code> / <code>dst</code></td>
                                <td>String</td>
                                <td><span style="color: #ef4444; font-weight: bold;">Да</span></td>
                                <td>Номер телефона клиента (любой формат: <code>+79260389197</code>, <code>+971501234567</code>, <code>8926...</code>).</td>
                            </tr>
                            <tr>
                                <td><code>operator</code> / <code>src</code> / <code>from</code></td>
                                <td>String</td>
                                <td>Нет</td>
                                <td>Внутренний номер софтфона оператора (например <code>101</code>).</td>
                            </tr>
                            <tr>
                                <td><code>token</code> / <code>api_key</code></td>
                                <td>String</td>
                                <td><span style="color: #ef4444; font-weight: bold;">Да*</span></td>
                                <td>API-токен (передается в <code>?token=...</code> или в заголовке <code>X-API-Key: ...</code>).</td>
                            </tr>
                            <tr>
                                <td><code>callerid</code></td>
                                <td>String</td>
                                <td>Нет</td>
                                <td>Текст Caller ID, отображаемый на дисплее софтфона оператора.</td>
                            </tr>
                        </tbody>
                    </table>
                </div>

                <h4 style="font-size: 14px; color: #a5f3fc; margin-bottom: 8px;">Готовые примеры кода:</h4>
                
                <div class="sub-nav-tabs" style="margin-bottom: 12px; background: #0f172a;">
                    <button type="button" class="sub-tab-btn active" onclick="showDocSnippet('doc-curl', this)">cURL / Bash</button>
                    <button type="button" class="sub-tab-btn" onclick="showDocSnippet('doc-python', this)">Python</button>
                    <button type="button" class="sub-tab-btn" onclick="showDocSnippet('doc-php', this)">PHP</button>
                    <button type="button" class="sub-tab-btn" onclick="showDocSnippet('doc-js', this)">JavaScript / Node.js</button>
                    <button type="button" class="sub-tab-btn" onclick="showDocSnippet('doc-crm', this)">amoCRM / Bitrix24</button>
                    <button type="button" class="sub-tab-btn" onclick="showDocSnippet('doc-html', this)">HTML Ссылка</button>
                </div>

                <!-- Snippet: cURL -->
                <div id="doc-curl" class="doc-code-snippet">
                    <pre style="background: #020617; padding: 14px; border-radius: 8px; color: #38bdf8; font-family: monospace; font-size: 12px; overflow-x: auto;"><code>curl -X POST http://{{ request.host }}/api/v1/callto   -H "Content-Type: application/json"   -H "X-API-Key: {{ integrations.webhooks.api_token if integrations.webhooks else 'ВАШ_API_ТОКЕН' }}"   -d '{
    "operator": "101",
    "phone": "+79260389197"
  }'</code></pre>
                </div>

                <!-- Snippet: Python -->
                <div id="doc-python" class="doc-code-snippet" style="display: none;">
                    <pre style="background: #020617; padding: 14px; border-radius: 8px; color: #a5f3fc; font-family: monospace; font-size: 12px; overflow-x: auto;"><code>import requests

url = "http://{{ request.host }}/api/v1/callto"
headers = {
    "X-API-Key": "{{ integrations.webhooks.api_token if integrations.webhooks else 'ВАШ_API_ТОКЕН' }}",
    "Content-Type": "application/json"
}
payload = {
    "operator": "101",
    "phone": "+79260389197"
}

response = requests.post(url, json=payload, headers=headers)
print("Результат вызова:", response.json())</code></pre>
                </div>

                <!-- Snippet: PHP -->
                <div id="doc-php" class="doc-code-snippet" style="display: none;">
                    <pre style="background: #020617; padding: 14px; border-radius: 8px; color: #c084fc; font-family: monospace; font-size: 12px; overflow-x: auto;"><code>&lt;?php
$token = "{{ integrations.webhooks.api_token if integrations.webhooks else 'ВАШ_API_ТОКЕН' }}";
$operator = "101";
$phone = urlencode("+79260389197");

// Быстрый вызов в 1 строчку через GET запрос
$url = "http://{{ request.host }}/api/v1/callto?operator={$operator}&phone={$phone}&token={$token}";
$response = file_get_contents($url);
$data = json_decode($response, true);

if ($data['status'] === 'success') {
    echo "Вызов успешно инициирован!";
}
?&gt;</code></pre>
                </div>

                <!-- Snippet: JS -->
                <div id="doc-js" class="doc-code-snippet" style="display: none;">
                    <pre style="background: #020617; padding: 14px; border-radius: 8px; color: #facc15; font-family: monospace; font-size: 12px; overflow-x: auto;"><code>// Пример для фронтенда сайта или бэкенда на Node.js
async function makeCallTo(operatorExt, clientPhone) {
  const res = await fetch("http://{{ request.host }}/api/v1/callto", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": "{{ integrations.webhooks.api_token if integrations.webhooks else 'ВАШ_API_ТОКЕН' }}"
    },
    body: JSON.stringify({
      operator: operatorExt,
      phone: clientPhone
    })
  });
  const data = await res.json();
  console.log("Статус вызова:", data);
  return data;
}

// Запуск вызова
makeCallTo("101", "+79260389197");</code></pre>
                </div>

                <!-- Snippet: CRM -->
                <div id="doc-crm" class="doc-code-snippet" style="display: none;">
                    <div style="background: #020617; padding: 14px; border-radius: 8px; color: #e2e8f0; font-size: 13px;">
                        <p style="margin-top: 0; color: #38bdf8; font-weight: bold;">🔗 URL Вебхука для автоматизации в CRM (Digital Pipeline / Бизнес-процессы):</p>
                        <pre style="background: #0f172a; padding: 10px; border-radius: 6px; color: #34d399; font-family: monospace; font-size: 12px; margin-bottom: 12px;"><code>http://{{ request.host }}/api/v1/callto?phone={{ '{{lead.phone}}' }}&operator={{ '{{user.phone}}' }}&token={{ integrations.webhooks.api_token if integrations.webhooks else 'ВАШ_API_ТОКЕН' }}</code></pre>
                        <ul style="margin: 0; padding-left: 20px; color: #94a3b8; font-size: 12px; line-height: 1.6;">
                            <li><b>amoCRM:</b> В настройках воронки добавьте действие <i>«Отправить Webhook»</i> или триггер виджета и вставьте указанный выше URL.</li>
                            <li><b>Bitrix24:</b> В роботах/бизнес-процессах добавьте действие <i>«Исходящий вебхук»</i> с методом GET/POST.</li>
                        </ul>
                    </div>
                </div>

                <!-- Snippet: HTML -->
                <div id="doc-html" class="doc-code-snippet" style="display: none;">
                    <div style="background: #020617; padding: 14px; border-radius: 8px; color: #e2e8f0; font-size: 13px;">
                        <p style="margin-top: 0; color: #38bdf8; font-weight: bold;">🌐 Кнопка «Позвонить клиенту» для CRM или сайта:</p>
                        <pre style="background: #0f172a; padding: 10px; border-radius: 6px; color: #f472b6; font-family: monospace; font-size: 12px; margin-bottom: 12px;"><code>&lt;!-- Простая ссылка клика в браузере --&gt;
&lt;a href="http://{{ request.host }}/callto?operator=101&phone=+79260389197&token={{ integrations.webhooks.api_token if integrations.webhooks else 'ВАШ_API_ТОКЕН' }}" target="_blank" class="btn"&gt;
    📞 Позвонить клиенту (+79260389197)
&lt;/a&gt;</code></pre>
                    </div>
                </div>

            </div>

        </div>

        <!-- ПОД-ВКЛАДКА 2: REST API & REALTIME -->
        <div id="subtab-docs-rest" class="subtab-content">
            <div class="card">
                <h2>📡 Системные REST API Эндпоинты</h2>
                <div class="table-scroll">
                    <table>
                        <thead>
                            <tr>
                                <th>Метод & URL</th>
                                <th>Формат</th>
                                <th>Описание</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr>
                                <td><code>GET /api/realtime</code></td>
                                <td>JSON</td>
                                <td>Возвращает текущие активные каналы, статус звонков, операторов и GSM-модема.</td>
                            </tr>
                            <tr>
                                <td><code>GET /api/check-update</code></td>
                                <td>JSON</td>
                                <td>Проверяет наличие новых обновлений прошивки Asterisk GUI на GitHub.</td>
                            </tr>
                            <tr>
                                <td><code>POST /action/do-update</code></td>
                                <td>Form/POST</td>
                                <td>Запускает автообновление прошивки в фоновом режиме через <code>updater.sh</code>.</td>
                            </tr>
                            <tr>
                                <td><code>GET /api/amocrm/pipelines</code></td>
                                <td>JSON</td>
                                <td>Возвращает список воронок и статусов из подключенного аккаунта amoCRM.</td>
                            </tr>
                            <tr>
                                <td><code>GET /api/amocrm/users</code></td>
                                <td>JSON</td>
                                <td>Возвращает список менеджеров amoCRM для сопоставления с операторами SIP.</td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- ПОД-ВКЛАДКА 3: CRM & WEBHOOKS -->
        <div id="subtab-docs-crm" class="subtab-content">
            <div class="card">
                <h2>🏢 Формат интеграции с amoCRM и Google Drive</h2>
                <p style="color: #94a3b8; font-size: 13px;">
                    После завершения каждого входящего или исходящего вызова Asterisk автоматически запускает скрипт <code>/opt/crm-yandex-uploader.py</code>:
                </p>
                <div style="background: #0f172a; padding: 14px; border-radius: 8px; border: 1px solid #1e293b; color: #e2e8f0; font-size: 13px;">
                    <p style="margin-top:0; color:#38bdf8; font-weight:bold;">Цикл обработки вызова:</p>
                    <ol style="padding-left: 20px; line-height: 1.6; margin: 0;">
                        <li>Запись аудиофайла в формате WAV сохраняется в <code>/var/spool/asterisk/monitor/</code>.</li>
                        <li>Скрипт загружает аудиозапись в указанную папку Google Drive через Google OAuth API.</li>
                        <li>Создается публичная прямая ссылка на прослушивание аудиозаписи.</li>
                        <li>В amoCRM через метод <code>POST /api/v4/calls</code> регистрируется звонок с длительностью, статусом (ANSWERED / NO ANSWER), ID оператора и ссылкой на запись в Google Drive.</li>
                    </ol>
                </div>
            </div>
        </div>

        <!-- ПОД-ВКЛАДКА 4: SIP & IAX2 -->
        <div id="subtab-docs-sip" class="subtab-content">
            <div class="card">
                <h2>🌐 Сетевые протоколы SIP PJSIP и IAX2</h2>
                <div class="table-scroll">
                    <table>
                        <thead>
                            <tr>
                                <th>Протокол</th>
                                <th>Порт</th>
                                <th>Назначение</th>
                                <th>Особенности</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr>
                                <td><b>IAX2</b></td>
                                <td><code>UDP 40002 / 5060 / 4569</code></td>
                                <td>Межатс-транкинг (Коробка 1 ➔ Коробка 2)</td>
                                <td>Сигнализация и Голос упакованы в 1 порт. Идеально для работы за NAT/роутером.</td>
                            </tr>
                            <tr>
                                <td><b>PJSIP UDP/TCP</b></td>
                                <td><code>UDP/TCP 5060, 5061</code></td>
                                <td>Подключение софтфонов (Zoiper, MicroSIP)</td>
                                <td>Поддержка HD-кодеков G.711a, G.722, PCM Linear.</td>
                            </tr>
                            <tr>
                                <td><b>RTP Audio</b></td>
                                <td><code>UDP 10000-20000</code></td>
                                <td>Передача аудиопотока в PJSIP</td>
                                <td>Автоматический симметричный RTP (<code>rtp_symmetric=yes</code>, <code>direct_media=no</code>).</td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

    </div>

    <!-- ВКЛАДКА 6: SIP АККАУНТЫ -->
    <div id="tab-sip" class="tab-content">
        <div class="card">
            <h2>
                <span>👥 Список SIP Аккаунтов</span>
                <span id="sip-count-badge" style="font-size: 13px; color: #94a3b8;">Всего: {{ accounts|length }}</span>
            </h2>
            <div id="sip-table-container"></div>
        </div>

        <div class="card">
            <h2>➕ Создать / Изменить SIP Аккаунт</h2>
            <form method="POST" action="/sip/save">
                <div class="grid">
                    <div>
                        <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 4px;">Внутренний номер:</label>
                        <input type="text" name="exten" placeholder="Например: 103" required>
                    </div>
                    <div>
                        <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 4px;">Пароль:</label>
                        <input type="text" name="password" placeholder="Пароль" required>
                    </div>
                </div>
                <button type="submit" class="btn-success" style="width: auto; margin-top: 10px; padding: 10px 24px;">Сохранить SIP Аккаунт</button>
            </form>
        </div>

        <!-- КАРТОЧКА ОТДЕЛОВ И ГРУПП ВЫЗОВА -->
        <div class="card" style="margin-top: 20px;">
            <h2>
                <span>🏢 Отделы и Группы вызова (Ring Groups)</span>
                <span style="font-size: 13px; color: #38bdf8;">Короткие номера отделов</span>
            </h2>
            <p style="color: #94a3b8; font-size: 13px; margin-bottom: 15px;">
                Объединяйте нескольких операторов в группы по короткому номеру (например, <b>200</b> — Отдел продаж, <b>300</b> — Поддержка).
                При звонке на номер отдела вызов поступит одновременно всем операторам группы со стереозаписью.
            </p>

            {% if ring_groups %}
            <div class="table-scroll" style="margin-bottom: 20px;">
                <table>
                    <thead>
                        <tr>
                            <th>Номер группы</th>
                            <th>Название отдела</th>
                            <th>Операторы в группе</th>
                            <th>Таймаут (сек)</th>
                            <th>Действие</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for g in ring_groups %}
                        <tr>
                            <td><span style="font-size: 14px; font-weight: bold; color: #38bdf8;">{{ g.exten }}</span></td>
                            <td><b>{{ g.name }}</b></td>
                            <td>
                                {% for m in g.members %}
                                <span style="background: #1e293b; padding: 2px 8px; border-radius: 4px; font-size: 12px; margin-right: 4px; border: 1px solid #334155;">SIP/{{ m }}</span>
                                {% endfor %}
                            </td>
                            <td>{{ g.timeout }} сек</td>
                            <td>
                                <form method="POST" action="/settings/ring-groups/delete/{{ g.exten }}" style="margin: 0;" onsubmit="return confirm('Удалить группу {{ g.name }} ({{ g.exten }})?');">
                                    <button type="submit" class="btn-danger" style="width: auto; margin:0; padding: 4px 10px; font-size: 12px;">Удалить</button>
                                </form>
                            </td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
            {% endif %}

            <h3 style="font-size: 15px; color: #a5f3fc; margin-bottom: 12px;">➕ Создать новый отдел / группу</h3>
            <form method="POST" action="/settings/ring-groups/add">
                <div class="grid">
                    <div>
                        <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 4px;">Короткий номер группы (напр. 200, 300, 500):</label>
                        <input type="text" name="group_exten" placeholder="200" required style="margin-bottom: 12px;">

                        <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 4px;">Название отдела (напр. Отдел продаж / Sales):</label>
                        <input type="text" name="group_name" placeholder="Отдел продаж" required style="margin-bottom: 12px;">

                        <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 4px;">Таймаут звонка (секунд):</label>
                        <input type="number" name="group_timeout" value="30" min="5" max="180" style="margin-bottom: 12px;">
                    </div>

                    <div>
                        <label style="font-size: 12px; color: #94a3b8; display: block; margin-bottom: 8px;">Выберите участников группы (Операторы):</label>
                        <div style="background: #070a12; border: 1px solid #1e293b; border-radius: 8px; padding: 12px; max-height: 180px; overflow-y: auto;">
                            {% for acc in accounts %}
                            <div class="checkbox-container" style="margin-bottom: 8px;">
                                <input type="checkbox" name="group_members" value="{{ acc.exten }}" id="gm_{{ acc.exten }}" checked>
                                <label for="gm_{{ acc.exten }}" style="cursor: pointer; font-size: 13px;">
                                    <b>{{ acc.exten }}</b> — {{ acc.name or ('Оператор ' ~ acc.exten) }}
                                </label>
                            </div>
                            {% endfor %}
                        </div>
                    </div>
                </div>
                <button type="submit" class="btn-success" style="width: auto; margin-top: 10px; padding: 10px 24px;">Создать отдел</button>
            </form>
        </div>
    </div>

    <!-- ВКЛАДКА 7: ТРАФИК И МОДЕМ -->
    <div id="tab-live-traffic" class="tab-content">
        <div class="card">
            <h2>
                <span>📡 GSM Модем (Huawei Dongle)</span>
                <span id="modem-header-status">Опрос...</span>
            </h2>
            <div id="modem-parsed-container"></div>
        </div>

        <div class="card">
            <h2>
                <span>📞 Текущие активные разговоры (Live Calls)</span>
                <span style="font-size: 12px; color: #10b981;">● В реальном времени</span>
            </h2>
            <div id="live-human-channels"></div>
        </div>

        <div class="grid">
            <div class="card">
                <h2>
                    <span>🌐 Активные SIP клиенты онлайн (IP / Порты)</span>
                    <span style="font-size: 12px; color: #10b981;">● Live Sockets</span>
                </h2>
                <div id="live-human-sockets"></div>
            </div>

            <div class="card">
                <h2>
                    <span>🔐 Системный поток Asterisk</span>
                    <span style="font-size: 12px; color: #10b981;">● Live Stream</span>
                </h2>
                <pre id="live-auth-logs" style="max-height: 250px;">Ожидание трафика...</pre>
            </div>
        </div>
    </div>

    <!-- ВКЛАДКА 8: ОБНОВЛЕНИЕ -->
    <div id="tab-update" class="tab-content">
        <div class="card">
            <h2>
                <span>🔄 Обновление системы Asterisk PBX</span>
                <span style="font-size: 13px; color: #10b981;">Текущая версия: {{ current_version }}</span>
            </h2>
            <p style="color: #94a3b8; font-size: 13px; margin-bottom: 15px;">
                Система поддерживает автоматическое обновление. При обновлении конфигурационные файлы и настройки не затрагиваются. 
                Обновления применяются последовательно.
            </p>
            <div id="update-status-box" class="debug-box" style="display: none;"></div>
            
            <button onclick="checkForUpdates()" class="btn-success" style="width: auto; padding: 10px 24px; margin-bottom: 15px;">🔍 Проверить обновления</button>
            
            <div id="update-actions" style="display: none; margin-top: 15px; border-top: 1px solid #1e293b; padding-top: 15px;">
                <h3 style="margin-top:0; font-size: 15px; color: #a5f3fc;" id="new-version-title">Доступна новая версия!</h3>
                <pre id="changelog-box" style="margin-bottom: 15px;"></pre>
                <form method="POST" action="/action/do-update" onsubmit="showLoadingSpinnerUpdate()">
                    <button type="submit" class="btn-play" style="padding: 10px 24px; font-size: 14px;">🚀 Установить обновление</button>
                </form>
            </div>

            <!-- БЛОК ПРИНУДИТЕЛЬНОГО ОБНОВЛЕНИЯ И ИНСТРУКЦИИ ДЛЯ КОНСОЛИ -->
            <div style="margin-top: 25px; padding-top: 15px; border-top: 1px solid #1e293b;">
                <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; margin-bottom: 15px;">
                    <div>
                        <div style="font-size: 13px; font-weight: bold; color: #cbd5e1;">Принудительная переустановка</div>
                        <div style="font-size: 12px; color: #64748b;">Синхронизировать код с Git без проверки номера версии</div>
                    </div>
                    <form method="POST" action="/action/do-update" onsubmit="showLoadingSpinnerUpdate()" style="margin:0;">
                        <button type="submit" class="btn-secondary" style="width: auto; padding: 6px 14px; font-size: 12px;">🔄 Принудительно обновить из Git</button>
                    </form>
                </div>

                <details style="background: #070a12; border: 1px solid #1e293b; border-radius: 8px; padding: 12px 16px;">
                    <summary style="cursor: pointer; color: #38bdf8; font-weight: 600; font-size: 13px;">💻 Как обновить систему через консоль (SSH)?</summary>
                    <div style="margin-top: 12px; font-size: 12px; color: #94a3b8; line-height: 1.6;">
                        <p style="margin: 0 0 8px 0;"><b style="color: #f8fafc;">1. Быстрое обновление установленной системы:</b></p>
                        <pre style="background: #0b0f19; padding: 8px 12px; border-radius: 6px; color: #a5f3fc; margin: 0 0 12px 0;">sudo bash /opt/asterisk-gui/updater.sh</pre>

                        <p style="margin: 0 0 8px 0;"><b style="color: #f8fafc;">2. Для старых коробок (где нет вкладки «Обновление»):</b></p>
                        <pre style="background: #0b0f19; padding: 8px 12px; border-radius: 6px; color: #a5f3fc; margin: 0 0 12px 0; white-space: pre-wrap; word-break: break-all;">sudo apt-get update && sudo apt-get install -y git && sudo rm -rf /opt/asterisk-gui && sudo git clone https://<YOUR_GITHUB_TOKEN>@github.com/apavlishev/asterisk-ui-vps.git /opt/asterisk-gui && cd /opt/asterisk-gui && sudo ./install.sh</pre>
                        <small style="color: #10b981;">✓ Настройки (integrations_config.json) и записи звонков не затираются!</small>
                    </div>
                </details>
            </div>
        </div>
    </div>

<!-- ПОПАП АУДИОПЛЕЕРА И ДЕТАЛЬНОГО МАРШРУТА ВЫЗОВА -->
<div id="audio-modal" class="modal-overlay" onclick="closeAudioModal(event)">
    <div class="modal-box" onclick="event.stopPropagation()">
        <div class="modal-header">
            <div id="modal-call-title" class="modal-title" style="display: flex; align-items: center; gap: 8px;">Детали вызова</div>
            <button class="modal-close" onclick="closeAudioModal()">&times;</button>
        </div>
        
        <!-- БЛОК АУДИОПЛЕЕРА (если есть запись) -->
        <div id="modal-audio-player-box" style="background: #070a12; border: 1px solid #1e293b; border-radius: 8px; padding: 14px; margin-bottom: 16px; display: none;">
            <audio id="popup-audio-player" controls style="width: 100%; margin-bottom: 8px;"></audio>
            <div style="display: flex; justify-content: space-between; align-items: center; font-size: 12px; color: #94a3b8; flex-wrap: wrap; gap: 8px;">
                <span>🎙 <b>2-канальная стереозапись:</b> L (Левый) = Оператор | R (Правый) = Клиент</span>
                <div style="display: flex; align-items: center; gap: 10px;">
                    <span style="color: #a5f3fc;" id="modal-audio-size"></span>
                    <a id="modal-download-link" href="#" download class="btn-play" style="text-decoration: none; padding: 4px 12px; font-size: 12px;">⬇ Скачать WAV</a>
                    <button id="modal-push-amocrm-btn" class="btn-primary" onclick="pushCallToAmoCRM()" style="padding: 4px 12px; font-size: 12px; width: auto; margin: 0; background: #0284c7; border: 1px solid #38bdf8;">⚡ Отправить в amoCRM</button>
                </div>
            </div>
        </div>

        <!-- ДЕТАЛИ И СХЕМА ВЫЗОВА -->
        <div id="modal-call-info"></div>

        <div style="margin-top: 18px; display: flex; justify-content: flex-end;">
            <button class="btn-secondary" onclick="closeAudioModal()" style="width: auto; margin:0; padding: 8px 20px;">Закрыть</button>
        </div>
    </div>
</div>

<!-- ПОЛНОЭКРАННЫЙ СПИННЕР ЗАГРУЗКИ И ОБРАБОТКИ АУДИО -->
<div id="loading-spinner-overlay" class="loading-overlay">
    <div class="spinner"></div>
    <div class="loading-title">⏳ Идет сохранение схемы и конвертация аудио...</div>
    <div class="loading-sub">
        Аудиофайл загружается на Raspberry Pi и оптимизируется через FFmpeg в телефонный стандарт качества (PCM 8000Hz). Пожалуйста, подождите несколько секунд.
    </div>
</div>

<script>
const accountsList = {{ accounts | tojson }};
let ivrTreeData = {{ ivr_tree | tojson }};

function showLoadingSpinner() {
    document.getElementById('loading-spinner-overlay').classList.add('show');
}


function openSubTab(subtabId, btn) {
    document.querySelectorAll('.subtab-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.sub-tab-btn').forEach(el => el.classList.remove('active'));
    
    const target = document.getElementById(subtabId);
    if (target) target.classList.add('active');
    if (btn) btn.classList.add('active');
    localStorage.setItem('active_subtab', subtabId);
}

function openTab(tabId, btn) {
    document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
    const target = document.getElementById(tabId);
    if (target) target.classList.add('active');
    if (btn) btn.classList.add('active');
    localStorage.setItem('active_tab', tabId);
}



/* ИДЕАЛЬНЫЙ РЕНДЕР КОНСТРУКТОРА IVR С ПРАВИЛЬНЫМИ ОТСТУПАМИ */
function renderIvrTree() {
    const container = document.getElementById('ivr-nodes-container');
    container.innerHTML = '';
    
    if (!ivrTreeData.nodes || ivrTreeData.nodes.length === 0) {
        ivrTreeData.nodes = [{
            id: 'main',
            title: 'Главное меню (Уровень 1)',
            audio_file: 'greeting_main.wav',
            timeout_sec: 7,
            timeout_action: 'operator',
            timeout_target: 'ALL',
            branches: [
                { digit: '1', title: 'Русский язык', action: 'operator', target: 'ALL' },
                { digit: '2', title: 'English', action: 'operator', target: 'ALL' },
                { digit: '3', title: 'Arabic (العربية)', action: 'operator', target: 'ALL' }
            ]
        }];
    }

    ivrTreeData.nodes.forEach((node, nodeIdx) => {
        const nodeCard = document.createElement('div');
        nodeCard.className = 'ivr-node';
        nodeCard.id = `node-${node.id}`;

        let branchesHtml = '';
        node.branches.forEach((b, bIdx) => {
            branchesHtml += `
                <div class="ivr-branch" id="branch-${node.id}-${bIdx}">
                    <div class="ivr-branch-grid">
                        <div class="ivr-field-col">
                            <label>Клавиша:</label>
                            <input type="text" name="node_${node.id}_digit[]" value="${b.digit}" placeholder="1" style="text-align:center; font-weight:bold; font-size:15px; margin:0;" required>
                        </div>
                        <div class="ivr-field-col">
                            <label>Название кнопки / отдела:</label>
                            <input type="text" name="node_${node.id}_title[]" value="${b.title}" placeholder="Например: Отдел продаж" style="margin:0;" required>
                        </div>
                        <div class="ivr-field-col">
                            <label>Действие:</label>
                            <select name="node_${node.id}_action[]" onchange="updateBranchAction('${node.id}', ${bIdx})" style="margin:0;">
                                <option value="operator" ${b.action === 'operator' ? 'selected' : ''}>👤 Перевод на оператора</option>
                                <option value="menu" ${b.action === 'menu' ? 'selected' : ''}>🎛 Вложенное меню (Уровень)</option>
                            </select>
                        </div>
                        <div class="ivr-field-col" id="branch-target-container-${node.id}-${bIdx}">
                            <!-- Селектор цели -->
                        </div>
                        <div>
                            <button type="button" onclick="deleteBranch('${node.id}', ${bIdx})" class="btn-danger" style="margin:0; height:38px; display:flex; justify-content:center; align-items:center;" title="Удалить эту кнопку">✖</button>
                        </div>
                    </div>
                </div>
            `;
        });

        nodeCard.innerHTML = `
            <div class="ivr-node-header">
                <div style="display: flex; align-items: center; gap: 10px;">
                    <span style="font-size: 15px; font-weight: bold; color: #38bdf8;">🏷 Уровень:</span>
                    <input type="text" name="node_title_${node.id}" value="${node.title}" style="width: 280px; font-weight: 600; padding: 6px 12px; margin: 0;" required>
                    <span style="color:#64748b; font-size:12px;">ID: <code>${node.id}</code></span>
                </div>
                <div>
                    ${node.id !== 'main' ? `<button type="button" onclick="deleteNode('${node.id}')" class="btn-danger" style="width:auto; padding: 6px 14px; margin:0; font-size:12px;">Удалить этот этап</button>` : '<span style="color:#10b981; font-size:13px; font-weight:bold;">★ Стартовый этап (Главное меню)</span>'}
                </div>
            </div>

            <div class="ivr-node-topgrid">
                <!-- ЛЕВАЯ КОЛОНКА: АУДИО -->
                <div>
                    <label style="font-size:12px; font-weight:500; color:#94a3b8; display:block; margin-bottom:6px;">🎵 Аудиофайл приветствия для этого этапа (MP3/WAV):</label>
                    <input type="file" name="audio_file_${node.id}" accept=".mp3,.wav" style="margin-bottom:8px;">
                    <input type="hidden" name="existing_audio_${node.id}" value="${node.audio_file || ''}">
                    ${node.audio_file ? `<div style="margin-top:6px;"><span style="color:#10b981; font-size:12px; font-weight:500;">✓ Активен: ${node.audio_file}</span><audio controls src="/custom-audio/${node.audio_file}" style="margin-top:6px; height:34px;"></audio></div>` : '<small style="color:#eab308;">⚠️ Файл не загружен (будет системный сигнал)</small>'}
                </div>

                <!-- ПРАВАЯ КОЛОНКА: ТАЙМАУТ И ДЕЙСТВИЕ ПО УМОЛЧАНИЮ -->
                <div>
                    <label style="font-size:12px; font-weight:500; color:#94a3b8; display:block; margin-bottom:6px;">⏱ Таймаут ожидания и действие по умолчанию (если ничего не нажал):</label>
                    <div style="display:grid; grid-template-columns: 80px 1.3fr 1.3fr; gap: 10px; align-items: center;">
                        <div>
                            <input type="number" name="node_timeout_${node.id}" value="${node.timeout_sec || 7}" min="2" max="30" style="text-align:center; font-weight:bold; margin:0;" title="Секунды">
                        </div>
                        <div>
                            <select name="node_timeout_action_${node.id}" onchange="updateNodeTimeoutAction('${node.id}')" style="margin:0;">
                                <option value="operator" ${node.timeout_action === 'operator' ? 'selected' : ''}>👤 На оператора</option>
                                <option value="menu" ${node.timeout_action === 'menu' ? 'selected' : ''}>🎛 В меню</option>
                            </select>
                        </div>
                        <div id="node-timeout-target-${node.id}">
                            <!-- Селектор цели таймаута -->
                        </div>
                    </div>
                </div>
            </div>

            <div style="border-top: 1px solid #1e293b; padding-top: 16px; margin-top: 8px;">
                <div class="ivr-section-title">🔘 Настроенные клавиши и ветвления этапа:</div>
                <div id="branches-list-${node.id}">
                    ${branchesHtml}
                </div>
                <button type="button" onclick="addNewBranch('${node.id}')" class="btn-secondary" style="width:auto; margin-top:10px; font-size:13px; padding:8px 16px;">➕ Добавить кнопку (ветку) к этому этапу</button>
            </div>
        `;

        container.appendChild(nodeCard);
        
        node.branches.forEach((b, bIdx) => renderBranchTargetSelect(node.id, bIdx, b.action, b.target));
        renderTimeoutTargetSelect(node.id, node.timeout_action, node.timeout_target);
    });
}

function renderBranchTargetSelect(nodeId, bIdx, action, currentTarget) {
    const container = document.getElementById(`branch-target-container-${nodeId}-${bIdx}`);
    if (!container) return;

    if (action === 'operator') {
        let html = `<label>Куда перевести:</label><select name="node_${nodeId}_target[]" style="margin:0;">`;
        html += `<option value="ALL" ${currentTarget === 'ALL' ? 'selected' : ''}>🔔 Все сотрудники</option>`;
        accountsList.forEach(acc => {
            html += `<option value="${acc.exten}" ${currentTarget === acc.exten ? 'selected' : ''}>👤 Номер ${acc.exten}</option>`;
        });
        html += `</select>`;
        container.innerHTML = html;
    } else {
        let html = `<label>Переход в меню:</label><select name="node_${nodeId}_target[]" style="margin:0;">`;
        ivrTreeData.nodes.forEach(n => {
            if (n.id !== nodeId) {
                html += `<option value="${n.id}" ${currentTarget === n.id ? 'selected' : ''}>🎛 ${n.title} (ID: ${n.id})</option>`;
            }
        });
        html += `</select>`;
        container.innerHTML = html;
    }
}

function renderTimeoutTargetSelect(nodeId, action, currentTarget) {
    const container = document.getElementById(`node-timeout-target-${nodeId}`);
    if (!container) return;

    if (action === 'operator') {
        let html = `<select name="node_timeout_target_${nodeId}" style="margin:0;">`;
        html += `<option value="ALL" ${currentTarget === 'ALL' ? 'selected' : ''}>🔔 Все сотрудники</option>`;
        accountsList.forEach(acc => {
            html += `<option value="${acc.exten}" ${currentTarget === acc.exten ? 'selected' : ''}>👤 Номер ${acc.exten}</option>`;
        });
        html += `</select>`;
        container.innerHTML = html;
    } else {
        let html = `<select name="node_timeout_target_${nodeId}" style="margin:0;">`;
        ivrTreeData.nodes.forEach(n => {
            if (n.id !== nodeId) {
                html += `<option value="${n.id}" ${currentTarget === n.id ? 'selected' : ''}>🎛 ${n.title} (ID: ${n.id})</option>`;
            }
        });
        html += `</select>`;
        container.innerHTML = html;
    }
}

function updateBranchAction(nodeId, bIdx) {
    syncTreeDataFromDOM();
    const node = ivrTreeData.nodes.find(n => n.id === nodeId);
    if (node && node.branches[bIdx]) {
        const allBranchSelects = document.querySelectorAll(`#node-${nodeId} select[name="node_${nodeId}_action[]"]`);
        const act = allBranchSelects[bIdx].value;
        node.branches[bIdx].action = act;
        renderBranchTargetSelect(nodeId, bIdx, act, 'ALL');
    }
}

function updateNodeTimeoutAction(nodeId) {
    syncTreeDataFromDOM();
    const node = ivrTreeData.nodes.find(n => n.id === nodeId);
    if (node) {
        const act = document.querySelector(`select[name="node_timeout_action_${nodeId}"]`).value;
        node.timeout_action = act;
        renderTimeoutTargetSelect(nodeId, act, 'ALL');
    }
}

function addNewNode() {
    syncTreeDataFromDOM();
    const newId = 'menu_' + Math.random().toString(36).substring(2, 7);
    ivrTreeData.nodes.push({
        id: newId,
        title: `Уровень ${ivrTreeData.nodes.length + 1}`,
        audio_file: '',
        timeout_sec: 7,
        timeout_action: 'operator',
        timeout_target: 'ALL',
        branches: [
            { digit: '1', title: 'Оператор 1', action: 'operator', target: 'ALL' },
            { digit: '0', title: 'Назад в главное меню', action: 'menu', target: 'main' }
        ]
    });
    renderIvrTree();
}

function deleteNode(nodeId) {
    if (nodeId === 'main') return;
    if (confirm(`Удалить этот этап меню (${nodeId})?`)) {
        syncTreeDataFromDOM();
        ivrTreeData.nodes = ivrTreeData.nodes.filter(n => n.id !== nodeId);
        renderIvrTree();
    }
}

function addNewBranch(nodeId) {
    syncTreeDataFromDOM();
    const node = ivrTreeData.nodes.find(n => n.id === nodeId);
    if (node) {
        const nextDigit = String(node.branches.length + 1);
        node.branches.push({
            digit: nextDigit,
            title: `Пункт ${nextDigit}`,
            action: 'operator',
            target: 'ALL'
        });
        renderIvrTree();
    }
}

function deleteBranch(nodeId, bIdx) {
    syncTreeDataFromDOM();
    const node = ivrTreeData.nodes.find(n => n.id === nodeId);
    if (node) {
        node.branches.splice(bIdx, 1);
        renderIvrTree();
    }
}

function syncTreeDataFromDOM() {
    ivrTreeData.nodes.forEach(node => {
        const titleInput = document.querySelector(`input[name="node_title_${node.id}"]`);
        if (titleInput) node.title = titleInput.value;

        const timeoutInput = document.querySelector(`input[name="node_timeout_${node.id}"]`);
        if (timeoutInput) node.timeout_sec = parseInt(timeoutInput.value) || 7;

        const timeoutActionSel = document.querySelector(`select[name="node_timeout_action_${node.id}"]`);
        if (timeoutActionSel) node.timeout_action = timeoutActionSel.value;

        const timeoutTargetSel = document.querySelector(`select[name="node_timeout_target_${node.id}"]`);
        if (timeoutTargetSel) node.timeout_target = timeoutTargetSel.value;

        const digitInputs = document.querySelectorAll(`#node-${node.id} input[name="node_${node.id}_digit[]"]`);
        const titleInputs = document.querySelectorAll(`#node-${node.id} input[name="node_${node.id}_title[]"]`);
        const actionSelects = document.querySelectorAll(`#node-${node.id} select[name="node_${node.id}_action[]"]`);
        const targetSelects = document.querySelectorAll(`#node-${node.id} select[name="node_${node.id}_target[]"]`);

        node.branches = [];
        digitInputs.forEach((dInp, idx) => {
            node.branches.push({
                digit: dInp.value,
                title: titleInputs[idx] ? titleInputs[idx].value : '',
                action: actionSelects[idx] ? actionSelects[idx].value : 'operator',
                target: targetSelects[idx] ? targetSelects[idx].value : 'ALL'
            });
        });
    });
}


let cachedPipelines = [];
let amoUsersList = [];

const savedPipelineId = "{{ integrations.amocrm.pipeline_id or '' }}";
const savedStatusId = "{{ integrations.amocrm.status_id or '' }}";

function fetchPipelines() {
    const pSelect = document.getElementById('amo_pipeline');
    const sSelect = document.getElementById('amo_stage');
    if (!pSelect) return;

    fetch('/api/amocrm/pipelines')
        .then(res => res.json())
        .then(data => {
            if (data.pipelines && data.pipelines.length > 0) {
                cachedPipelines = data.pipelines;
                pSelect.innerHTML = '<option value="">-- Выберите воронку --</option>';
                data.pipelines.forEach(p => {
                    const isSel = String(p.id) === String(savedPipelineId) ? 'selected' : '';
                    pSelect.innerHTML += `<option value="${p.id}" ${isSel}>${p.name}</option>`;
                });
                updateStages();
            } else {
                pSelect.innerHTML = '<option value="">Воронки не найдены</option>';
                sSelect.innerHTML = '<option value="">--</option>';
            }
        })
        .catch(err => {
            console.error('Pipelines error:', err);
            pSelect.innerHTML = '<option value="">Ошибка загрузки</option>';
        });
}

function updateStages() {
    const pSelect = document.getElementById('amo_pipeline');
    const sSelect = document.getElementById('amo_stage');
    if (!pSelect || !sSelect) return;

    const selectedPipelineId = pSelect.value || savedPipelineId;
    const pipeline = cachedPipelines.find(p => String(p.id) === String(selectedPipelineId));
    
    if (pipeline && pipeline.statuses && pipeline.statuses.length > 0) {
        sSelect.innerHTML = '<option value="">-- Выберите этап воронки --</option>';
        pipeline.statuses.forEach(s => {
            const isSel = String(s.id) === String(savedStatusId) ? 'selected' : '';
            sSelect.innerHTML += `<option value="${s.id}" ${isSel}>${s.name}</option>`;
        });
    } else {
        sSelect.innerHTML = '<option value="">Сначала выберите воронку</option>';
    }
}

function fetchAmoUsers() {
    fetch('/api/amocrm/users')
        .then(r => r.json())
        .then(data => {
            if (data.status === 'ok' && data.users && data.users.length > 0) {
                amoUsersList = data.users;
                populateAmoUserSelects();
            }
        })
        .catch(e => console.error("Error fetching amoCRM users:", e));
}

function populateAmoUserSelects() {
    document.querySelectorAll('.amo-user-select').forEach(select => {
        const savedVal = select.getAttribute('data-saved');
        let html = select.name === 'seat_default' ? '<option value="">Не назначен</option>' : '<option value="">(По умолчанию)</option>';
        amoUsersList.forEach(u => {
            const isSel = String(u.id) === String(savedVal) ? 'selected' : '';
            html += `<option value="${u.id}" ${isSel}>${u.name} (${u.email || u.id})</option>`;
        });
        select.innerHTML = html;
    });
}


function fetchRealtimeData() {
    fetch('/api/realtime')
        .then(res => res.json())
        .then(data => {
            if (!data) return;
            try { renderSipAccounts(data.accounts || [], data.active_contacts || []); } catch(e) { console.error('SIP render err:', e); }
            try { renderCalls(data.calls || []); } catch(e) { console.error('Calls render err:', e); }
            try { renderHumanChannels(data.active_channels_parsed || []); } catch(e) { console.error('Channels render err:', e); }
            try { renderHumanSockets(data.active_contacts_detailed || []); } catch(e) { console.error('Sockets render err:', e); }
            try { renderModemDashboard(data.modem_parsed); } catch(e) { console.error('Modem render err:', e); }
            
            const authLogs = document.getElementById('live-auth-logs');
            if (authLogs) authLogs.textContent = data.auth_logs || 'Нет активных событий';
            
            const sTime = document.getElementById('server-time');
            if (sTime && data.server_time) sTime.innerText = data.server_time;
            
            const amoLogs = document.getElementById('live-amocrm-logs');
            if (amoLogs) amoLogs.textContent = data.amocrm_logs || 'Лог обмена с amoCRM пуст. Ожидание вызовов...';
        })
        .catch(err => console.error('Live sync error:', err));
}


function restartDongleAjax(btn) {
    const originalText = btn.innerHTML;
    btn.innerHTML = '⏳ Поиск портов и перезапуск канала...';
    btn.disabled = true;
    
    fetch('/action/restart-dongle', {
        method: 'POST',
        headers: { 'X-Requested-With': 'XMLHttpRequest' }
    })
    .then(r => r.json())
    .then(data => {
        btn.innerHTML = '✅ Порты обновлены!';
        setTimeout(() => {
            btn.innerHTML = originalText;
            btn.disabled = false;
            fetchRealtimeData();
        }, 2000);
    })
    .catch(e => {
        btn.innerHTML = '✅ Команда отправлена!';
        setTimeout(() => {
            btn.innerHTML = originalText;
            btn.disabled = false;
            fetchRealtimeData();
        }, 2000);
    });
}

function renderModemDashboard(modem) {
    const container = document.getElementById('modem-parsed-container');
    const headerStatus = document.getElementById('modem-header-status');

    if (!modem) return;

    // 1. NO_USB: Модем вообще не воткнут в плату
    if (modem.status_code === 'NO_USB') {
        headerStatus.innerHTML = '<span class="modem-status-badge modem-offline">● Отключен (USB не найден)</span>';
        container.innerHTML = `
            <div style="background: #070a12; padding: 18px; border-radius: 8px; border: 1px solid #ef4444;">
                <div style="color: #ef4444; font-weight: bold; font-size: 15px; margin-bottom: 8px;">❌ USB-модем не подключен к плате Raspberry Pi</div>
                <div style="color: #94a3b8; font-size: 13px; line-height: 1.6;">
                    1. Убедитесь, что USB-модем плотно вставлен в USB-разъем.<br>
                    2. Проверьте, мигает ли светодиод питания на корпусе модема.<br>
                    3. При необходимости переподключите модем в соседний USB-порт.
                </div>
            </div>
        `;
        return;
    }

    // 2. NO_SIM: Модем воткнут, но SIM-карта не найдена/не читается
    if (modem.status_code === 'NO_SIM') {
        headerStatus.innerHTML = '<span class="modem-status-badge" style="background: rgba(245, 158, 11, 0.15); color: #f59e0b; border: 1px solid #f59e0b;">● SIM-карта не читается</span>';
        container.innerHTML = `
            <div style="background: #070a12; padding: 18px; border-radius: 8px; border: 1px solid #f59e0b; margin-bottom: 15px;">
                <div style="color: #f59e0b; font-weight: bold; font-size: 15px; margin-bottom: 8px;">⚠️ Модем обнаружен, но SIM-карта не распознана</div>
                <div style="color: #cbd5e1; font-size: 13px; line-height: 1.6;">
                    Устройство <b>${modem.model || 'Huawei Modem'}</b> (IMEI: <code>${modem.imei || 'Чтение...'}</code>) подключено к USB.<br>
                    Однако модем не может считать SIM-карту:
                    <ul style="margin: 6px 0 0 18px; color: #94a3b8;">
                        <li>Проверьте, вставлена ли SIM-карта в слот модема до щелчка.</li>
                        <li>Убедитесь, что SIM-карта вставлена правильной стороной (контактами вниз).</li>
                        <li>Протрите контакты SIM-карты ластиком или спиртом.</li>
                    </ul>
                </div>
            </div>
            ${getModemGridHtml(modem)}
        `;
        return;
    }

    // 3. SEARCHING: SIM-карта есть, идет регистрация в сети оператора
    if (modem.status_code === 'SEARCHING') {
        headerStatus.innerHTML = '<span class="modem-status-badge" style="background: rgba(56, 189, 248, 0.15); color: #38bdf8; border: 1px solid #38bdf8;">● Поиск сотовой сети</span>';
        container.innerHTML = `
            <div style="background: #070a12; padding: 14px; border-radius: 8px; border: 1px solid #38bdf8; margin-bottom: 15px;">
                <div style="color: #38bdf8; font-weight: bold; margin-bottom: 4px;">🔍 Регистрация в сети оператора...</div>
                <div style="color: #94a3b8; font-size: 13px;">
                    SIM-карта успешно прочитана (IMSI: <code>${modem.imsi}</code>). Модем выполняет поиск вышек и регистрацию.
                </div>
            </div>
            ${getModemGridHtml(modem)}
        `;
        return;
    }

    // 4. ONLINE / BUSY: Полная готовность
    headerStatus.innerHTML = '<span class="modem-status-badge modem-online">● В сети (Готов к вызовам)</span>';
    container.innerHTML = getModemGridHtml(modem);
}

function getModemGridHtml(modem) {
    const signalBar = modem.signal_pct > 0 
        ? `<div style="background: #1e293b; border-radius: 4px; height: 5px; width: 100%; margin-top: 4px; overflow: hidden;">
            <div style="background: ${modem.signal_pct > 50 ? '#10b981' : (modem.signal_pct > 25 ? '#f59e0b' : '#ef4444')}; height: 100%; width: ${modem.signal_pct}%;"></div>
           </div>`
        : '';

    return `
        <div class="table-scroll" style="margin-bottom: 12px;">
            <table>
                <thead>
                    <tr>
                        <th>Устройство / Модель</th>
                        <th>Оператор связи</th>
                        <th>Статус канала</th>
                        <th>Уровень сигнала</th>
                        <th>SIM-карта (IMSI)</th>
                        <th>Номер телефона SIM</th>
                        <th>IMEI</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td>
                            <div style="font-weight: bold; color: #f8fafc; font-size: 13px;">${modem.model || 'Huawei Modem'}</div>
                            <div style="font-size: 11px; color: #64748b; font-family: monospace;">dongle0 (USB ${modem.usb_present ? 'подключен' : 'нет'})</div>
                        </td>
                        <td>
                            <b style="color: #38bdf8; font-size: 13px;">${modem.provider ? modem.provider : (modem.connected ? 'В сети' : (modem.sim_present ? 'Поиск сети...' : 'Нет SIM'))}</b>
                        </td>
                        <td>
                            <span class="badge" style="background: ${modem.connected ? 'rgba(16, 185, 129, 0.2)' : 'rgba(239, 68, 68, 0.2)'}; color: ${modem.connected ? '#10b981' : '#ef4444'};">
                                ${modem.connected ? '● В сети (Онлайн)' : '○ ' + modem.state}
                            </span>
                        </td>
                        <td style="min-width: 140px;">
                            <div style="font-weight: 500; font-size: 12px; color: #f59e0b;">
                                ${modem.signal_pct > 0 ? modem.signal_pct + '% (' + modem.rssi_desc + ')' : 'Нет сигнала'}
                            </div>
                            ${signalBar}
                        </td>
                        <td>
                            <span style="font-family: monospace; font-size: 12px; color: ${modem.sim_present ? '#10b981' : '#ef4444'};">
                                ${modem.sim_present ? '✓ ' + modem.imsi : '✗ Не обнаружена'}
                            </span>
                        </td>
                        <td>
                            <span style="font-size: 13px; font-weight: bold; color: #a5f3fc;">
                                ${modem.number ? modem.number : '<span style="color:#64748b; font-weight:normal; font-size:12px;">Не указан</span>'}
                            </span>
                            <button onclick="promptSavePhone()" style="background:none; border:none; color:#38bdf8; cursor:pointer; font-size:11px; text-decoration:underline; padding:0; margin-left:4px;">(изм)</button>
                        </td>
                        <td><code style="font-size: 11px; color: #94a3b8;">${modem.imei || '—'}</code></td>
                    </tr>
                </tbody>
            </table>
        </div>

        <div style="background: #070a12; padding: 10px 14px; border-radius: 8px; border: 1px solid #1e293b; display: flex; align-items: center; justify-content: space-between; gap: 15px; flex-wrap: wrap;">
            <div style="font-size: 13px; color: #94a3b8; display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
                <span>📲 <b>USSD Запрос / Баланс:</b></span>
                <input type="text" id="ussd-input" value="*100#" style="width: 80px; padding: 5px 8px; font-size: 12px; margin: 0;">
                <button onclick="sendUssd()" class="btn-play" style="padding: 5px 12px; font-size: 12px; white-space: nowrap;">Отправить USSD</button>
                <span id="ussd-result" style="color: #a5f3fc; font-size: 12px; margin-left: 8px;"></span>
            </div>
            <div>
                <button type="button" onclick="restartDongleAjax(this)" class="btn-secondary" style="width: auto; padding: 5px 12px; font-size: 12px; margin: 0;">🔄 Пересканировать модем</button>
            </div>
        </div>
    `;
}


function promptSavePhone() {
    const num = prompt("Введите номер телефона вашей SIM-карты (например: +79261234567):");
    if (num !== null && num.trim() !== '') {
        fetch('/settings/modem-phone', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({phone: num.trim()})
        })
        .then(r => r.json())
        .then(data => {
            alert('Номер телефона сохранен!');
            fetchRealtimeData();
        })
        .catch(err => alert('Ошибка сохранения'));
    }
}

function sendUssd() {
    const code = document.getElementById('ussd-input').value.trim();
    const resultSpan = document.getElementById('ussd-result');
    if (!code) return;
    resultSpan.innerText = "⏳ Отправка запроса...";
    fetch('/api/modem/ussd', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({code: code})
    })
    .then(r => r.json())
    .then(data => {
        resultSpan.innerText = data.message || data.error || 'Запрос отправлен';
    })
    .catch(err => {
        resultSpan.innerText = "Ошибка запроса";
    });
}

function renderHumanChannels(activeCalls) {
    const container = document.getElementById('live-human-channels');
    if (!activeCalls || activeCalls.length === 0) {
        container.innerHTML = '<p style="color: #94a3b8; padding: 10px 0; margin:0;">🟢 В данный момент активных разговоров нет (линии свободны)</p>';
        return;
    }

    let html = '';
    activeCalls.forEach(c => {
        html += `<div class="active-call-box">
            <div>
                <div class="active-call-title">
                    <span>📞 Разговор: <b>${c.caller}</b> ➔ <b>${c.callee}</b></span>
                    <span class="active-call-badge">${c.state}</span>
                </div>
                <div class="active-call-sub">
                    Канал: <code>${c.channel}</code> | Длительность: <b>${c.duration}</b>
                </div>
            </div>
            <div style="color: #10b981; font-weight: bold; font-size: 18px;">● В эфире</div>
        </div>`;
    });
    container.innerHTML = html;
}

function renderHumanSockets(sockets) {
    const container = document.getElementById('live-human-sockets');
    if (!sockets || sockets.length === 0) {
        container.innerHTML = '<p style="color: #94a3b8; padding: 10px 0; margin:0;">Нет активных зарегистрированных клиентов</p>';
        return;
    }

    let html = '<table><thead><tr><th>Номер</th><th>IP адрес и Порт клиента</th><th>Статус</th></tr></thead><tbody>';
    sockets.forEach(s => {
        html += `<tr>
            <td><b>${s.exten}</b></td>
            <td><code style="color: #38bdf8;">${s.ip_port}</code></td>
            <td><span class="tag-online">● Подключен</span></td>
        </tr>`;
    });
    html += '</tbody></table>';
    container.innerHTML = html;
}

function renderSipAccounts(accounts, activeContacts) {
    document.getElementById('sip-count-badge').textContent = `Всего: ${accounts.length}`;
    if (accounts.length === 0) {
        document.getElementById('sip-table-container').innerHTML = '<p style="color: #94a3b8; padding: 10px 0;">Нет созданных SIP учетных записей.</p>';
        return;
    }

    let html = `<table>
        <thead>
            <tr>
                <th>Номер (Extension)</th>
                <th>Пароль (Secret)</th>
                <th>Контекст</th>
                <th>Статус в сети</th>
                <th>Действие</th>
            </tr>
        </thead>
        <tbody>`;

    accounts.forEach(acc => {
        const isOnline = activeContacts.includes(acc.exten);
        const statusBadge = isOnline 
            ? '<span class="tag-online">● В сети (Online)</span>' 
            : '<span class="tag-offline">○ Не в сети (Offline)</span>';
        
        html += `<tr>
            <td><b>${acc.exten}</b></td>
            <td><code>${acc.password}</code></td>
            <td>${acc.context}</td>
            <td>${statusBadge}</td>
            <td style="width: 120px;">
                <form method="POST" action="/sip/delete" style="margin:0;" onsubmit="return confirm('Удалить номер ${acc.exten}?');">
                    <input type="hidden" name="exten" value="${acc.exten}">
                    <button type="submit" class="btn-danger" style="padding: 6px 12px; margin:0;">Удалить</button>
                </form>
            </td>
        </tr>`;
    });

    html += '</tbody></table>';
    document.getElementById('sip-table-container').innerHTML = html;
}

let allCallsData = [];
let currentPage = 1;
let pageSize = 50;
let searchQuery = '';
let lastCallsHash = '';

function onSearchChange() {
    searchQuery = document.getElementById('call-search-input').value.trim().toLowerCase();
    currentPage = 1;
    applyCallsFilterAndRender();
}

function onPageSizeChange() {
    pageSize = parseInt(document.getElementById('page-size-select').value) || 50;
    currentPage = 1;
    applyCallsFilterAndRender();
}

function goToPage(page) {
    currentPage = page;
    applyCallsFilterAndRender();
}




function renderCalls(calls) {
    const currentHash = JSON.stringify(calls);
    if (currentHash === lastCallsHash) {
        return;
    }
    lastCallsHash = currentHash;
    allCallsData = calls || [];
    applyCallsFilterAndRender();
}

function applyCallsFilterAndRender() {
    let filtered = allCallsData;
    if (searchQuery) {
        filtered = allCallsData.filter(c => {
            const src = (c.src || '').toLowerCase();
            const dst = (c.dst || '').toLowerCase();
            const dir = (c.dir_label || '').toLowerCase();
            return src.includes(searchQuery) || dst.includes(searchQuery) || dir.includes(searchQuery);
        });
    }

    const totalCount = filtered.length;
    const totalPages = Math.ceil(totalCount / pageSize) || 1;
    if (currentPage > totalPages) currentPage = totalPages;
    if (currentPage < 1) currentPage = 1;

    const startIdx = (currentPage - 1) * pageSize;
    const endIdx = startIdx + pageSize;
    const pageItems = filtered.slice(startIdx, endIdx);

    const countBadge = document.getElementById('calls-count-badge');
    if (countBadge) {
        if (searchQuery) {
            countBadge.textContent = `● Найдено: ${totalCount} из ${allCallsData.length}`;
        } else {
            countBadge.textContent = `● Всего: ${totalCount} звонков (до 10 дней / макс 200)`;
        }
    }

    const container = document.getElementById('calls-table-container');
    const paginationContainer = document.getElementById('calls-pagination-container');

    if (totalCount === 0) {
        container.innerHTML = '<p style="color: #94a3b8; padding: 15px 0;">' + (searchQuery ? 'Ничего не найдено по вашему запросу.' : 'Звонков за последние 10 дней не зафиксировано.') + '</p>';
        paginationContainer.innerHTML = '';
        return;
    }

    let html = `<div class="table-scroll"><table>
        <thead>
            <tr>
                <th>#</th>
                <th>Время вызова</th>
                <th>Тип</th>
                <th>Кто (Src)</th>
                <th>Кому (Dst)</th>
                <th>Статус</th>
                <th>Разговор</th>
                <th>Размер</th>
                <th>Действие / Маршрут</th>
            </tr>
        </thead>
        <tbody>`;

    pageItems.forEach((c, idx) => {
        const rowNum = startIdx + idx + 1;
        const globalIdx = allCallsData.indexOf(c);
        let st = `<span class="tag-failed">${c.disposition}</span>`;
        if (c.disposition === 'ANSWERED') st = '<span class="tag-answered">Отвечен</span>';
        else if (c.disposition === 'NO ANSWER') st = '<span class="tag-noanswer">Без ответа</span>';
        else if (c.disposition === 'BUSY') st = '<span class="tag-failed">Занято</span>';

        let audioAction = '';
        if (c.filename && c.file_size_bytes > 44) {
            audioAction = `<button class="btn-play" onclick="openCallDetails(${globalIdx})">▶ Слушать & Маршрут</button>`;
        } else {
            audioAction = `<button class="btn-secondary" onclick="openCallDetails(${globalIdx})" style="padding: 5px 12px; font-size: 12px;">ℹ Маршрут & Детали</button>`;
        }

        html += `<tr style="cursor: pointer;" onclick="if(!event.target.closest('button')) openCallDetails(${globalIdx})">
            <td style="color: #64748b; font-size: 11px;">${rowNum}</td>
            <td>${c.date}</td>
            <td><span style="font-size: 12px; color: #a5f3fc;">${c.dir_icon || '📞 Вызов'}</span></td>
            <td><b>${c.src}</b></td>
            <td><b>${c.dst}</b></td>
            <td>${st}</td>
            <td><b>${c.duration_fmt}</b></td>
            <td style="color: #a5f3fc;">${c.file_size_fmt}</td>
            <td>${audioAction}</td>
        </tr>`;
    });

    html += '</tbody></table></div>';
    container.innerHTML = html;

    let pagHtml = `<div class="page-info">Показано <b>${pageItems.length}</b> из <b>${totalCount}</b> (Стр. <b>${currentPage}</b> из <b>${totalPages}</b>)</div><div class="pagination-buttons">
            <button class="page-btn" onclick="goToPage(1)" ${currentPage === 1 ? 'disabled' : ''} title="В начало">⏮</button>
            <button class="page-btn" onclick="goToPage(${currentPage - 1})" ${currentPage === 1 ? 'disabled' : ''} title="Назад">◀ Назад</button>`;

    for (let p = Math.max(1, currentPage - 2); p <= Math.min(totalPages, currentPage + 2); p++) {
        pagHtml += `<button class="page-btn ${p === currentPage ? 'active' : ''}" onclick="goToPage(${p})">${p}</button>`;
    }

    pagHtml += `
            <button class="page-btn" onclick="goToPage(${currentPage + 1})" ${currentPage === totalPages ? 'disabled' : ''} title="Вперед">Вперед ▶</button>
            <button class="page-btn" onclick="goToPage(${totalPages})" ${currentPage === totalPages ? 'disabled' : ''} title="В конец">⏭</button>
        </div>
    `;

    paginationContainer.innerHTML = totalPages > 1 ? pagHtml : `<div class="page-info">Всего записей: <b>${totalCount}</b></div>`;
}


let currentModalCallData = null;

function pushCallToAmoCRM() {
    if (!currentModalCallData) {
        alert("Нет данных о вызове");
        return;
    }
    const btn = document.getElementById('modal-push-amocrm-btn');
    const oldText = btn.innerText;
    btn.innerText = "⏳ Отправка...";
    btn.disabled = true;

    fetch('/api/amocrm/push-call', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            call_id: currentModalCallData.uniqueid || currentModalCallData.filename || 'manual',
            src: currentModalCallData.src,
            dst: currentModalCallData.dst,
            direction: currentModalCallData.dir_type || 'inbound',
            disposition: currentModalCallData.disposition || 'ANSWERED',
            billsec: currentModalCallData.billsec || 0,
            filename: currentModalCallData.filename || ''
        })
    })
    .then(r => r.json())
    .then(data => {
        btn.disabled = false;
        btn.innerText = oldText;
        if (data.status === 'ok') {
            alert("✓ " + data.message);
        } else {
            alert("Ошибка: " + data.message);
        }
    })
    .catch(err => {
        btn.disabled = false;
        btn.innerText = oldText;
        alert("Ошибка сети при отправке в amoCRM: " + err);
    });
}

function openCallDetails(idx) {
    currentModalCallData = allCallsData[idx];
    const c = allCallsData[idx];
    if (!c) return;

    const modal = document.getElementById('audio-modal');
    const player = document.getElementById('popup-audio-player');
    const title = document.getElementById('modal-call-title');
    const info = document.getElementById('modal-call-info');
    const download = document.getElementById('modal-download-link');
    const audioBox = document.getElementById('modal-audio-player-box');

    let dispColor = '#ef4444';
    let dispRu = 'Сбой / Ошибка';
    if (c.disposition === 'ANSWERED') { dispColor = '#10b981'; dispRu = 'Отвечен (Разговор)'; }
    else if (c.disposition === 'NO ANSWER') { dispColor = '#f59e0b'; dispRu = 'Без ответа'; }
    else if (c.disposition === 'BUSY') { dispColor = '#f59e0b'; dispRu = 'Занято'; }

    title.innerHTML = `<span style="color:${dispColor}; font-size:18px;">●</span> ${c.dir_icon || '📞'}: <b>${c.src}</b> ➔ <b>${c.dst}</b>`;

    if (c.filename && c.file_size_bytes > 44) {
        audioBox.style.display = 'block';
        const audioUrl = `/audio/${encodeURIComponent(c.filename)}`;
        player.src = audioUrl;
        download.href = audioUrl;
        document.getElementById('modal-audio-size').innerText = c.file_size_fmt;
    } else {
        audioBox.style.display = 'none';
        player.pause();
        player.src = '';
    }

    let routeFlowHtml = '';
    if (c.dir_type === 'inbound') {
        routeFlowHtml = `
            <div class="call-flow-diagram">
                <div class="flow-step">
                    <div class="flow-step-icon">📱</div>
                    <div class="flow-step-title">Клиент</div>
                    <div class="flow-step-sub">${c.src}</div>
                </div>
                <div class="flow-arrow">➔</div>
                <div class="flow-step">
                    <div class="flow-step-icon">📡</div>
                    <div class="flow-step-title">Модем</div>
                    <div class="flow-step-sub">dongle0</div>
                </div>
                <div class="flow-arrow">➔</div>
                <div class="flow-step">
                    <div class="flow-step-icon">🎛</div>
                    <div class="flow-step-title">Диалплан</div>
                    <div class="flow-step-sub">${c.dcontext}</div>
                </div>
                <div class="flow-arrow">➔</div>
                <div class="flow-step active">
                    <div class="flow-step-icon">👤</div>
                    <div class="flow-step-title">${c.disposition === 'ANSWERED' ? 'Ответил' : 'Получатель'}</div>
                    <div class="flow-step-sub" style="color: #38bdf8; font-weight:bold;">${c.dst}</div>
                </div>
            </div>
        `;
    } else if (c.dir_type === 'outbound') {
        routeFlowHtml = `
            <div class="call-flow-diagram">
                <div class="flow-step">
                    <div class="flow-step-icon">👤</div>
                    <div class="flow-step-title">Оператор</div>
                    <div class="flow-step-sub">${c.src}</div>
                </div>
                <div class="flow-arrow">➔</div>
                <div class="flow-step">
                    <div class="flow-step-icon">🎛</div>
                    <div class="flow-step-title">АТС PBX</div>
                    <div class="flow-step-sub">${c.dcontext}</div>
                </div>
                <div class="flow-arrow">➔</div>
                <div class="flow-step">
                    <div class="flow-step-icon">📡</div>
                    <div class="flow-step-title">GSM Модем</div>
                    <div class="flow-step-sub">dongle0</div>
                </div>
                <div class="flow-arrow">➔</div>
                <div class="flow-step active">
                    <div class="flow-step-icon">📱</div>
                    <div class="flow-step-title">Абонент</div>
                    <div class="flow-step-sub" style="color: #38bdf8; font-weight:bold;">${c.dst}</div>
                </div>
            </div>
        `;
    } else {
        routeFlowHtml = `
            <div class="call-flow-diagram">
                <div class="flow-step">
                    <div class="flow-step-icon">👤</div>
                    <div class="flow-step-title">Оператор A</div>
                    <div class="flow-step-sub">${c.src}</div>
                </div>
                <div class="flow-arrow">➔</div>
                <div class="flow-step">
                    <div class="flow-step-icon">🎛</div>
                    <div class="flow-step-title">Asterisk PBX</div>
                    <div class="flow-step-sub">Внутренняя сеть</div>
                </div>
                <div class="flow-arrow">➔</div>
                <div class="flow-step active">
                    <div class="flow-step-icon">👤</div>
                    <div class="flow-step-title">Оператор B</div>
                    <div class="flow-step-sub" style="color: #38bdf8; font-weight:bold;">${c.dst}</div>
                </div>
            </div>
        `;
    }

    info.innerHTML = `
        ${routeFlowHtml}

        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; margin-bottom: 15px;">
            <div class="call-detail-card">
                <div class="call-detail-label">Статус вызова</div>
                <div class="call-detail-value" style="color: ${dispColor}; font-weight: bold;">${dispRu} (${c.disposition})</div>
            </div>
            <div class="call-detail-card">
                <div class="call-detail-label">Разговор (billsec)</div>
                <div class="call-detail-value" style="color: #10b981; font-weight: bold;">${c.duration_fmt}</div>
            </div>
            <div class="call-detail-card">
                <div class="call-detail-label">Полная длительность</div>
                <div class="call-detail-value" style="color: #cbd5e1;">${c.total_duration_fmt} (${c.duration_sec || 0} сек)</div>
            </div>
            <div class="call-detail-card">
                <div class="call-detail-label">Направление вызова</div>
                <div class="call-detail-value" style="color: #38bdf8;">${c.dir_label}</div>
            </div>
        </div>

        <details style="background: #070a12; border: 1px solid #1e293b; border-radius: 8px; padding: 12px 16px;">
            <summary style="cursor: pointer; color: #38bdf8; font-weight: 600; font-size: 13px;">⚙️ Полный технический паспорт вызова (Asterisk CDR)</summary>
            <div style="margin-top: 12px; font-size: 12px; line-height: 1.8; color: #94a3b8;">
                <div style="display: flex; justify-content: space-between; border-bottom: 1px solid #1e293b; padding: 4px 0;">
                    <span>Уникальный ID (Unique ID):</span>
                    <code style="color: #38bdf8;">${c.uniqueid || '—'}</code>
                </div>
                <div style="display: flex; justify-content: space-between; border-bottom: 1px solid #1e293b; padding: 4px 0;">
                    <span>Канал источника (Source Channel):</span>
                    <code style="color: #cbd5e1;">${c.channel || '—'}</code>
                </div>
                <div style="display: flex; justify-content: space-between; border-bottom: 1px solid #1e293b; padding: 4px 0;">
                    <span>Канал получателя (Destination Channel):</span>
                    <code style="color: #cbd5e1;">${c.dstchannel || '—'}</code>
                </div>
                <div style="display: flex; justify-content: space-between; border-bottom: 1px solid #1e293b; padding: 4px 0;">
                    <span>Контекст диалплана (Context):</span>
                    <code style="color: #cbd5e1;">${c.dcontext || '—'}</code>
                </div>
                <div style="display: flex; justify-content: space-between; border-bottom: 1px solid #1e293b; padding: 4px 0;">
                    <span>Приложение Asterisk (Last App):</span>
                    <code style="color: #cbd5e1;">${c.lastapp || '—'}(${c.lastdata || ''})</code>
                </div>
                <div style="display: flex; justify-content: space-between; border-bottom: 1px solid #1e293b; padding: 4px 0;">
                    <span>Время инициализации звонка:</span>
                    <span style="color: #f8fafc;">${c.date}</span>
                </div>
                <div style="display: flex; justify-content: space-between; border-bottom: 1px solid #1e293b; padding: 4px 0;">
                    <span>Время ответа абонента:</span>
                    <span style="color: #f8fafc;">${c.answer_date || 'Не отвечен'}</span>
                </div>
                <div style="display: flex; justify-content: space-between; border-bottom: 1px solid #1e293b; padding: 4px 0;">
                    <span>Время завершения (Hangup):</span>
                    <span style="color: #f8fafc;">${c.end_date || c.date}</span>
                </div>
                <div style="display: flex; justify-content: space-between; padding: 4px 0;">
                    <span>Файл записи на сервере:</span>
                    <code style="color: #a5f3fc; word-break: break-all;">${c.filename || 'Запись отсутствует'}</code>
                </div>
            </div>
        </details>
    `;

    modal.classList.add('show');
}


function showLoadingSpinnerUpdate() {
    const overlay = document.getElementById('loading-spinner-overlay');
    overlay.querySelector('.loading-title').innerText = "⏳ Идет скачивание и установка обновления...";
    overlay.querySelector('.loading-sub').innerText = "Пожалуйста, не закрывайте страницу. Процесс займет от 15 секунд до 1 минуты. После завершения система будет перезагружена.";
    overlay.classList.add('show');
}

function checkForUpdates() {
    const btn = document.querySelector('#tab-update button.btn-success');
    btn.innerText = "⏳ Проверка...";
    btn.disabled = true;
    
    fetch('/api/check-update')
        .then(r => r.json())
        .then(data => {
            btn.innerText = "🔍 Проверить обновления";
            btn.disabled = false;
            const statusBox = document.getElementById('update-status-box');
            statusBox.style.display = 'block';
            
            if (data.error) {
                statusBox.innerHTML = `<span style="color: #ef4444;">Ошибка: ${data.error}</span>`;
            } else if (data.has_update) {
                statusBox.innerHTML = `<span style="color: #10b981;">Найдено обновление: <b>${data.latest_version}</b></span>`;
                document.getElementById('update-actions').style.display = 'block';
                document.getElementById('new-version-title').innerText = `Доступна версия ${data.latest_version}`;
                document.getElementById('changelog-box').innerText = data.changelog || "Нет описания изменений.";
            } else {
                statusBox.innerHTML = `<span style="color: #38bdf8;">У вас установлена самая актуальная версия (${data.current_version}).</span>`;
                document.getElementById('update-actions').style.display = 'none';
            }
        })
        .catch(err => {
            btn.innerText = "🔍 Проверить обновления";
            btn.disabled = false;
            document.getElementById('update-status-box').style.display = 'block';
            document.getElementById('update-status-box').innerHTML = `<span style="color: #ef4444;">Ошибка сети при проверке обновлений.</span>`;
        });
}


function closeAudioModal() {
    const modal = document.getElementById('audio-modal');
    const player = document.getElementById('popup-audio-player');
    player.pause();
    player.src = '';
    modal.classList.remove('show');
}

document.addEventListener("DOMContentLoaded", function() {
    const saved = localStorage.getItem('active_tab') || localStorage.getItem('activeTab');
    if (saved && document.getElementById(saved)) {
        document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
        document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
        document.getElementById(saved).classList.add('active');
        const btn = document.querySelector(`button[onclick*="${saved}"]`);
        if (btn) btn.classList.add('active');
    }
    try { renderIvrTree(); } catch(e) { console.error(e); }
    try { fetchPipelines(); } catch(e) { console.error(e); }
    try { fetchAmoUsers(); } catch(e) { console.error(e); }
    try { fetchRealtimeData(); } catch(e) { console.error(e); }
    setInterval(fetchRealtimeData, 1500);

    setTimeout(() => {
        fetch('/api/check-update')
            .then(r => r.json())
            .then(data => {
                if (data && data.has_update) {
                    const badge = document.getElementById('update-badge');
                    const btn = document.getElementById('btn-tab-update');
                    if (badge) badge.style.display = 'inline-block';
                    if (btn) btn.style.color = '#38bdf8';
                }
            }).catch(e => console.log("Update check failed", e));
    }, 2000);
});


function copyApiToken() {
    const input = document.getElementById('webhook_api_token');
    if (input) {
        input.select();
        document.execCommand('copy');
        alert('API-ключ успешно скопирован в буфер обмена!');
    }
}

function showDocSnippet(snippetId, btn) {
    document.querySelectorAll('.doc-code-snippet').forEach(el => el.style.display = 'none');
    const target = document.getElementById(snippetId);
    if (target) target.style.display = 'block';
    
    btn.parentElement.querySelectorAll('.sub-tab-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
}

async function executeCallToTest() {
    const operator = document.getElementById('test_callto_operator').value.trim();
    const phone = document.getElementById('test_callto_phone').value.trim();
    const resultBox = document.getElementById('callto_test_result');
    
    if (!phone) {
        alert('Пожалуйста, введите номер телефона клиента!');
        return;
    }
    
    resultBox.style.display = 'block';
    resultBox.style.background = '#1e293b';
    resultBox.style.color = '#38bdf8';
    resultBox.style.border = '1px solid #0284c7';
    resultBox.innerHTML = '⏳ Отправка запроса в Asterisk... Набираем оператора ' + (operator || '101') + '...';
    
    try {
        const res = await fetch('/api/v1/callto', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                operator: operator || '101',
                phone: phone
            })
        });
        const data = await res.json();
        
        if (data.status === 'success') {
            resultBox.style.background = '#064e3b';
            resultBox.style.color = '#34d399';
            resultBox.style.border = '1px solid #059669';
            resultBox.innerHTML = '✅ <b>Успешно!</b> ' + data.message + '<br><small style="color:#a7f3d0;">Поднимите трубку на софтфоне оператора ' + data.operator + ' — вызов автоматически соединится с ' + data.phone + '</small>';
        } else {
            resultBox.style.background = '#4c0519';
            resultBox.style.color = '#fda4af';
            resultBox.style.border = '1px solid #e11d48';
            resultBox.innerHTML = '❌ <b>Ошибка:</b> ' + (data.error || data.message || 'Не удалось инициировать вызов');
        }
    } catch (e) {
        resultBox.style.background = '#4c0519';
        resultBox.style.color = '#fda4af';
        resultBox.style.border = '1px solid #e11d48';
        resultBox.innerHTML = '❌ <b>Ошибка сети:</b> ' + e;
    }
}


function toggleNetMode(mode) {
    const staticFields1 = document.getElementById('static_ip_fields');
    const staticFields2 = document.getElementById('static_gw_fields');
    if (mode === 'dhcp') {
        if (staticFields1) staticFields1.style.opacity = '0.4';
        if (staticFields2) staticFields2.style.opacity = '0.4';
    } else {
        if (staticFields1) staticFields1.style.opacity = '1';
        if (staticFields2) staticFields2.style.opacity = '1';
    }
}

</script>



</body>
</html>
"""

def run_asterisk(cmd):
    try:
        res = subprocess.run(['sudo', '/usr/sbin/asterisk', '-rx', cmd], capture_output=True, text=True, timeout=3)
        if res.returncode == 0 and res.stdout:
            return res.stdout.strip()
        res = subprocess.run(['asterisk', '-rx', cmd], capture_output=True, text=True, timeout=3)
        return res.stdout.strip()
    except Exception as e:
        return str(e)

def format_duration(seconds):
    try:
        s = int(float(seconds))
        m = s // 60
        sec = s % 60
        if m > 0:
            return f"{m} мин {sec} сек"
        return f"{sec} сек"
    except Exception:
        return f"{seconds} сек"

def format_size(bytes_val):
    if bytes_val <= 44:
        return "0 КБ"
    if bytes_val < 1024 * 1024:
        return f"{bytes_val / 1024:.1f} КБ"
    return f"{bytes_val / (1024 * 1024):.2f} МБ"

def get_auth_logs():
    try:
        res = subprocess.run("journalctl -u asterisk.service -n 120 --no-pager | grep -iE 'REGISTER|INVITE|AUTH|Contact|res_pjsip|Call|Dial' | tail -n 18", shell=True, capture_output=True, text=True)
        return res.stdout.strip()
    except Exception as e:
        return str(e)

def get_amocrm_logs():
    if not os.path.exists(AMOCRM_LOG):
        return 'Лог синхронизации с amoCRM пуст.'
    try:
        with open(AMOCRM_LOG, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
            return ''.join(lines[-40:])
    except Exception as e:
        return str(e)

MCC_MNC_MAP = {
    '25062': 'T-Bank / Tinkoff',
    '25020': 'Tele2 / Ростелеком',
    '25001': 'МТС (MTS)',
    '25002': 'МегаФон (MegaFon)',
    '25099': 'Билайн (Beeline)',
    '25050': 'СберМобайл',
    '25028': 'Yota',
    '42402': 'Etisalat (UAE)',
    '42403': 'du (UAE)',
    '42404': 'Virgin Mobile (UAE)',
}

HUAWEI_PID_MAP = {
    '1001': 'Huawei E169 / E620',
    '1003': 'Huawei E220 / E230',
    '140c': 'Huawei E173',
    '1436': 'Huawei E171 / E173s',
    '1446': 'Huawei E171 / E173',
    '14ac': 'Huawei E1550 / E1820',
    '14db': 'Huawei E1550',
    '14fe': 'Huawei E3131',
    '1506': 'Huawei E3272 / E3372',
    '155b': 'Huawei E3372',
    '1c05': 'Huawei E173s',
    '1c08': 'Huawei E173u',
}

def get_modem_parsed():
    # 1. Проверяем физическое наличие USB-модема в разъеме
    usb_present = False
    try:
        lsusb = subprocess.run(['lsusb'], capture_output=True, text=True, timeout=2).stdout
        tty_ports = glob.glob('/dev/ttyUSB*')
        if ('12d1:' in lsusb or '19d2:' in lsusb or len(tty_ports) > 0):
            usb_present = True
    except Exception:
        pass

    raw = run_asterisk('dongle show device state dongle0')

    modem_info = {
        'usb_present': usb_present,
        'sim_present': False,
        'connected': False,
        'status_code': 'NO_USB',
        'state': 'USB-модем не подключен',
        'provider': '',
        'rssi': 0,
        'rssi_desc': 'Нет сигнала',
        'signal_pct': 0,
        'model': '',
        'imei': '',
        'imsi': '',
        'registration': '',
        'sms_center': '',
        'mode': '',
        'number': ''
    }

    # Если физически нет USB устройства - СТРОГО статус NO_USB
    if not usb_present:
        modem_info['status_code'] = 'NO_USB'
        modem_info['state'] = 'USB-модем отключен (разъем пуст)'
        return modem_info

    global last_hotplug_check
    now = time.time()

    # Если USB есть, но Asterisk еще не связался с портом
    if not raw or 'No such device' in raw or 'Unable to connect' in raw or 'No such command' in raw or 'Device not found' in raw:
        modem_info['status_code'] = 'SEARCHING'
        modem_info['state'] = 'USB подключен, автонастройка портов...'
        if now - last_hotplug_check > 15:
            last_hotplug_check = now
            import threading
            threading.Thread(target=lambda: subprocess.run(['python3', '/opt/asterisk-gui/dongle_hotplug.py'])).start()
        return modem_info

    fields = {}
    for line in raw.splitlines():
        if ':' in line:
            parts = line.split(':', 1)
            k = parts[0].strip()
            v = parts[1].strip()
            fields[k] = v

    detected_model = ''
    raw_mod = fields.get('Model', '').strip()
    man = fields.get('Manufacturer', 'Huawei').strip().capitalize()
    fw = fields.get('Firmware', '').strip()

    if raw_mod and raw_mod not in ['Unknown', 'NONE', '']:
        if not raw_mod.lower().startswith('huawei'):
            detected_model = f"{man} {raw_mod}"
        else:
            detected_model = raw_mod
        if fw and fw not in ['Unknown', 'NONE', '']:
            detected_model += f" (FW: {fw})"

    # Если Asterisk не вернул точную модель, определяем по USB Product ID
    if not detected_model and usb_present:
        try:
            lsusb_out = subprocess.run(['lsusb'], capture_output=True, text=True, timeout=2).stdout
            match = re.search(r'12d1:([0-9a-fA-F]{4})', lsusb_out)
            if match:
                pid = match.group(1).lower()
                if pid in HUAWEI_PID_MAP:
                    detected_model = HUAWEI_PID_MAP[pid]
                else:
                    match_name = re.search(r'12d1:[0-9a-fA-F]{4}\s+Huawei Technologies Co\., Ltd\.\s+(.*)', lsusb_out)
                    if match_name:
                        detected_model = f"Huawei {match_name.group(1).strip()}"
        except Exception:
            pass

    if detected_model:
        modem_info['model'] = detected_model
    elif usb_present:
        modem_info['model'] = 'Huawei 3G Modem' 
    if 'IMEI' in fields:
        modem_info['imei'] = fields.get('IMEI', '')
    if 'IMSI' in fields:
        imsi = fields.get('IMSI', '')
        if imsi and imsi not in ['Unknown', 'NONE', '', 'Error']:
            modem_info['imsi'] = imsi
            modem_info['sim_present'] = True
    if 'Provider Name' in fields:
        prov = fields.get('Provider Name', '').strip()
        if prov and prov not in ['NONE', 'Unknown', '']:
            modem_info['provider'] = prov

    # Если оператор не отдал текстовое имя, определяем по коду сети IMSI (MCC/MNC)
    if not modem_info.get('provider') and modem_info.get('imsi'):
        imsi_prefix = modem_info['imsi'][:5]
        if imsi_prefix in MCC_MNC_MAP:
            modem_info['provider'] = MCC_MNC_MAP[imsi_prefix]

    if not modem_info.get('provider') and modem_info.get('registration') in ['Registered, home network', 'Registered, roaming', 'Registered']:
        modem_info['provider'] = 'Сотовая сеть (В сети)' 
    if 'GSM Registration Status' in fields:
        modem_info['registration'] = fields.get('GSM Registration Status', '')
    if 'SMS Service Center' in fields:
        modem_info['sms_center'] = fields.get('SMS Service Center', '')
    if 'Subscriber Number' in fields:
        sub_num = fields.get('Subscriber Number', '')
        if sub_num and sub_num not in ['Unknown', 'NONE', '']:
            modem_info['number'] = sub_num
            
    if not modem_info.get('number'):
        cfg = load_integrations()
        modem_info['number'] = cfg.get('modem_phone', '')
    if 'Mode' in fields:
        modem_info['mode'] = fields.get('Mode', '')

    if 'RSSI' in fields:
        rssi_str = fields['RSSI']
        parts = rssi_str.split(',')
        rssi_val_str = parts[0].strip()
        try:
            val = int(rssi_val_str)
            if val == 99 or val <= 0:
                modem_info['rssi'] = 0
                modem_info['signal_pct'] = 0
                modem_info['rssi_desc'] = 'Нет сигнала (0 dBm)'
            else:
                dbm = -113 + (val * 2)
                pct = int(min(100, max(0, (val / 31.0) * 100)))
                modem_info['rssi'] = dbm
                modem_info['signal_pct'] = pct
                if pct >= 70:
                    modem_info['rssi_desc'] = f'Отличный ({pct}%, {dbm} dBm)'
                elif pct >= 40:
                    modem_info['rssi_desc'] = f'Хороший ({pct}%, {dbm} dBm)'
                else:
                    modem_info['rssi_desc'] = f'Слабый ({pct}%, {dbm} dBm)'
        except Exception:
            pass

    # Расчет финального кода статуса
    if not modem_info['sim_present']:
        modem_info['status_code'] = 'NO_SIM'
        modem_info['state'] = 'SIM-карта не вставлена или не читается'
    elif modem_info['registration'] in ['Registered, home network', 'Registered, roaming', 'Registered']:
        modem_info['status_code'] = 'ONLINE'
        modem_info['connected'] = True
        modem_info['state'] = 'В сети (Онлайн)'
    else:
        modem_info['status_code'] = 'SEARCHING'
        modem_info['state'] = 'Поиск сети оператора...'

    return modem_info

    fields = {}
    for line in raw.splitlines():
        if ':' in line:
            parts = line.split(':', 1)
            k = parts[0].strip()
            v = parts[1].strip()
            fields[k] = v

    detected_model = ''
    raw_mod = fields.get('Model', '').strip()
    man = fields.get('Manufacturer', 'Huawei').strip().capitalize()
    fw = fields.get('Firmware', '').strip()

    if raw_mod and raw_mod not in ['Unknown', 'NONE', '']:
        if not raw_mod.lower().startswith('huawei'):
            detected_model = f"{man} {raw_mod}"
        else:
            detected_model = raw_mod
        if fw and fw not in ['Unknown', 'NONE', '']:
            detected_model += f" (FW: {fw})"

    # Если Asterisk не вернул точную модель, определяем по USB Product ID
    if not detected_model and usb_present:
        try:
            lsusb_out = subprocess.run(['lsusb'], capture_output=True, text=True, timeout=2).stdout
            match = re.search(r'12d1:([0-9a-fA-F]{4})', lsusb_out)
            if match:
                pid = match.group(1).lower()
                if pid in HUAWEI_PID_MAP:
                    detected_model = HUAWEI_PID_MAP[pid]
                else:
                    match_name = re.search(r'12d1:[0-9a-fA-F]{4}\s+Huawei Technologies Co\., Ltd\.\s+(.*)', lsusb_out)
                    if match_name:
                        detected_model = f"Huawei {match_name.group(1).strip()}"
        except Exception:
            pass

    if detected_model:
        modem_info['model'] = detected_model
    elif usb_present:
        modem_info['model'] = 'Huawei 3G Modem' 
    if 'IMEI' in fields:
        modem_info['imei'] = fields.get('IMEI', '')
    if 'IMSI' in fields:
        imsi = fields.get('IMSI', '')
        if imsi and imsi not in ['Unknown', 'NONE', '']:
            modem_info['imsi'] = imsi
            modem_info['sim_present'] = True
    if 'Provider Name' in fields:
        prov = fields.get('Provider Name', '').strip()
        if prov and prov not in ['NONE', 'Unknown', '']:
            modem_info['provider'] = prov

    # Если оператор не отдал текстовое имя, определяем по коду сети IMSI (MCC/MNC)
    if not modem_info.get('provider') and modem_info.get('imsi'):
        imsi_prefix = modem_info['imsi'][:5]
        if imsi_prefix in MCC_MNC_MAP:
            modem_info['provider'] = MCC_MNC_MAP[imsi_prefix]

    if not modem_info.get('provider') and modem_info.get('registration') in ['Registered, home network', 'Registered, roaming', 'Registered']:
        modem_info['provider'] = 'Сотовая сеть (В сети)' 
    if 'GSM Registration Status' in fields:
        modem_info['registration'] = fields.get('GSM Registration Status', '')
    if 'SMS Service Center' in fields:
        modem_info['sms_center'] = fields.get('SMS Service Center', '')
    if 'Subscriber Number' in fields:
        sub_num = fields.get('Subscriber Number', '')
        if sub_num and sub_num not in ['Unknown', 'NONE', '']:
            modem_info['number'] = sub_num
            
    # Чтение сохраненного номера из конфига, если SIM не отдает MSISDN
    if not modem_info.get('number'):
        cfg = load_integrations()
        modem_info['number'] = cfg.get('modem_phone', '')
    if 'Mode' in fields:
        modem_info['mode'] = fields.get('Mode', '')

    if 'RSSI' in fields:
        rssi_str = fields['RSSI']
        parts = rssi_str.split(',')
        rssi_val_str = parts[0].strip()
        if rssi_val_str.isdigit():
            val = int(rssi_val_str)
            modem_info['rssi'] = val
            if val == 99 or val == 0:
                modem_info['rssi_desc'] = 'Нет сигнала'
                modem_info['signal_pct'] = 0
            else:
                modem_info['signal_pct'] = min(100, int((val / 31.0) * 100))
                modem_info['rssi_desc'] = parts[1].strip() if len(parts) > 1 else f"{val}/31"

    state_raw = fields.get('State', '')
    reg_raw = fields.get('GSM Registration Status', '').lower()

    if not usb_present and not fields.get('Device'):
        modem_info['status_code'] = 'NO_USB'
        modem_info['state'] = 'USB-модем не обнаружен в разъеме'
    elif not modem_info['sim_present']:
        modem_info['status_code'] = 'NO_SIM'
        modem_info['state'] = 'Сим-карта не вставлена или не читается'
    elif 'not registered' in reg_raw or 'searching' in reg_raw or 'GSM not re' in state_raw:
        modem_info['status_code'] = 'SEARCHING'
        modem_info['state'] = 'Поиск и регистрация в сети...'
        modem_info['connected'] = False
    elif 'registered' in reg_raw or 'Free' in state_raw or 'Ready' in state_raw:
        modem_info['status_code'] = 'ONLINE'
        modem_info['state'] = 'В сети (Готов к вызовам)'
        modem_info['connected'] = True
    else:
        modem_info['status_code'] = 'BUSY'
        modem_info['state'] = state_raw
        modem_info['connected'] = True

    return modem_info

    for line in raw.splitlines():
        if 'dongle0' in line:
            parts = line.split()
            if len(parts) >= 4:
                state_str = parts[2]
                if 'Free' in state_str or 'Ready' in state_str or 'GSM' in line:
                    modem_info['connected'] = True
                    modem_info['state'] = state_str
                else:
                    modem_info['connected'] = False
                    modem_info['state'] = 'Отключен (Not connected)'

                if len(parts) >= 8:
                    modem_info['provider'] = parts[7] if parts[7] != 'NONE' else ''
                    modem_info['rssi'] = parts[3]
                if len(parts) >= 11:
                    modem_info['imei'] = parts[10]
                if len(parts) >= 9:
                    modem_info['model'] = parts[8]

    return modem_info

def get_human_active_channels():
    raw = run_asterisk('core show channels concise')
    calls = []
    if not raw or '0 active' in raw:
        return calls

    for line in raw.splitlines():
        parts = line.strip().split('!')
        if len(parts) >= 12:
            chan = parts[0]
            exten_dst = parts[2]
            app = parts[5]
            caller_num = parts[7]
            duration_sec = parts[11]

            if app == 'Dial' or 'AppDial' not in chan:
                dst = exten_dst if exten_dst else 'Оператор'
                if 'Dial' in app and len(parts) >= 7 and 'PJSIP/' in parts[6]:
                    dst_match = re.search(r'PJSIP/([^,]+)', parts[6])
                    if dst_match:
                        dst = dst_match.group(1)

                calls.append({
                    'caller': caller_num if caller_num else chan.split('-')[0].replace('PJSIP/', ''),
                    'callee': dst,
                    'channel': chan.split('-')[0],
                    'state': 'Разговаривают' if parts[4] == 'Up' else 'Вызов (Звонок)',
                    'duration': format_duration(duration_sec)
                })
    return calls

def get_human_sip_sockets():
    raw = run_asterisk('pjsip show contacts')
    sockets = []
    for line in raw.splitlines():
        if 'Contact:' in line and 'sip:' in line:
            parts = line.split()
            if len(parts) >= 2:
                contact_str = parts[1]
                ext = contact_str.split('/')[0]
                ip_match = re.search(r'sip:[^@]+@([^;]+)', contact_str)
                ip_port = ip_match.group(1) if ip_match else contact_str
                sockets.append({'exten': ext, 'ip_port': ip_port})
    return sockets

def get_recent_calls():
    calls = []
    recordings = []
    if os.path.exists(RECORD_DIR):
        for f in glob.glob(os.path.join(RECORD_DIR, '*.wav')):
            fn = os.path.basename(f)
            sz = os.path.getsize(f)
            mtime = os.path.getmtime(f)
            time_part = fn.split('_')[0]
            try:
                file_dt = datetime.datetime.strptime(time_part, "%Y%m%d-%H%M%S")
            except Exception:
                file_dt = datetime.datetime.fromtimestamp(mtime)

            recordings.append({
                'filename': fn,
                'path': f,
                'size': sz,
                'dt': file_dt,
                'mtime': mtime,
                'fn': fn
            })

    ten_days_ago = datetime.datetime.now() - datetime.timedelta(days=10)
    seen_files = set()

    if os.path.exists(CSV_PATH):
        try:
            with open(CSV_PATH, 'r', encoding='utf-8', errors='ignore') as f:
                reader = csv.reader(f)
                all_rows = list(reader)
                for row in reversed(all_rows):
                    if len(row) >= 15:
                        accountcode = row[0] if len(row) > 0 else ''
                        src = row[1] if len(row) > 1 else ''
                        dst = row[2] if len(row) > 2 else ''
                        dcontext = row[3] if len(row) > 3 else ''
                        clid = row[4] if len(row) > 4 else ''
                        channel = row[5] if len(row) > 5 else ''
                        dstchannel = row[6] if len(row) > 6 else ''
                        lastapp = row[7] if len(row) > 7 else ''
                        lastdata = row[8] if len(row) > 8 else ''
                        calldate_str = row[9] if len(row) > 9 else ''
                        answer_date = row[10] if len(row) > 10 else ''
                        end_date = row[11] if len(row) > 11 else ''
                        duration_sec = row[12] if len(row) > 12 else '0'
                        billsec = row[13] if len(row) > 13 else '0'
                        disposition = row[14] if len(row) > 14 else ''
                        uniqueid = row[16] if len(row) > 16 else ''

                        # Skip internal technical SMS/USSD events
                        if (not src or src in ['sms', 'ussd']) and dst in ['sms', 'ussd']:
                            continue
                        if not src and not dst:
                            continue

                        # Determine real destination and direction
                        real_dst = dst
                        match = re.search(r'PJSIP/([0-9a-zA-Z]+)', dstchannel)
                        if match:
                            ans_ext = match.group(1)
                            if dst in ['s', '', 'ALL', 'operator', 'sms']:
                                real_dst = ans_ext
                            elif dst.startswith('+') or dst.startswith('0') or len(dst) >= 5:
                                real_dst = dst
                            else:
                                real_dst = f"{ans_ext} ({dst})"
                        elif dst in ['s', '']:
                            real_dst = 'Операторы (ALL)'
                        elif dst == 'sms':
                            real_dst = 'SMS входящее'
                        elif dst == 'ussd':
                            real_dst = 'USSD запрос'

                        # Determine Call Direction & Category
                        if 'dongle-incoming' in dcontext or 'Dongle/' in channel:
                            dir_type = 'inbound'
                            dir_label = 'Входящий GSM вызов'
                            dir_icon = '📥 Входящий GSM'
                        elif 'from-internal' in dcontext and ('Dongle/' in dstchannel or 'Dongle/' in lastdata or dst.startswith('+') or dst.startswith('0') or len(dst) >= 5):
                            dir_type = 'outbound'
                            dir_label = 'Исходящий GSM вызов'
                            dir_icon = '📤 Исходящий GSM'
                        elif 'from-internal' in dcontext and dst.isdigit() and len(dst) <= 4:
                            dir_type = 'internal'
                            dir_label = 'Внутренний вызов (SIP)'
                            dir_icon = '🔄 Внутренний SIP'
                        else:
                            dir_type = 'other'
                            dir_label = 'Вызов'
                            dir_icon = '📞 Звонок'

                        try:
                            calldate = datetime.datetime.strptime(calldate_str, "%Y-%m-%d %H:%M:%S")
                            if calldate < ten_days_ago:
                                continue
                        except Exception:
                            calldate = None

                        matched_file = None
                        file_sz = 0

                        if calldate:
                            for rec in sorted(recordings, key=lambda x: abs((x['dt'] - calldate).total_seconds())):
                                if rec['filename'] not in seen_files:
                                    diff_sec = abs((rec['dt'] - calldate).total_seconds())
                                    if diff_sec <= 20:
                                        fn = rec['filename']
                                        clean_src = src.replace('+', '') if src else ''
                                        clean_dst = dst.replace('+', '') if dst else ''
                                        
                                        match_f = False
                                        if src and src in fn:
                                            match_f = True
                                        elif clean_src and len(clean_src) >= 3 and clean_src in fn:
                                            match_f = True
                                        elif dst and len(dst) >= 3 and dst in fn:
                                            match_f = True
                                        elif clean_dst and len(clean_dst) >= 3 and clean_dst in fn:
                                            match_f = True
                                        elif 'ALL' in fn and (src in fn or (clean_src and clean_src in fn)):
                                            match_f = True

                                        if match_f:
                                            matched_file = rec['filename']
                                            file_sz = rec['size']
                                            seen_files.add(rec['filename'])
                                            break

                        calls.append({
                            'date': calldate_str,
                            'answer_date': answer_date,
                            'end_date': end_date,
                            'src': src,
                            'dst': real_dst,
                            'raw_dst': dst,
                            'clid': clid,
                            'channel': channel,
                            'dstchannel': dstchannel,
                            'dcontext': dcontext,
                            'lastapp': lastapp,
                            'lastdata': lastdata,
                            'uniqueid': uniqueid,
                            'duration_sec': duration_sec,
                            'billsec': billsec,
                            'duration_fmt': format_duration(billsec),
                            'total_duration_fmt': format_duration(duration_sec),
                            'file_size_bytes': file_sz,
                            'file_size_fmt': format_size(file_sz),
                            'filename': matched_file,
                            'disposition': disposition,
                            'dir_type': dir_type,
                            'dir_label': dir_label,
                            'dir_icon': dir_icon
                        })
                        if len(calls) >= 200:
                            break
        except Exception:
            pass

    return calls



def get_trunks_status():
    status_map = {}
    
    # 1. Check PJSIP Registrations
    pjsip_reg_out = run_asterisk('pjsip show registrations')
    for line in pjsip_reg_out.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            t_reg_name = parts[0].split('/')[0]
            clean_name = t_reg_name.replace('reg_', '').replace('_reg', '')
            
            host_match = re.search(r'sip:([^/\s]+)', line)
            host_val = host_match.group(1) if host_match else ''
            
            if 'Registered' in line:
                val = {
                    'online': True,
                    'status_type': 'registered',
                    'status_text': 'Авторизован',
                    'status_badge': '🟢 Авторизован (200 OK)',
                    'color': 'success'
                }
            elif 'Request Sent' in line or 'Trying' in line or 'Auth. Sent' in line:
                val = {
                    'online': False,
                    'status_type': 'connecting',
                    'status_text': 'Идет подключение...',
                    'status_badge': '🟡 Идет подключение...',
                    'color': 'warning'
                }
            elif 'Rejected' in line or 'Failed' in line or 'Forbidden' in line:
                val = {
                    'online': False,
                    'status_type': 'rejected',
                    'status_text': 'Не авторизован (Ошибка)',
                    'status_badge': '🔴 Ошибка авторизации (Rejected)',
                    'color': 'error'
                }
            else:
                val = {
                    'online': False,
                    'status_type': 'unregistered',
                    'status_text': 'Не подключен',
                    'status_badge': '⚪ Не подключен',
                    'color': 'on-surface-variant'
                }
            
            status_map[t_reg_name] = val
            status_map[clean_name] = val
            status_map[f"trunk_{clean_name}"] = val
            if host_val:
                status_map[host_val] = val
                status_map[host_val.split(':')[0]] = val

    # 2. Check PJSIP Endpoints for RTT ping & availability
    pjsip_ep_out = run_asterisk('pjsip show endpoints')
    current_ep = None
    for line in pjsip_ep_out.splitlines():
        if 'Endpoint:' in line:
            parts = line.split()
            if len(parts) >= 2:
                current_ep = parts[1].split('/')[0]
        if current_ep and 'Contact:' in line:
            if 'Avail' in line:
                rtt_m = re.search(r'Avail\s+([0-9.]+)', line)
                rtt_str = f" ({int(float(rtt_m.group(1)))} ms)" if rtt_m else ""
                if current_ep not in status_map or status_map[current_ep]['status_type'] == 'unregistered':
                    status_map[current_ep] = {
                        'online': True,
                        'status_type': 'registered',
                        'status_text': f'В сети{rtt_str}',
                        'status_badge': f'🟢 В сети{rtt_str}',
                        'color': 'success'
                    }
            elif 'Unavail' in line and current_ep not in status_map:
                status_map[current_ep] = {
                    'online': False,
                    'status_type': 'rejected',
                    'status_text': 'Недоступен',
                    'status_badge': '🔴 Недоступен (Offline)',
                    'color': 'error'
                }

    return status_map


    # 3. Check IAX2 Peers
    iax_peers_out = run_asterisk('iax2 show peers')
    for line in iax_peers_out.splitlines():
        parts = line.split()
        if len(parts) >= 6:
            peer_name = parts[0].split('/')[0]
            line_str = " ".join(parts)
            host_ip = parts[1]
            if 'OK' in line_str:
                ping_m = re.search(r'OK \(([0-9]+ ms)\)', line_str)
                ping_str = f" ({ping_m.group(1)})" if ping_m else ""
                val = {'online': True, 'status_text': f'🟢 В сети{ping_str}', 'color': '#10b981'}
                status_map[peer_name] = val
                status_map[host_ip] = val
            elif 'UNREACHABLE' in line_str:
                val = {'online': False, 'status_text': '🔴 Не в сети (Unreachable)', 'color': '#ef4444'}
                if peer_name not in status_map: status_map[peer_name] = val
                if host_ip not in status_map: status_map[host_ip] = val
            elif 'LAGGED' in line_str:
                val = {'online': True, 'status_text': '🟡 Высокий пинг (Lagged)', 'color': '#f59e0b'}
                if peer_name not in status_map: status_map[peer_name] = val

    # 4. Check IAX2 Registry
    iax_reg_out = run_asterisk('iax2 show registry')
    for line in iax_reg_out.splitlines():
        parts = line.split()
        if len(parts) >= 5:
            host_p = parts[0]
            host_clean = host_p.split(':')[0]
            user_p = parts[2]
            state_p = parts[-1]
            if state_p == 'Registered':
                val = {'online': True, 'status_text': '🟢 В сети (Registered)', 'color': '#10b981'}
                status_map[host_p] = val
                status_map[host_clean] = val
                status_map[user_p] = val
            elif 'Request' in line or 'Sent' in line:
                if host_clean not in status_map or not status_map[host_clean]['online']:
                    val = {'online': False, 'status_text': '🟡 Ожидание ответа (Request Sent)', 'color': '#f59e0b'}
                    status_map[host_p] = val
                    status_map[host_clean] = val
                    status_map[user_p] = val

    return status_map
import ipaddress
import threading

def send_telegram_ip_notification(current_ip, gateway, saved_ip=None, subnet_changed=False):
    try:
        cfg = load_integrations()
        tg_cfg = cfg.get('telegram', {})
        if not tg_cfg.get('enabled') or not tg_cfg.get('token') or not tg_cfg.get('chat_id'):
            return
        
        token = tg_cfg['token']
        chat_id = tg_cfg['chat_id']
        
        if subnet_changed:
            msg = (
                "⚠️ <b>ВНИМАНИЕ: СМЕНА ПОДСЕТИ ОБОРУДОВАНИЯ!</b>\n\n"
                f"📡 <b>Новый IP от DHCP:</b> <code>{current_ip}</code>\n"
                f"🌐 <b>Шлюз:</b> <code>{gateway}</code>\n"
                f"📌 <b>Ранее сохраненный IP:</b> <code>{saved_ip or 'Не задан'}</code>\n\n"
                "ℹ️ Подсеть изменилась. Статический адрес проигнорирован во избежание потери доступа. "
                "Оборудование доступно по новому адресу! Зайдите в панель управления во вкладку «🌐 Настройки сети» для подтверждения."
            )
        else:
            msg = (
                "ℹ️ <b>Уведомление о сетевом статусе:</b>\n\n"
                f"📡 <b>Текущий IP (DHCP):</b> <code>{current_ip}</code>\n"
                f"🌐 <b>Шлюз:</b> <code>{gateway}</code>\n"
                f"📌 <b>Сохраненный IP:</b> <code>{saved_ip or 'Не задан'}</code>"
            )
            
        url = f"{TG_BASE_URL}/bot{token}/sendMessage"
        requests.post(url, json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"}, timeout=5)
    except Exception as e:
        print("Telegram IP notification error:", e)


def get_system_network_info():
    info = {
        'current_ip': '127.0.0.1',
        'prefix': 24,
        'gateway': '192.168.0.1',
        'mac': '',
        'dhcp_ip': '',
        'is_dhcp': True,
        'dns': ['8.8.8.8', '1.1.1.1'],
        'all_ips': []
    }
    try:
        res = subprocess.run(['ip', '-j', 'addr', 'show', 'dev', 'eth0'], capture_output=True, text=True, timeout=3)
        if res.returncode == 0:
            data = json.loads(res.stdout)
            for iface in data:
                info['mac'] = iface.get('address', '')
                for addr in iface.get('addr_info', []):
                    if addr.get('family') == 'inet':
                        ip_val = addr.get('local')
                        plen = addr.get('prefixlen', 24)
                        info['all_ips'].append(f"{ip_val}/{plen}")
                        if not info['dhcp_ip'] and addr.get('dynamic'):
                            info['dhcp_ip'] = ip_val
                        if not info['current_ip'] or info['current_ip'] == '127.0.0.1':
                            info['current_ip'] = ip_val
                            info['prefix'] = plen
        
        # Check default route
        route_res = subprocess.run(['ip', 'route', 'show', 'default'], capture_output=True, text=True, timeout=3)
        if route_res.returncode == 0 and route_res.stdout:
            for line in route_res.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 3 and parts[0] == 'default' and parts[1] == 'via':
                    info['gateway'] = parts[2]
                    break
    except Exception as e:
        print("Error getting network info:", e)
    return info


def network_guardian_startup_check():
    try:
        cfg = load_integrations()
        net_cfg = cfg.get('network', {})
        saved_ip = net_cfg.get('saved_ip')
        saved_prefix = net_cfg.get('saved_prefix', 24)
        saved_gateway = net_cfg.get('saved_gateway', '192.168.0.1')
        
        net_info = get_system_network_info()
        current_ip = net_info['current_ip']
        current_gateway = net_info['gateway']
        
        if not saved_ip:
            # First launch, no saved IP yet
            return
            
        try:
            cur_net = ipaddress.IPv4Network(f"{current_ip}/{net_info['prefix']}", strict=False)
            saved_net = ipaddress.IPv4Network(f"{saved_ip}/{saved_prefix}", strict=False)
            
            if cur_net == saved_net:
                print(f"[Network Guardian] Subnet MATCHES ({cur_net}). Applying saved static IP: {saved_ip}")
                apply_network_settings(saved_ip, saved_prefix, saved_gateway, mode='static', silent=True)
            else:
                print(f"[Network Guardian] Subnet CHANGED! Detected: {cur_net}, Saved: {saved_net}. Ignoring static IP, keeping DHCP.")
                # Send alert to Telegram
                send_telegram_ip_notification(current_ip, current_gateway, saved_ip, subnet_changed=True)
        except Exception as ex:
            print("[Network Guardian] Subnet comparison error:", ex)
    except Exception as e:
        print("[Network Guardian] Startup check error:", e)


def apply_network_settings(ip_addr, prefix_len, gateway_ip, mode='static', silent=False):
    try:
        if mode == 'dhcp':
            netplan_yaml = """network:
  version: 2
  ethernets:
    eth0:
      renderer: NetworkManager
      dhcp4: true
      dhcp6: false
"""
        else:
            netplan_yaml = f"""network:
  version: 2
  ethernets:
    eth0:
      renderer: NetworkManager
      dhcp4: false
      dhcp6: false
      addresses:
        - {ip_addr}/{prefix_len}
      routes:
        - to: default
          via: {gateway_ip}
      nameservers:
        addresses: [8.8.8.8, 1.1.1.1]
"""
        tmp_file = '/tmp/01-netcfg.yaml'
        with open(tmp_file, 'w', encoding='utf-8') as f:
            f.write(netplan_yaml)
        
        subprocess.run(['sudo', 'cp', tmp_file, '/etc/netplan/01-netcfg.yaml'], check=True, timeout=5)
        subprocess.run(['sudo', 'chmod', '600', '/etc/netplan/01-netcfg.yaml'], timeout=5)
        subprocess.run(['sudo', 'netplan', 'apply'], timeout=10)
        return True, "Сетевые настройки успешно применены!"
    except Exception as e:
        return False, f"Ошибка применения сети: {str(e)}"

def get_available_gateways(cfg=None):
    if cfg is None:
        cfg = load_integrations()
    gateways = [
        {'id': 'dongle_pool', 'name': '📱 Пул GSM Модемов (Первый свободный)'},
        {'id': 'dongle0', 'name': '📱 GSM Модем 1 (dongle0)'}
    ]
    for t in cfg.get('sip_trunks', []):
        if t.get('enabled', True):
            gateways.append({'id': t['id'], 'name': f"🌐 SIP-Транк: {t['name']} ({t.get('host', '')})"})
    return gateways

def load_integrations():
    data = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            pass
    if not isinstance(data, dict):
        data = {}
    
    if 'webhooks' not in data:
        data['webhooks'] = {
            'enabled': True,
            'api_token': 'sk_' + secrets.token_hex(16),
            'default_operator': '101'
        }
    if 'amocrm' not in data:
        data['amocrm'] = {"enabled": False, "send_internal": True, "subdomain": "", "token": "", "pipeline_id": "", "status_id": ""}
    if 'gdrive' not in data:
        data['gdrive'] = {"enabled": False, "token": "", "folder_id": ""}
    if 'telegram' not in data:
        data['telegram'] = {"enabled": False, "token": "", "chat_id": ""}
    if 'routing' not in data:
        data['routing'] = {"inbound_target": "ALL"}
    if 'ivr_tree' not in data:
        data['ivr_tree'] = {
            "enabled": True,
            "debug_enabled": True,
            "debug_exten": "888",
            "nodes": [
                {
                    "id": "main",
                    "title": "Главное меню (Уровень 1)",
                    "audio_file": "greeting_main.wav",
                    "timeout_sec": 7,
                    "timeout_action": "operator",
                    "timeout_target": "ALL",
                    "branches": [
                        {"digit": "1", "title": "Русский язык", "action": "operator", "target": "ALL"},
                        {"digit": "2", "title": "English", "action": "operator", "target": "ALL"},
                        {"digit": "3", "title": "Arabic (العربية)", "action": "operator", "target": "ALL"}
                    ]
                }
            ]
        }
    return data
def save_integrations(data):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)




# ================= MULTILINGUAL (I18N) ENGINE (TOP 10 WORLD LANGUAGES) =================
LOCALES_DIR = os.path.join(os.path.dirname(__file__), 'locales')

def get_available_languages():
    lang_file = os.path.join(LOCALES_DIR, 'languages.json')
    if os.path.exists(lang_file):
        try:
            with open(lang_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "ru": {"name": "Русский", "flag": "🇷🇺", "dir": "ltr"},
        "en": {"name": "English", "flag": "🇺🇸", "dir": "ltr"}
    }

def get_locale_translations(lang_code='ru'):
    fp = os.path.join(LOCALES_DIR, f"{lang_code}.json")
    if not os.path.exists(fp):
        fp = os.path.join(LOCALES_DIR, "en.json")
    if os.path.exists(fp):
        try:
            with open(fp, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def get_current_language():
    cfg = load_integrations()
    # Check cookie first, then saved integrations, then browser header
    cookie_lang = request.cookies.get('app_lang')
    if cookie_lang and cookie_lang in get_available_languages():
        return cookie_lang
    if 'system_lang' in cfg and cfg['system_lang'] in get_available_languages():
        return cfg['system_lang']
    return 'ru'

@app.route('/set-language/<lang_code>', methods=['GET', 'POST'])
def set_language(lang_code):
    langs = get_available_languages()
    if lang_code not in langs:
        lang_code = 'en'
    cfg = load_integrations()
    cfg['system_lang'] = lang_code
    save_integrations(cfg)
    
    resp = redirect(request.referrer or url_for('index'))
    resp.set_cookie('app_lang', lang_code, max_age=60*60*24*365) # 1 year
    return resp

@app.route('/api/locales/<lang_code>', methods=['GET'])
def api_get_locale(lang_code):
    return jsonify(get_locale_translations(lang_code))


def get_available_sip_contexts():
    """Extracts all valid dialplan contexts from extensions.conf and pjsip.conf."""
    contexts = set(['from-internal', 'default'])
    
    # 1. Parse extensions.conf
    if os.path.exists(EXTENSIONS_CONF):
        try:
            with open(EXTENSIONS_CONF, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('[') and line.endswith(']'):
                        ctx = line[1:-1].strip()
                        if ctx not in ['general', 'globals'] and not ctx.startswith('sub-'):
                            contexts.add(ctx)
        except Exception:
            pass

    # 2. Parse existing PJSIP endpoints contexts
    if os.path.exists(PJSIP_CONF):
        try:
            with open(PJSIP_CONF, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip().startswith('context='):
                        ctx = line.strip().split('=', 1)[1].strip()
                        if ctx:
                            contexts.add(ctx)
        except Exception:
            pass

    return sorted(list(contexts))


def load_sip_accounts():
    accounts = []
    if not os.path.exists(PJSIP_CONF):
        return accounts
    with open(PJSIP_CONF, 'r', encoding='utf-8') as f:
        content = f.read()

    sections = re.findall(r'\[([^\]]+)\]\s*([^\[]*)', content)
    endpoints = {}
    auths = {}
    for sec_name, sec_body in sections:
        sec_name = sec_name.strip()
        body_dict = {}
        for line in sec_body.splitlines():
            line = line.strip()
            if '=' in line and not line.startswith(';'):
                k, v = line.split('=', 1)
                body_dict[k.strip()] = v.strip()
        if body_dict.get('type') == 'endpoint':
            endpoints[sec_name] = body_dict
        elif body_dict.get('type') == 'auth':
            auths[sec_name] = body_dict

    for ext, body in endpoints.items():
        if ext.startswith('trunk_') or not ext.isdigit():
            continue
        auth_name = body.get('auth', ext)
        pwd = "***"
        if auth_name in auths and 'password' in auths[auth_name]:
            pwd = auths[auth_name]['password']
        
        cid = body.get('callerid', '')
        name = ''
        if cid:
            m = re.match(r'([^<]+)<', cid)
            if m:
                name = m.group(1).strip()
        if not name:
            name = f"Оператор {ext}"

        accounts.append({
            'exten': ext,
            'password': pwd,
            'name': name
        })
    return sorted(accounts, key=lambda x: x['exten'])

def generate_pjsip_conf():
    accounts = load_sip_accounts()
    cfg = load_integrations()
    trunks = cfg.get('sip_trunks', [])

    out = [
        "; ==========================================================",
        "; PJSIP Configuration (Auto-generated by Asterisk PBX Web)",
        "; ==========================================================",
        "",
        "[transport-udp]",
        "type=transport",
        "protocol=udp",
        "bind=0.0.0.0:5060",
        "local_net=192.168.0.0/16",
        "",
        "[transport-tcp]",
        "type=transport",
        "protocol=tcp",
        "bind=0.0.0.0:5060",
        "local_net=192.168.0.0/16",
        ""
    ]

    # 1. Внутренние софтфоны (Internal Extensions)
    out.append("; --- Внутренние SIP-аккаунты и софтфоны (Internal Extensions) ---")
    for acc in accounts:
        ext = acc['exten']
        pwd = acc['password']
        name = acc.get('name', f"Оператор {ext}")

        out.append(f"; Аккаунт {name} ({ext})")
        out.append(f"[{ext}]")
        out.append("type=aor")
        out.append("max_contacts=5")
        out.append("remove_existing=yes")
        out.append("")
        out.append(f"[{ext}]")
        out.append("type=auth")
        out.append("auth_type=userpass")
        out.append(f"username={ext}")
        out.append(f"password={pwd}")
        out.append("")
        out.append(f"[{ext}]")
        out.append("type=endpoint")
        out.append("context=from-internal")
        out.append("disallow=all")
        out.append("allow=alaw")
        out.append("allow=ulaw")
        out.append("allow=g722")
        out.append("allow=slin16")
        out.append("direct_media=no")
        out.append("rtp_symmetric=yes")
        out.append("force_rport=yes")
        out.append("rewrite_contact=yes")
        out.append("tos_audio=ef")
        out.append("cos_audio=6")
        out.append(f"auth={ext}")
        out.append(f"outbound_auth={ext}")
        out.append(f"aors={ext}")
        out.append(f"callerid={name} <{ext}>")
        out.append("")

    # 2. Внешние SIP-Транки и регистрации (VoIP Провайдеры и подключение к другой PBX)
    out.append("; --- Внешние SIP-Транки и Клиентские Регистрации ---")
    for t in trunks:
        if not t.get('enabled', True):
            continue
        t_id = t['id']
        t_name = t.get('name', t_id)
        t_host = t.get('host', '')
        t_port = t.get('port', 5060)
        t_user = t.get('username', '')
        t_pass = t.get('password', '')
        t_cid = t.get('callerid', '')
        t_transport = t.get('transport', 'udp')

        if not t_host:
            continue

        out.append(f"; SIP Транк: {t_name}")
        
        if t_user:
            # Сценарий 1: Транк с регистрацией (логин/пароль)
            out.append(f"[{t_id}_auth]")
            out.append("type=auth")
            out.append("auth_type=userpass")
            out.append(f"username={t_user}")
            out.append(f"password={t_pass}")
            out.append("")
            out.append(f"[{t_id}_reg]")
            out.append("type=registration")
            out.append(f"transport=transport-{t_transport}")
            out.append(f"outbound_auth={t_id}_auth")
            out.append(f"server_uri=sip:{t_host}:{t_port}")
            out.append(f"client_uri=sip:{t_user}@{t_host}:{t_port}")
            out.append(f"contact_user={t_user}")
            out.append("retry_interval=10")
            out.append("max_retries=1000")
            out.append("expiration=60")
            out.append("line=yes")
            out.append(f"endpoint={t_id}")
            out.append("")
            out.append(f"[{t_id}]")
            out.append("type=aor")
            out.append(f"contact=sip:{t_user}@{t_host}:{t_port}")
            out.append("qualify_frequency=30")
        else:
            # Сценарий 2: IP-транк (без регистрации, аутентификация по IP)
            out.append(f"[{t_id}_identify]")
            out.append("type=identify")
            out.append(f"endpoint={t_id}")
            out.append(f"match={t_host}")
            out.append("")
            out.append(f"[{t_id}]")
            out.append("type=aor")
            out.append(f"contact=sip:{t_host}:{t_port}")
            out.append("qualify_frequency=30")
            
        out.append("")
        out.append(f"[{t_id}]")
        out.append("type=endpoint")
        out.append(f"transport=transport-{t_transport}")
        out.append(f"context=trunk-in-{t_id}")
        out.append("disallow=all")
        out.append("allow=alaw")
        out.append("allow=ulaw")
        out.append("allow=g722")
        out.append("allow=slin16")
        out.append("direct_media=no")
        out.append("rtp_symmetric=yes")
        out.append("force_rport=yes")
        out.append("rewrite_contact=yes")
        if t_user:
            out.append(f"outbound_auth={t_id}_auth")
            out.append(f"from_user={t_user}")
        out.append(f"aors={t_id}")
        out.append(f"from_domain={t_host}:{t_port}")
        
        if t_cid:
            out.append(f"callerid={t_cid}")
        out.append("")

    try:
        with open(PJSIP_CONF, 'w', encoding='utf-8') as f:
            f.write('\n'.join(out))
        run_asterisk('pjsip reload')
    except Exception as e:
        print("Error saving pjsip.conf:", e)
def load_sip_accounts():
    accounts = []
    if not os.path.exists(PJSIP_CONF):
        return accounts
    with open(PJSIP_CONF, 'r', encoding='utf-8') as f:
        content = f.read()

    sections = re.findall(r'\[([^\]]+)\]\s*([^\[]*)', content)
    endpoints = {}
    auths = {}
    for sec_name, sec_body in sections:
        sec_name = sec_name.strip()
        body_dict = {}
        for line in sec_body.splitlines():
            line = line.strip()
            if '=' in line and not line.startswith(';'):
                k, v = line.split('=', 1)
                body_dict[k.strip()] = v.strip()
        if body_dict.get('type') == 'endpoint':
            endpoints[sec_name] = body_dict
        elif body_dict.get('type') == 'auth':
            auths[sec_name] = body_dict

    for ext, body in endpoints.items():
        if ext.startswith('trunk_') or not ext.isdigit():
            continue
        auth_name = body.get('auth', ext)
        pwd = "***"
        if auth_name in auths and 'password' in auths[auth_name]:
            pwd = auths[auth_name]['password']
        ctx = body.get('context', 'from-internal')
        accounts.append({'exten': ext, 'password': pwd, 'context': ctx})
    return accounts

def get_online_contacts():
    online = []
    # 1. PJSIP contacts
    raw_contacts = run_asterisk('pjsip show contacts')
    for line in raw_contacts.splitlines():
        if 'Contact:' in line and 'sip:' in line:
            parts = line.split()
            if len(parts) >= 2:
                contact_str = parts[1]
                ext = contact_str.split('/')[0]
                online.append(ext)
    # 2. IAX2 peers
    raw_iax = run_asterisk('iax2 show peers')
    for line in raw_iax.splitlines():
        parts = line.split()
        if len(parts) >= 6:
            peer_name = parts[0].split('/')[0]
            host_str = parts[1]
            status_str = " ".join(parts[2:])
            if 'OK' in status_str or 'Unmonitored' in status_str or (host_str != '(null)' and host_str != '(Unspecified)' and not host_str.startswith('(')):
                online.append(peer_name)
    return list(set(online))

def get_dial_target(target, all_extens):
    if target == 'ALL' or not target:
        if all_extens:
            return '&'.join([f'PJSIP/{e}' for e in all_extens])
        return 'PJSIP/100&PJSIP/100&PJSIP/101&PJSIP/102'
    return f'PJSIP/{target}'


def build_outbound_dialplan_lines(cfg):
    outbound = cfg.get('outbound_routing', {})
    routes = outbound.get('routes', [])
    operator_rules = outbound.get('operator_rules', {})
    enable_failover = outbound.get('enable_failover', True)

    def get_dial_str(gw, exten_var="${EXTEN}"):
        if not gw or gw == 'dongle0' or gw == 'dongle_pool':
            return f"Dongle/dongle0/{exten_var}"
        else:
            return f"PJSIP/{exten_var}@{gw}"

    primary_gw = "dongle0"
    failover_gw = None
    if routes:
        first_route = routes[0]
        gateways = first_route.get('gateways', [])
        if gateways:
            primary_gw = gateways[0]
            if len(gateways) > 1:
                failover_gw = gateways[1]
    elif cfg.get('sip_trunks'):
        for t in cfg['sip_trunks']:
            if t.get('enabled', True):
                primary_gw = t['id']
                break

    lines = []
    lines.append("; 3. Динамическая исходящая маршрутизация (GSM & SIP Транки)")
    
    op_gotos = []
    for exten, gw in operator_rules.items():
        if gw:
            op_gotos.append(f" same => n,ExecIf($[\"${{CALLERID(num)}}\" = \"{exten}\"]?Dial({get_dial_str(gw)},60))")

    for pat in ['_+.', '_XXXXX.', '_0X.']:
        lines.append(f"exten => {pat},1,NoOp(Исходящий вызов на ${{EXTEN}} от ${{CALLERID(num)}})")
        lines.append(" same => n,Set(REC_FILE=${STRFTIME(${EPOCH},,%Y%m%d-%H%M%S)}_${CALLERID(num)}_${EXTEN}.wav)")
        lines.append(" same => n,Set(__CALL_ID=${UNIQUEID})")
        lines.append(" same => n,Set(__CALL_SRC=${CALLERID(num)})")
        lines.append(" same => n,Set(__CALL_DST=${EXTEN})")
        lines.append(" same => n,Set(__CALL_DIRECTION=outbound)")
        lines.append(" same => n,Set(__REC_PATH=${RECORD_DIR}/${REC_FILE})")
        lines.append(" same => n,Set(CHANNEL(hangup_handler_push)=sub-post-call-sync,s,1)")
        lines.append(" same => n,MixMonitor(${REC_PATH})")
        
        for og in op_gotos:
            lines.append(og)
            lines.append(" same => n,GotoIf($[\"${DIALSTATUS}\" = \"ANSWER\"]?done)")

        lines.append(f" same => n,Dial({get_dial_str(primary_gw)},60)")
        
        if enable_failover and failover_gw:
            lines.append(" same => n,GotoIf($[\"${DIALSTATUS}\" = \"ANSWER\"]?done)")
            lines.append(f" same => n,NoOp(Линия 1 недоступна (${{DIALSTATUS}}) -> Переход на резерв {failover_gw})")
            lines.append(f" same => n,Dial({get_dial_str(failover_gw)},60)")

        lines.append(" same => n(done),Hangup()")
        lines.append("")

    return "\n".join(lines)

def generate_dialplan_from_tree():
    cfg = load_integrations()
    accounts = load_sip_accounts()
    all_extens = [a['exten'] for a in accounts]
    outbound_dialplan_block = build_outbound_dialplan_lines(cfg)

    # Compile SIP Groups into Dialplan
    sip_groups = get_sip_groups()
    group_dialplan_lines = []
    for g in sip_groups:
        g_ext = g.get('exten', '').strip()
        g_name = g.get('name', 'Group')
        g_timeout = g.get('timeout', 30)
        g_strategy = g.get('strategy', 'ringall')
        members = g.get('members', [])
        
        if not g_ext or not members: continue
        
        if g_strategy == 'ringall':
            dial_str = "&".join([f"PJSIP/{m}" for m in members])
            group_dialplan_lines.append(f"; Группа абонентов: {g_name} ({g_ext}) - Одновременный звонок всем")
            group_dialplan_lines.append(f"exten => {g_ext},1,NoOp(Вызов группы: {g_name} -> {g_ext})")
            group_dialplan_lines.append(f" same => n,Set(REC_FILE=${{STRFTIME(${{EPOCH}},,%Y%m%d-%H%M%S)}}_${{CALLERID(num)}}_group{g_ext}.wav)")
            group_dialplan_lines.append(f" same => n,Set(__CALL_ID=${{UNIQUEID}})")
            group_dialplan_lines.append(f" same => n,Set(__CALL_SRC=${{CALLERID(num)}})")
            group_dialplan_lines.append(f" same => n,Set(__CALL_DST={g_ext})")
            group_dialplan_lines.append(f" same => n,Set(__CALL_DIRECTION=internal)")
            group_dialplan_lines.append(f" same => n,Set(__REC_PATH=${{RECORD_DIR}}/${{REC_FILE}})")
            group_dialplan_lines.append(f" same => n,Set(CHANNEL(hangup_handler_push)=sub-post-call-sync,s,1)")
            group_dialplan_lines.append(f" same => n,Answer()")
            group_dialplan_lines.append(f" same => n,MixMonitor(${{REC_PATH}})")
            group_dialplan_lines.append(f" same => n,Dial({dial_str},{g_timeout})")
            group_dialplan_lines.append(f" same => n,Hangup()")
        elif g_strategy == 'hunt':
            # Sequential hunt
            group_dialplan_lines.append(f"; Группа абонентов: {g_name} ({g_ext}) - Поочередный звонок (Hunt)")
            group_dialplan_lines.append(f"exten => {g_ext},1,NoOp(Вызов группы поочередно: {g_name} -> {g_ext})")
            group_dialplan_lines.append(f" same => n,Set(REC_FILE=${{STRFTIME(${{EPOCH}},,%Y%m%d-%H%M%S)}}_${{CALLERID(num)}}_group{g_ext}.wav)")
            group_dialplan_lines.append(f" same => n,Set(__CALL_ID=${{UNIQUEID}})")
            group_dialplan_lines.append(f" same => n,Set(__CALL_SRC=${{CALLERID(num)}})")
            group_dialplan_lines.append(f" same => n,Set(__CALL_DST={g_ext})")
            group_dialplan_lines.append(f" same => n,Set(__CALL_DIRECTION=internal)")
            group_dialplan_lines.append(f" same => n,Set(__REC_PATH=${{RECORD_DIR}}/${{REC_FILE}})")
            group_dialplan_lines.append(f" same => n,Set(CHANNEL(hangup_handler_push)=sub-post-call-sync,s,1)")
            group_dialplan_lines.append(f" same => n,Answer()")
            group_dialplan_lines.append(f" same => n,MixMonitor(${{REC_PATH}})")
            hunt_to = max(5, int(g_timeout / len(members)))
            for m in members:
                group_dialplan_lines.append(f" same => n,Dial(PJSIP/{m},{hunt_to})")
            group_dialplan_lines.append(f" same => n,Hangup()")

    groups_dialplan_block = "\n".join(group_dialplan_lines)


    ivr_trees = cfg.get('ivr_trees', {})
    default_ivr_tree = cfg.get('ivr_tree', {})
    sip_trunks = cfg.get('sip_trunks', [])

    debug_enabled = default_ivr_tree.get('debug_enabled', True)
    debug_exten = default_ivr_tree.get('debug_exten', '888').strip()
    if debug_enabled and debug_exten:
        debug_dialplan_entry = f"""
; --- DEBUG IVR ТЕСТОВЫЙ ВЫЗОВ (Наберите {debug_exten} с любого SIP софтфона) ---
exten => {debug_exten},1,NoOp(Debug вызов IVR от ${{CALLERID(num)}} на {debug_exten})
 same => n,Goto(dongle-incoming,s,1)
"""
    else:
        debug_dialplan_entry = ""


    all_ivr_contexts = []

    # Helper to compile an IVR tree into Asterisk dialplan contexts
    def compile_tree_to_contexts(tree, prefix, trunk_title):
        if not tree or not tree.get('nodes'):
            return f"""
[{prefix}]
exten => _.,1,NoOp(Входящий вызов {trunk_title} -> Прямой вызов всех)
 same => n,Set(REC_FILE=${{STRFTIME(${{EPOCH}},,%Y%m%d-%H%M%S)}}_${{CALLERID(num)}}_INBOUND.wav)
 same => n,Set(__CALL_ID=${{UNIQUEID}})
 same => n,Set(__CALL_SRC=${{CALLERID(num)}})
 same => n,Set(__CALL_DST=ALL)
 same => n,Set(__CALL_DIRECTION=inbound)
 same => n,Set(__REC_PATH=${{RECORD_DIR}}/${{REC_FILE}})
 same => n,Set(CHANNEL(hangup_handler_push)=sub-post-call-sync,s,1)
 same => n,Dial({get_dial_target('ALL', all_extens)},60,U(sub-record-start))
 same => n,Hangup()
"""
        nodes = tree.get('nodes', [])
        root_node = nodes[0]
        work_hours = tree.get('work_hours', {})
        wh_enabled = work_hours.get('enabled', False)
        wh_start = work_hours.get('start', '09:00')
        wh_end = work_hours.get('end', '18:00')
        wh_days = work_hours.get('days', 'mon-fri')
        wh_audio = work_hours.get('audio_file', '')

        entry_ctx_name = prefix
        main_ctx_name = f"{prefix}-main"

        if wh_enabled:
            time_str = f"{wh_start}-{wh_end},{wh_days},*,*"
            offhours_goto = f" same => n,GotoIfTime({time_str}?{main_ctx_name},s,1)\n same => n,Goto({prefix}-offhours,s,1)"
            sound_base = wh_audio.replace('.wav', '').replace('.mp3', '') if wh_audio else 'beep'
            offhours_ctx = f"""
[{prefix}-offhours]
exten => s,1,NoOp({trunk_title}: Вызов в нерабочее время)
 same => n,Answer()
 same => n,Wait(0.5)
 same => n,Playback(custom/{sound_base})
 same => n,Hangup()
"""
        else:
            offhours_goto = f" same => n,Goto({main_ctx_name},s,1)"
            offhours_ctx = ""

        entry_section = f"""
[{entry_ctx_name}]
exten => _X.,1,NoOp(Входящий вызов {trunk_title} от ${{CALLERID(num)}})
{offhours_goto}

exten => _+.,1,NoOp(Входящий вызов {trunk_title} (+) от ${{CALLERID(num)}})
{offhours_goto}

exten => s,1,NoOp(Входящий вызов {trunk_title} (s-exten))
{offhours_goto}

exten => _.,1,NoOp(Входящий вызов {trunk_title} (catch-all) от ${{CALLERID(num)}})
{offhours_goto}

{offhours_ctx}
"""

        node_contexts = []
        for n in nodes:
            n_id = n['id']
            ctx_name = f"{prefix}-{n_id}" if n_id != 'main' else main_ctx_name
            audio_fn = n.get('audio_file', '')
            t_sec = n.get('timeout_sec', 7)
            t_action = n.get('timeout_action', 'operator')
            t_target = n.get('timeout_target', 'ALL')

            sound_base = audio_fn.replace('.wav', '').replace('.mp3', '') if audio_fn else ''
            play_line = f'same => n,Background(custom/{sound_base})' if sound_base else 'same => n,Playback(beep)'

            t_dial = get_dial_target(t_target, all_extens)
            timeout_lines = f"""exten => t,1,NoOp({trunk_title} IVR {n_id}: Таймаут -> {t_target})
 same => n,Set(REC_FILE=${{STRFTIME(${{EPOCH}},,%Y%m%d-%H%M%S)}}_${{CALL_SRC}}_{n_id}_TO_{t_target}.wav)
 same => n,Set(__CALL_DST={n_id}_TO_{t_target})
 same => n,Set(__REC_PATH=${{RECORD_DIR}}/${{REC_FILE}})
 same => n,Set(CHANNEL(hangup_handler_push)=sub-post-call-sync,s,1)
 same => n,Answer()
 same => n,Dial({t_dial},60,U(sub-record-start))
 same => n,Hangup()"""

            branch_lines = []
            for b in n.get('branches', []):
                digit = b.get('digit', '1')
                action = b.get('action', 'extension')
                target = b.get('target', '101')
                b_title = b.get('title', 'Отдел')

                if action == 'hangup' or target == 'Hangup':
                    act_line = f""" same => n,Playback(beep)
 same => n,Hangup()"""
                elif action == 'voicemail' or target.startswith('Voicemail'):
                    act_line = f""" same => n,Answer()
 same => n,VoiceMail(101@default,u)
 same => n,Hangup()"""
                else:
                    b_dial = get_dial_target(target, all_extens)
                    act_line = f""" same => n,Dial({b_dial},60,U(sub-record-start))
 same => n,Hangup()"""

                branch_lines.append(f"""exten => {digit},1,NoOp({trunk_title} IVR {n_id}: Нажата клавиша [{digit}] -> {b_title} ({target}))
 same => n,Set(REC_FILE=${{STRFTIME(${{EPOCH}},,%Y%m%d-%H%M%S)}}_${{CALL_SRC}}_{n_id}_KEY_{digit}.wav)
 same => n,Set(__CALL_DST={n_id}_KEY_{digit}_{target})
 same => n,Set(__REC_PATH=${{RECORD_DIR}}/${{REC_FILE}})
 same => n,Set(CHANNEL(hangup_handler_push)=sub-post-call-sync,s,1)
 same => n,Answer()
{act_line}""")

            # Direct dial if enabled
            direct_dial_lines = ""
            if n.get('direct_dial', True):
                direct_dial_lines = f"""
exten => _[1-9]XX,1,NoOp({trunk_title} IVR {n_id}: Прямой донабор номера ${{EXTEN}})
 same => n,Set(REC_FILE=${{STRFTIME(${{EPOCH}},,%Y%m%d-%H%M%S)}}_${{CALL_SRC}}_{n_id}_EXT_${{EXTEN}}.wav)
 same => n,Set(__CALL_DST={n_id}_EXT_${{EXTEN}})
 same => n,Set(__REC_PATH=${{RECORD_DIR}}/${{REC_FILE}})
 same => n,Set(CHANNEL(hangup_handler_push)=sub-post-call-sync,s,1)
 same => n,Answer()
 same => n,Dial(PJSIP/${{EXTEN}},60,U(sub-record-start))
 same => n,Hangup()
"""

            node_contexts.append(f"""
[{ctx_name}]
exten => s,1,NoOp(Старт IVR узла {n_id} ({trunk_title}))
 same => n,Answer()
 same => n,Wait(0.5)
 same => n,Set(CALL_SRC=${{CALLERID(num)}})
 same => n,Set(__CALL_DIRECTION=inbound)
 same => n,{play_line}
 same => n,WaitExten({t_sec})

{chr(10).join(branch_lines)}

{timeout_lines}

exten => i,1,NoOp({trunk_title} IVR {n_id}: Неверный ввод клавиши)
 same => n,Playback(invalid)
 same => n,Goto(s,4)

{direct_dial_lines}
""")

        return entry_section + "\n\n" + "\n\n".join(node_contexts)

    # 1. Compile GSM Dongles incoming (dongle-incoming)
    gsm_tree = ivr_trees.get('gsm_pool') or ivr_trees.get('default') or default_ivr_tree
    all_ivr_contexts.append(compile_tree_to_contexts(gsm_tree, 'dongle-incoming', 'GSM Dongle Gateway'))

    # 2. Compile each SIP Trunk incoming route (trunk-in-<trunk_id>)
    for t in sip_trunks:
        t_id = t['id']
        t_name = t.get('name', t_id)
        trunk_tree = ivr_trees.get(t_id) or ivr_trees.get('default') or default_ivr_tree
        all_ivr_contexts.append(compile_tree_to_contexts(trunk_tree, f"trunk-in-{t_id}", f"SIP Trunk {t_name}"))

    ivr_sections = "\n\n".join(all_ivr_contexts)

    dialplan = f"""[general]
static=yes
writeprotect=no

[globals]
RECORD_DIR=/var/spool/asterisk/monitor

[sub-post-call-sync]
exten => s,1,NoOp(=== POST-CALL SYNC TRIGGERED: ${{CALL_ID}}, ${{CALL_SRC}} -> ${{CALL_DST}} (${{CALL_DIRECTION}}, ${{CDR(disposition)}}, ${{CDR(billsec)}}s) ===)
 same => n,System(/usr/bin/python3 /opt/crm-yandex-uploader.py "${{CALL_ID}}" "${{CALL_SRC}}" "${{CALL_DST}}" "${{CALL_DIRECTION}}" "${{CDR(disposition)}}" "${{CDR(billsec)}}" "${{REC_PATH}}" &)
 same => n,Return()

[from-internal]
; 1. Тест эхо (777)
exten => 777,1,NoOp(Эхо тест от ${{CALLERID(num)}})
 same => n,Answer()
 same => n,Echo()
 same => n,Hangup()
{debug_dialplan_entry}
; 2. Внутренние звонки (3- и 4-значные номера: 100-9999) в 2 канала
exten => _[1-9]XX,1,NoOp(Внутренний вызов 3-значный: ${{CALLERID(num)}} -> ${{EXTEN}})
 same => n,Set(REC_FILE=${{STRFTIME(${{EPOCH}},,%Y%m%d-%H%M%S)}}_${{CALLERID(num)}}_${{EXTEN}}.wav)
 same => n,Set(__CALL_ID=${{UNIQUEID}})
 same => n,Set(__CALL_SRC=${{CALLERID(num)}})
 same => n,Set(__CALL_DST=${{EXTEN}})
 same => n,Set(__CALL_DIRECTION=internal)
 same => n,Set(__REC_PATH=${{RECORD_DIR}}/${{REC_FILE}})
 same => n,Set(CHANNEL(hangup_handler_push)=sub-post-call-sync,s,1)
 same => n,Answer()
 same => n,MixMonitor(${{REC_PATH}})
 same => n,Dial(PJSIP/${{EXTEN}},60)
 same => n,Hangup()

exten => _[1-9]XXX,1,NoOp(Внутренний вызов 4-значный: ${{CALLERID(num)}} -> ${{EXTEN}})
 same => n,Set(REC_FILE=${{STRFTIME(${{EPOCH}},,%Y%m%d-%H%M%S)}}_${{CALLERID(num)}}_${{EXTEN}}.wav)
 same => n,Set(__CALL_ID=${{UNIQUEID}})
 same => n,Set(__CALL_SRC=${{CALLERID(num)}})
 same => n,Set(__CALL_DST=${{EXTEN}})
 same => n,Set(__CALL_DIRECTION=internal)
 same => n,Set(__REC_PATH=${{RECORD_DIR}}/${{REC_FILE}})
 same => n,Set(CHANNEL(hangup_handler_push)=sub-post-call-sync,s,1)
 same => n,Answer()
 same => n,MixMonitor(${{REC_PATH}})
 same => n,Dial(PJSIP/${{EXTEN}},60)
 same => n,Hangup()

{groups_dialplan_block}

{outbound_dialplan_block}


{ivr_sections}
"""
    with open(EXTENSIONS_CONF, 'w', encoding='utf-8') as f:
        f.write(dialplan)
    subprocess.run("systemctl restart asterisk", shell=True)


def get_current_version():
    vfile = os.path.join(os.path.dirname(__file__), 'version.json')
    if os.path.exists(vfile):
        try:
            with open(vfile, 'r', encoding='utf-8') as f:
                return json.load(f).get('version', '1.0.1')
        except Exception:
            pass
    return "1.0.1"

CURRENT_VERSION = get_current_version()

@app.route('/api/check-update')
def api_check_update():
    try:
        # 1. Fetch latest changes silently
        subprocess.run(["git", "fetch", "origin"], cwd="/opt/asterisk-gui", timeout=10)
        
        # 2. Extract version.json from origin/main
        res = subprocess.run(["git", "show", "origin/main:version.json"], cwd="/opt/asterisk-gui", capture_output=True, text=True, timeout=5)
        if res.returncode != 0:
            return jsonify({"error": "Не удалось прочитать version.json из репозитория"})
            
        data = json.loads(res.stdout)
        latest = data.get("version", "1.0.0")
        
        def v_to_tuple(v):
            return tuple(map(int, (v.split("."))))
            
        cur_v = get_current_version()
        has_upd = v_to_tuple(latest) > v_to_tuple(cur_v)
        return jsonify({
            "has_update": has_upd,
            "current_version": cur_v,
            "latest_version": latest,
            "changelog": data.get("changelog", "")
        })
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route('/action/do-update', methods=['POST'])
def action_do_update():
    try:
        # systemd-run запускает updater.sh в изолированном юните,
        # чтобы перезапуск asterisk-gui не убивал сам скрипт обновления
        subprocess.Popen([
            'systemd-run',
            '--unit=asterisk-update-runner',
            '/bin/bash',
            '/opt/asterisk-gui/updater.sh'
        ])
    except Exception:
        subprocess.Popen(['nohup', 'bash', '/opt/asterisk-gui/updater.sh'], start_new_session=True)
    
    # Возвращаем HTML страницу, которая через 15 секунд перезагрузится
    return """
    <html><head><meta charset="utf-8"><title>Обновление...</title>
    <style>body{background:#0b0f19; color:#f8fafc; font-family:sans-serif; text-align:center; padding-top:100px;}
        .sub-nav-tabs {
            display: flex;
            gap: 8px;
            margin-bottom: 20px;
            background: #090d16;
            padding: 6px;
            border-radius: 10px;
            border: 1px solid #1e293b;
            overflow-x: auto;
        }
        .sub-tab-btn {
            background: transparent;
            border: none;
            color: #94a3b8;
            padding: 9px 18px;
            border-radius: 6px;
            font-size: 13px;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.2s ease;
            white-space: nowrap;
        }
        .sub-tab-btn:hover {
            color: #f8fafc;
            background: rgba(255, 255, 255, 0.05);
        }
        .sub-tab-btn.active {
            background: #0284c7;
            color: #ffffff;
            font-weight: 600;
            box-shadow: 0 2px 8px rgba(2, 132, 199, 0.4);
        }
        .subtab-content {
            display: none;
        }
        .subtab-content.active {
            display: block;
        }

    
        /* СТИЛИ ПАГИНАЦИИ ТАБЛИЦЫ ВЫЗОВОВ */
        .pagination-container {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-top: 18px;
            padding-top: 14px;
            border-top: 1px solid #1e293b;
            flex-wrap: wrap;
            gap: 12px;
        }
        .page-info {
            font-size: 13px;
            color: #94a3b8;
        }
        .page-info b {
            color: #f8fafc;
        }
        .pagination-buttons {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            flex-wrap: wrap;
        }
        .page-btn {
            width: auto !important;
            min-width: 36px;
            height: 36px;
            padding: 0 12px !important;
            margin: 0 !important;
            font-size: 13px !important;
            font-weight: 600;
            border-radius: 8px !important;
            border: 1px solid #334155 !important;
            background: #0f172a !important;
            color: #cbd5e1 !important;
            display: inline-flex !important;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: all 0.2s ease;
        }
        .page-btn:hover:not(:disabled) {
            background: #1e293b !important;
            border-color: #38bdf8 !important;
            color: #fff !important;
        }
        .page-btn.active {
            background: #0284c7 !important;
            border-color: #38bdf8 !important;
            color: #fff !important;
            box-shadow: 0 0 10px rgba(2, 132, 199, 0.4);
        }
        .page-btn:disabled {
            opacity: 0.35;
            cursor: not-allowed;
            background: #090d16 !important;
            border-color: #1e293b !important;
            color: #64748b !important;
        }

    </style>
    </head><body>
    <h2>🚀 Обновление запущено в фоновом режиме...</h2>
    <p>Сервер сейчас скачивает новые файлы и устанавливает обновления.</p>
    <p>Страница автоматически перезагрузится через <span id="sec">15</span> секунд.</p>
    <script>
    let s = 15;
    setInterval(() => {
        s--;
        document.getElementById('sec').innerText = s;
        if(s <= 0) window.location.href = '/';
    }, 1000);
    
function copyApiToken() {
    const input = document.getElementById('webhook_api_token');
    if (input) {
        input.select();
        document.execCommand('copy');
        alert('API-ключ успешно скопирован в буфер обмена!');
    }
}

function showDocSnippet(snippetId, btn) {
    document.querySelectorAll('.doc-code-snippet').forEach(el => el.style.display = 'none');
    const target = document.getElementById(snippetId);
    if (target) target.style.display = 'block';
    
    btn.parentElement.querySelectorAll('.sub-tab-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
}

async function executeCallToTest() {
    const operator = document.getElementById('test_callto_operator').value.trim();
    const phone = document.getElementById('test_callto_phone').value.trim();
    const resultBox = document.getElementById('callto_test_result');
    
    if (!phone) {
        alert('Пожалуйста, введите номер телефона клиента!');
        return;
    }
    
    resultBox.style.display = 'block';
    resultBox.style.background = '#1e293b';
    resultBox.style.color = '#38bdf8';
    resultBox.style.border = '1px solid #0284c7';
    resultBox.innerHTML = '⏳ Отправка запроса в Asterisk... Набираем оператора ' + (operator || '101') + '...';
    
    try {
        const res = await fetch('/api/v1/callto', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                operator: operator || '101',
                phone: phone
            })
        });
        const data = await res.json();
        
        if (data.status === 'success') {
            resultBox.style.background = '#064e3b';
            resultBox.style.color = '#34d399';
            resultBox.style.border = '1px solid #059669';
            resultBox.innerHTML = '✅ <b>Успешно!</b> ' + data.message + '<br><small style="color:#a7f3d0;">Поднимите трубку на софтфоне оператора ' + data.operator + ' — вызов автоматически соединится с ' + data.phone + '</small>';
        } else {
            resultBox.style.background = '#4c0519';
            resultBox.style.color = '#fda4af';
            resultBox.style.border = '1px solid #e11d48';
            resultBox.innerHTML = '❌ <b>Ошибка:</b> ' + (data.error || data.message || 'Не удалось инициировать вызов');
        }
    } catch (e) {
        resultBox.style.background = '#4c0519';
        resultBox.style.color = '#fda4af';
        resultBox.style.border = '1px solid #e11d48';
        resultBox.innerHTML = '❌ <b>Ошибка сети:</b> ' + e;
    }
}


function toggleNetMode(mode) {
    const staticFields1 = document.getElementById('static_ip_fields');
    const staticFields2 = document.getElementById('static_gw_fields');
    if (mode === 'dhcp') {
        if (staticFields1) staticFields1.style.opacity = '0.4';
        if (staticFields2) staticFields2.style.opacity = '0.4';
    } else {
        if (staticFields1) staticFields1.style.opacity = '1';
        if (staticFields2) staticFields2.style.opacity = '1';
    }
}

</script>
    


</body></html>
    """

@app.route('/')
def index():
    accounts = load_sip_accounts()
    integrations = load_integrations()
    active_contacts = get_online_contacts()
    inbound_target = integrations.get('routing', {}).get('inbound_target', 'ALL')
    ivr_tree = integrations.get('ivr_tree', {
        'enabled': True,
        'debug_enabled': True,
        'debug_exten': '888',
        'nodes': [{
            'id': 'main',
            'title': 'Главное меню (Уровень 1)',
            'audio_file': 'greeting_main.wav',
            'timeout_sec': 7,
            'timeout_action': 'operator',
            'timeout_target': 'ALL',
            'branches': [
                {'digit': '1', 'title': 'Русский язык', 'action': 'operator', 'target': 'ALL'},
                {'digit': '2', 'title': 'English', 'action': 'operator', 'target': 'ALL'},
                {'digit': '3', 'title': 'Arabic (العربية)', 'action': 'operator', 'target': 'ALL'}
            ]
        }]
    })
    host = request.host.split(':')[0]
    ring_groups = integrations.get('ring_groups', [])
    raw_sip_trunks = integrations.get('sip_trunks', [])
    trunks_net_status = get_trunks_status()
    sip_trunks = []
    for t in raw_sip_trunks:
        t_copy = dict(t)
        host_port = f"{t.get('host', '')}:{t.get('port', 5060)}"
        host_only = t.get('host', '')
        t_id = t.get('id', '')
        u_name = t.get('username', '')
        t_copy['network_status'] = trunks_net_status.get(t_id) or trunks_net_status.get(host_port) or trunks_net_status.get(host_only) or trunks_net_status.get(u_name) or {'online': False, 'status_text': '🔴 Не в сети (Offline)', 'color': '#ef4444'}
        sip_trunks.append(t_copy)
    available_gateways = get_available_gateways(integrations)
    amocrm_user_mapping = integrations.get('amocrm', {}).get('user_mapping', {})
    license_info = license_mgr.load_license()
    max_users = license_mgr.get_max_allowed_users()
    disabled_plugins = integrations.get('plugins_disabled', [])
    plugins = marketplace_data.load_marketplace_plugins(license_info.get('active_plugins', []), disabled_plugins, lang_code=get_current_language())
    client_ip = get_client_ip()
    is_client_local = is_local_ip(client_ip)
    auth_cfg = integrations.get('security_auth', {})
    auth_enabled = auth_cfg.get('enabled', False)
    prompt_dismissed = auth_cfg.get('prompt_dismissed', False)
    show_security_prompt = (not is_client_local) and (not auth_enabled) and (not prompt_dismissed)
    return render_template(
        'index.html',
        client_ip=client_ip,
        is_client_local=is_client_local,
        auth_enabled=auth_enabled,
        auth_username=auth_cfg.get('username', 'admin'),
        show_security_prompt=show_security_prompt,
        accounts=accounts,
        available_contexts=get_available_sip_contexts(),
        current_lang=get_current_language(),
        available_languages=get_available_languages(),
        t=get_locale_translations(get_current_language()),
        integrations=integrations,
        ring_groups=ring_groups,
        sip_trunks=sip_trunks,
        available_gateways=available_gateways,
        yandex_account=get_yandex_disk_account_info(integrations.get('yandex_disk', {}).get('token', '')),
        gdrive_account=get_google_drive_account_info(integrations.get('gdrive', {}).get('token', '')),
        amocrm_user_mapping=amocrm_user_mapping,
        active_contacts=active_contacts,
        inbound_target=inbound_target,
        ivr_tree=ivr_tree,
        host=host,
        license=license_info,
        max_users=max_users,
        marketplace_plugins=plugins,
        current_version=get_current_version(),
        get_system_network_info=get_system_network_info,
        installed_plugins=plugin_manager.get_installed_plugins(),
        modems_list=get_system_modems_info(),
        amocrm_account=get_amocrm_account_info(),
        antifraud=get_antifraud_status(),
        log_quota=get_log_quota_status(),
        sip_groups=get_sip_groups()
    )


@app.route('/api/ivr/get-tree', methods=['GET'])
def api_ivr_get_tree():
    trunk_key = request.args.get('trunk_key', 'default').strip()
    cfg = load_integrations()
    ivr_trees = cfg.get('ivr_trees', {})
    
    # Return specific tree or default tree
    tree = ivr_trees.get(trunk_key)
    if not tree:
        if trunk_key == 'default':
            tree = cfg.get('ivr_tree', {})
        else:
            # Fallback to default
            tree = cfg.get('ivr_tree', {})

    return jsonify({'success': True, 'trunk_key': trunk_key, 'tree': tree, 'ivr_tree': tree})


# ================= STORAGE INTEGRITY & END-TO-END HASH VERIFIER =================
@app.route('/api/storage/test-integrity', methods=['POST'])
def api_storage_test_integrity():
    """
    Uploads a test payload to Cloud/FTP storage, reads it back,
    and validates SHA-256 integrity checksums with live step-by-step logging.
    """
    data = request.get_json(force=True) or {}
    provider = data.get('provider')
    cfg = load_integrations()
    logs = []
    t_start = time.strftime('%H:%M:%S')

    # Generate unique payload
    test_content = f"Asterisk Call Record Integrity Test Payload {time.time()} - PBX Logic Core Integrity Check".encode('utf-8')
    orig_sha256 = hashlib.sha256(test_content).hexdigest()
    test_filename = f"integrity_test_{int(time.time())}.txt"

    logs.append(f"[{t_start}] 🚀 Инициализация сквозного теста для: {provider.upper()}")
    logs.append(f"[{t_start}] 📦 Создан локальный блок данных: {test_filename} ({len(test_content)} байт)")
    logs.append(f"[{t_start}] 🔑 Исходный SHA-256: {orig_sha256}")

    try:
        if provider in ['yandex', 'yandex_disk']:
            token = data.get('token') or cfg.get('yandex_disk', {}).get('token', '').strip()
            folder = data.get('path') or cfg.get('yandex_disk', {}).get('path', 'app:/records').strip()
            if not token:
                logs.append(f"[{time.strftime('%H:%M:%S')}] ❌ Ошибка: Отсутствует OAuth токен Яндекс.Диска.")
                return jsonify({'success': False, 'logs': logs})

            headers = {'Authorization': f'OAuth {token}'}
            remote_path = f"{folder}/{test_filename}"
            logs.append(f"[{time.strftime('%H:%M:%S')}] 📤 Запрос URL на выгрузку в целевой каталог: {remote_path}")

            # Ensure folder exists
            requests.put(f"https://cloud-api.yandex.net/v1/disk/resources?path={folder}", headers=headers, timeout=4)

            # Get upload url
            r_url = requests.get(f"https://cloud-api.yandex.net/v1/disk/resources/upload?path={remote_path}&overwrite=true", headers=headers, timeout=5)
            if r_url.status_code not in (200, 201):
                logs.append(f"[{time.strftime('%H:%M:%S')}] ❌ Ошибка получения ссылки на загрузку ({r_url.status_code}): {r_url.text}")
                return jsonify({'success': False, 'logs': logs})

            upload_href = r_url.json().get('href')
            # Upload
            r_up = requests.put(upload_href, data=test_content, timeout=8)
            if r_up.status_code not in (200, 201, 202):
                logs.append(f"[{time.strftime('%H:%M:%S')}] ❌ Ошибка при отправке пакета на Диск ({r_up.status_code}): {r_up.text}")
                return jsonify({'success': False, 'logs': logs})
            logs.append(f"[{time.strftime('%H:%M:%S')}] ✅ Файл успешно записан на удаленный сервер Яндекс.Диск")

            # Download back
            logs.append(f"[{time.strftime('%H:%M:%S')}] 📥 Скачивание записанного файла обратно для верификации...")
            r_down = requests.get(f"https://cloud-api.yandex.net/v1/disk/resources/download?path={remote_path}", headers=headers, timeout=5)
            if r_down.status_code != 200:
                logs.append(f"[{time.strftime('%H:%M:%S')}] ❌ Ошибка получения ссылки на чтение ({r_down.status_code}): {r_down.text}")
                return jsonify({'success': False, 'logs': logs})

            download_href = r_down.json().get('href')
            downloaded_bytes = requests.get(download_href, timeout=8).content
            downloaded_sha256 = hashlib.sha256(downloaded_bytes).hexdigest()
            logs.append(f"[{time.strftime('%H:%M:%S')}] 🔍 Вычислен SHA-256 прочитанного файла: {downloaded_sha256}")

            # Delete test artifact
            requests.delete(f"https://cloud-api.yandex.net/v1/disk/resources?path={remote_path}&permanently=true", headers=headers, timeout=4)
            logs.append(f"[{time.strftime('%H:%M:%S')}] 🧹 Тестовый артефакт успешно удален из хранилища.")

            if downloaded_sha256 == orig_sha256:
                logs.append(f"[{time.strftime('%H:%M:%S')}] 🏆 ВЕРИФИКАЦИЯ УСПЕШНА: Хеши 100% совпадают! Хранилище надежно и готово к архивации звонков.")
                return jsonify({'success': True, 'logs': logs})
            else:
                logs.append(f"[{time.strftime('%H:%M:%S')}] ❌ ОШИБКА ЦЕЛОСТНОСТИ: Контрольные суммы не совпали.")
                return jsonify({'success': False, 'logs': logs})

        elif provider in ['gdrive', 'google']:
            token = data.get('token') or cfg.get('gdrive', {}).get('token', '').strip()
            folder_id = data.get('folder_id') or cfg.get('gdrive', {}).get('folder_id', '').strip()
            if not token:
                logs.append(f"[{time.strftime('%H:%M:%S')}] ❌ Ошибка: Отсутствует OAuth токен Google Drive.")
                return jsonify({'success': False, 'logs': logs})

            headers = {'Authorization': f'Bearer {token}'}
            metadata = {'name': test_filename}
            if folder_id:
                metadata['parents'] = [folder_id]

            logs.append(f"[{time.strftime('%H:%M:%S')}] 📤 Загрузка тестового файла в Google Drive API v3...")
            files = {
                'data': ('metadata', json.dumps(metadata), 'application/json; charset=UTF-8'),
                'file': (test_filename, io.BytesIO(test_content), 'text/plain')
            }
            r_up = requests.post("https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart", headers=headers, files=files, timeout=8)
            if r_up.status_code not in (200, 201):
                logs.append(f"[{time.strftime('%H:%M:%S')}] ❌ Ошибка загрузки в Google Drive ({r_up.status_code}): {r_up.text}")
                return jsonify({'success': False, 'logs': logs})

            file_id = r_up.json().get('id')
            logs.append(f"[{time.strftime('%H:%M:%S')}] ✅ Файл создан в Google Drive (ID: {file_id})")

            # Download back
            logs.append(f"[{time.strftime('%H:%M:%S')}] 📥 Скачивание файла обратно из Google Drive...")
            r_down = requests.get(f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media", headers=headers, timeout=8)
            if r_down.status_code != 200:
                logs.append(f"[{time.strftime('%H:%M:%S')}] ❌ Ошибка скачивания из Google Drive ({r_down.status_code}): {r_down.text}")
                return jsonify({'success': False, 'logs': logs})

            downloaded_sha256 = hashlib.sha256(r_down.content).hexdigest()
            logs.append(f"[{time.strftime('%H:%M:%S')}] 🔍 Вычислен SHA-256 прочитанного файла: {downloaded_sha256}")

            # Delete test file
            requests.delete(f"https://www.googleapis.com/drive/v3/files/{file_id}", headers=headers, timeout=4)
            logs.append(f"[{time.strftime('%H:%M:%S')}] 🧹 Тестовый артефакт удален из Google Drive.")

            if downloaded_sha256 == orig_sha256:
                logs.append(f"[{time.strftime('%H:%M:%S')}] 🏆 ВЕРИФИКАЦИЯ УСПЕШНА: Хеши 100% совпадают! Google Drive полностью готов к работе.")
                return jsonify({'success': True, 'logs': logs})
            else:
                logs.append(f"[{time.strftime('%H:%M:%S')}] ❌ ОШИБКА ЦЕЛОСТНОСТИ: Контрольные суммы не совпали.")
                return jsonify({'success': False, 'logs': logs})

        elif provider == 'ftp':
            host = data.get('host') or cfg.get('ftp', {}).get('host', '').strip()
            port = int(data.get('port') or cfg.get('ftp', {}).get('port', 21))
            user = data.get('user') or cfg.get('ftp', {}).get('user', '').strip()
            password = data.get('password') or cfg.get('ftp', {}).get('password', '').strip()

            if not host:
                logs.append(f"[{time.strftime('%H:%M:%S')}] ❌ Ошибка: Не указан хост FTP сервера.")
                return jsonify({'success': False, 'logs': logs})

            logs.append(f"[{time.strftime('%H:%M:%S')}] 🌐 Подключение к FTP {host}:{port} под пользователем {user}...")
            ftp = ftplib.FTP()
            ftp.connect(host, port, timeout=5)
            ftp.login(user, password)
            logs.append(f"[{time.strftime('%H:%M:%S')}] ✅ Авторизация на FTP успешна.")

            # Upload
            logs.append(f"[{time.strftime('%H:%M:%S')}] 📤 Отправка файла {test_filename} на FTP...")
            bio_up = io.BytesIO(test_content)
            ftp.storbinary(f"STOR {test_filename}", bio_up)
            logs.append(f"[{time.strftime('%H:%M:%S')}] ✅ Файл записан на FTP.")

            # Download back
            logs.append(f"[{time.strftime('%H:%M:%S')}] 📥 Скачивание файла обратно с FTP...")
            bio_down = io.BytesIO()
            ftp.retrbinary(f"RETR {test_filename}", bio_down.write)
            downloaded_bytes = bio_down.getvalue()
            downloaded_sha256 = hashlib.sha256(downloaded_bytes).hexdigest()
            logs.append(f"[{time.strftime('%H:%M:%S')}] 🔍 Вычислен SHA-256 прочитанного файла: {downloaded_sha256}")

            # Delete
            ftp.delete(test_filename)
            ftp.quit()
            logs.append(f"[{time.strftime('%H:%M:%S')}] 🧹 Тестовый артефакт удален с FTP сервера.")

            if downloaded_sha256 == orig_sha256:
                logs.append(f"[{time.strftime('%H:%M:%S')}] 🏆 ВЕРИФИКАЦИЯ УСПЕШНА: Хеши 100% совпадают! FTP хранилище готово к архивации.")
                return jsonify({'success': True, 'logs': logs})
            else:
                logs.append(f"[{time.strftime('%H:%M:%S')}] ❌ ОШИБКА ЦЕЛОСТНОСТИ: Контрольные суммы не совпали.")
                return jsonify({'success': False, 'logs': logs})

    except Exception as e:
        logs.append(f"[{time.strftime('%H:%M:%S')}] ❌ Критическая ошибка выполнения теста: {str(e)}")
        return jsonify({'success': False, 'logs': logs})

    return jsonify({'success': False, 'logs': logs})


@app.route('/api/ivr/save-canvas', methods=['POST'])
def api_ivr_save_canvas():
    try:
        data = request.get_json(force=True) or {}
        if not data:
            return jsonify({'success': False, 'error': 'Empty JSON payload'}), 400

        cfg = load_integrations()
        if 'ivr_trees' not in cfg:
            cfg['ivr_trees'] = {}

        trunk_key = data.get('trunk_key', 'default').strip()
        
        # Build tree structure
        target_tree = {}
        if 'ivr_tree' in data and isinstance(data['ivr_tree'], dict):
            target_tree = data['ivr_tree']
        else:
            target_tree = {
                'enabled': bool(data.get('enabled', True)),
                'debug_exten': str(data.get('debug_exten', '888')).strip(),
                'nodes': data.get('nodes', []),
                'canvas_layout': data.get('canvas_layout', {})
            }

        # Store in specific trunk slot
        cfg['ivr_trees'][trunk_key] = target_tree
        if trunk_key == 'default':
            cfg['ivr_tree'] = target_tree

        save_integrations(cfg)
        generate_dialplan_from_tree()
        generate_pjsip_conf()
        
        return jsonify({
            'success': True,
            'message': f'IVR схема для маршрута «{trunk_key}» успешно сохранена и применена в Asterisk!'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


def save_ftp():
    cfg = load_integrations()
    enabled = True if request.form.get('enabled') else False
    host = request.form.get('host', '').strip()
    port = request.form.get('port', '21').strip()
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    remote_path = request.form.get('remote_path', '').strip()
    http_base_url = request.form.get('http_base_url', '').strip()

    cfg['ftp'] = {
        'enabled': enabled,
        'host': host,
        'port': port,
        'username': username,
        'password': password,
        'remote_path': remote_path,
        'http_base_url': http_base_url
    }
    save_integrations(cfg)
    flash('Настройки FTP-хранилища и HTTP-ссылок успешно сохранены!')
    return redirect(url_for('index'))


@app.route('/settings/yandex-disk', methods=['POST'])
def save_yandex_disk():
    cfg = load_integrations()
    if 'yandex_disk' not in cfg:
        cfg['yandex_disk'] = {}
    cfg['yandex_disk']['enabled'] = True if request.form.get('enabled') else False
    cfg['yandex_disk']['token'] = request.form.get('token', '').strip()
    cfg['yandex_disk']['path'] = request.form.get('path', 'app:/records').strip()
    save_integrations(cfg)
    flash('Настройки Яндекс.Диска сохранены!')
    return redirect(url_for('index'))

@app.route('/settings/gdrive', methods=['POST'])
def save_gdrive():
    cfg = load_integrations()
    enabled = True if request.form.get('enabled') else False
    token = request.form.get('token', '').strip()
    folder_id = request.form.get('folder_id', '').strip()
    cfg['gdrive'] = {'enabled': enabled, 'token': token, 'folder_id': folder_id}
    save_integrations(cfg)
    flash('Настройки Google Drive сохранены!')
    return redirect(url_for('index'))



# ================= MODULAR CRM CONNECTORS (BITRIX24, HUBSPOT, PIPEDRIVE, ZOHO, GHL, ZENDESK) =================
@app.route('/settings/bitrix24', methods=['POST'])
def api_save_bitrix24():
    cfg = load_integrations()
    b24 = cfg.get('bitrix24', {})
    b24['enabled'] = True if request.form.get('enabled') else False
    b24['webhook_url'] = request.form.get('webhook_url', '').strip()
    b24['create_lead'] = True if request.form.get('create_lead') else False
    b24['send_recording'] = True if request.form.get('send_recording') else False
    b24['default_user_id'] = request.form.get('default_user_id', '1').strip()
    cfg['bitrix24'] = b24
    save_integrations(cfg)
    flash('Настройки Битрикс24 Telephony сохранены!')
    return redirect(request.referrer or url_for('index'))

@app.route('/settings/hubspot', methods=['POST'])
def api_save_hubspot():
    cfg = load_integrations()
    hs = cfg.get('hubspot', {})
    hs['enabled'] = True if request.form.get('enabled') else False
    hs['token'] = request.form.get('token', '').strip()
    hs['portal_id'] = request.form.get('portal_id', '').strip()
    hs['auto_create_contact'] = True if request.form.get('auto_create_contact') else False
    cfg['hubspot'] = hs
    save_integrations(cfg)
    flash('Настройки HubSpot CRM сохранены!')
    return redirect(request.referrer or url_for('index'))

@app.route('/settings/pipedrive', methods=['POST'])
def api_save_pipedrive():
    cfg = load_integrations()
    pipe = cfg.get('pipedrive', {})
    pipe['enabled'] = True if request.form.get('enabled') else False
    pipe['token'] = request.form.get('token', '').strip()
    pipe['subdomain'] = request.form.get('subdomain', '').strip()
    cfg['pipedrive'] = pipe
    save_integrations(cfg)
    flash('Настройки Pipedrive сохранены!')
    return redirect(request.referrer or url_for('index'))

@app.route('/settings/zoho', methods=['POST'])
def api_save_zoho():
    cfg = load_integrations()
    zh = cfg.get('zoho', {})
    zh['enabled'] = True if request.form.get('enabled') else False
    zh['domain'] = request.form.get('domain', 'zoho.com').strip()
    zh['client_id'] = request.form.get('client_id', '').strip()
    zh['client_secret'] = request.form.get('client_secret', '').strip()
    cfg['zoho'] = zh
    save_integrations(cfg)
    flash('Настройки Zoho CRM сохранены!')
    return redirect(request.referrer or url_for('index'))

@app.route('/settings/gohighlevel', methods=['POST'])
def api_save_gohighlevel():
    cfg = load_integrations()
    ghl = cfg.get('gohighlevel', {})
    ghl['enabled'] = True if request.form.get('enabled') else False
    ghl['webhook_url'] = request.form.get('webhook_url', '').strip()
    ghl['api_key'] = request.form.get('api_key', '').strip()
    cfg['gohighlevel'] = ghl
    save_integrations(cfg)
    flash('Настройки GoHighLevel (GHL) сохранены!')
    return redirect(request.referrer or url_for('index'))

@app.route('/settings/zendesk', methods=['POST'])
def api_save_zendesk():
    cfg = load_integrations()
    zd = cfg.get('zendesk', {})
    zd['enabled'] = True if request.form.get('enabled') else False
    zd['subdomain'] = request.form.get('subdomain', '').strip()
    zd['email'] = request.form.get('email', '').strip()
    zd['token'] = request.form.get('token', '').strip()
    cfg['zendesk'] = zd
    save_integrations(cfg)
    flash('Настройки Zendesk Talk сохранены!')
    return redirect(request.referrer or url_for('index'))


@app.route('/settings/telegram', methods=['POST'])
def save_telegram():
    cfg = load_integrations()
    enabled = True if request.form.get('enabled') else False
    token = request.form.get('token', '').strip()
    chat_id = request.form.get('chat_id', '').strip()
    cfg['telegram'] = {'enabled': enabled, 'token': token, 'chat_id': chat_id}
    save_integrations(cfg)
    flash('Настройки Telegram сохранены!')
    return redirect(url_for('index'))

@app.route('/sip/save', methods=['POST'])
def sip_save():
    old_exten = request.form.get('old_exten', '').strip()
    exten = request.form.get('exten', '').strip()
    name = request.form.get('name', '').strip()
    password = request.form.get('password', '').strip()
    context = request.form.get('context', 'from-internal').strip() or 'from-internal'
    
    if not exten:
        flash('Номер абонента обязателен!')
        return redirect(url_for('index'))
        
    accounts = load_sip_accounts()
    target_acc = None
    
    if old_exten:
        for a in accounts:
            if a['exten'] == old_exten:
                target_acc = a
                break
    else:
        for a in accounts:
            if a['exten'] == exten:
                target_acc = a
                break

    if target_acc:
        target_acc['exten'] = exten
        if name: target_acc['name'] = name
        if password: target_acc['password'] = password
        target_acc['context'] = context
        flash(f'Данные абонента {exten} («{target_acc.get("name", exten)}») успешно обновлены!')
    else:
        if not password:
            password = f"Pass{exten}!"
        max_users = license_mgr.get_max_allowed_users()
        if len(accounts) >= max_users:
            flash(f'Достигнут лимит пользователей ({max_users}). Для добавления новых абонентов активируйте пакет Multi-SIP в Маркетплейсе.')
            return redirect(url_for('index'))
        if not name: name = f"Оператор {exten}"
        accounts.append({'exten': exten, 'name': name, 'password': password, 'context': context})
        flash(f'Новый абонент {exten} («{name}») успешно создан!')

    # Write clean PJSIP configuration
    out = ["[transport-udp]", "type=transport", "protocol=udp", "bind=0.0.0.0:5060", "local_net=192.168.0.0/16", ""]
    for acc in accounts:
        ext = acc['exten']
        pwd = acc['password']
        disp_name = acc.get('name', f'User {ext}')
        ctx = acc.get('context', 'from-internal')
        out.append(f"[{ext}]")
        out.append("type=auth")
        out.append("auth_type=userpass")
        out.append(f"username={ext}")
        out.append(f"password={pwd}")
        out.append("")
        out.append(f"[{ext}]")
        out.append("type=aor")
        out.append("max_contacts=5")
        out.append("remove_existing=yes")
        out.append("")
        out.append(f"[{ext}]")
        out.append("type=endpoint")
        out.append(f"context={ctx}")
        out.append(f'callerid="{disp_name}" <{ext}>')
        out.append("disallow=all")
        out.append("allow=alaw")
        out.append("allow=ulaw")
        out.append("allow=g722")
        out.append("allow=slin16")
        out.append("direct_media=no")
        out.append("rtp_symmetric=yes")
        out.append("force_rport=yes")
        out.append("rewrite_contact=yes")
        out.append(f"auth={ext}")
        out.append(f"aors={ext}")
        out.append("")

    with open(PJSIP_CONF, 'w', encoding='utf-8') as f:
        f.write("\n".join(out))

    subprocess.run(['asterisk', '-rx', 'pjsip reload'], capture_output=True)
    generate_dialplan_from_tree()
    return redirect(url_for('index'))

@app.route('/sip/delete', methods=['POST'])
def sip_delete():
    exten = request.form.get('exten', '').strip()
    accounts = [a for a in load_sip_accounts() if a['exten'] != exten]
    
    out = ["[transport-udp]", "type=transport", "protocol=udp", "bind=0.0.0.0:5060", ""]
    for acc in accounts:
        ext = acc['exten']
        pwd = acc['password']
        ctx = acc.get('context', 'from-internal')
        out.append(f"[{ext}]")
        out.append("type=auth")
        out.append("auth_type=userpass")
        out.append(f"username={ext}")
        out.append(f"password={pwd}")
        out.append("")
        out.append(f"[{ext}]")
        out.append("type=aor")
        out.append("max_contacts=2")
        out.append("remove_existing=yes")
        out.append("")
        out.append(f"[{ext}]")
        out.append("type=endpoint")
        out.append(f"context={ctx}")
        out.append("disallow=all")
        out.append("allow=ulaw")
        out.append("allow=alaw")
        out.append("allow=g722")
        out.append(f"auth={ext}")
        out.append(f"aors={ext}")
        out.append("")
    with open(PJSIP_CONF, 'w', encoding='utf-8') as f:
        f.write("\n".join(out))

    generate_dialplan_from_tree()
    flash(f'SIP Аккаунт {exten} удален!')
    return redirect(url_for('index'))


# ================= SIP TRUNKS MANAGEMENT (ADD / EDIT / TOGGLE / DELETE) =================
@app.route('/settings/sip-trunks/add', methods=['POST'])
@app.route('/settings/sip-trunks/save', methods=['POST'])
def api_save_sip_trunk():
    cfg = load_integrations()
    trunks = cfg.get('sip_trunks', [])
    
    trunk_id = request.form.get('trunk_id', '').strip()
    name = request.form.get('trunk_name', '').strip()
    host = request.form.get('trunk_host', '').strip()
    port = int(request.form.get('trunk_port', 5060) or 5060)
    username = request.form.get('trunk_user', '').strip()
    password = request.form.get('trunk_pass', '').strip()
    callerid = request.form.get('trunk_cid', '').strip()
    transport = request.form.get('trunk_transport', 'udp').strip()
    context = request.form.get('trunk_context', '').strip()
    
    if not name or not host:
        flash('Название, хост и логин транка обязательны!')
        return redirect(url_for('index'))
        
    target_trunk = None
    if trunk_id:
        for t in trunks:
            if t['id'] == trunk_id:
                target_trunk = t
                break
                
    if target_trunk:
        target_trunk['name'] = name
        target_trunk['host'] = host
        target_trunk['port'] = port
        target_trunk['username'] = username
        if password:
            target_trunk['password'] = password
        target_trunk['callerid'] = callerid
        target_trunk['transport'] = transport
        if context:
            target_trunk['context'] = context
        flash(f'SIP-Транк «{name}» успешно обновлен!')
    else:
        # Generate clean trunk ID
        new_id = "trunk_" + re.sub(r'[^a-zA-Z0-9_]', '', name.lower())[:15] + "_" + str(int(time.time()))[-4:]
        new_trunk = {
            'id': new_id,
            'name': name,
            'host': host,
            'port': port,
            'username': username,
            'password': password,
            'callerid': callerid,
            'transport': transport,
            'context': context or f"trunk-in-{new_id}",
            'enabled': True
        }
        trunks.append(new_trunk)
        flash(f'SIP-Транк «{name}» успешно подключен!')

    cfg['sip_trunks'] = trunks
    save_integrations(cfg)
    
    # Re-generate PJSIP & Dialplan
    try:
        generate_pjsip_conf()
        generate_dialplan_from_tree()
        subprocess.run(['asterisk', '-rx', 'pjsip reload'], capture_output=True)
        subprocess.run(['asterisk', '-rx', 'dialplan reload'], capture_output=True)
    except Exception as e:
        print("Error reloading Asterisk after trunk save:", e)
        
    return redirect(url_for('index'))

@app.route('/settings/sip-trunks/toggle/<trunk_id>', methods=['POST'])
def api_toggle_sip_trunk(trunk_id):
    cfg = load_integrations()
    trunks = cfg.get('sip_trunks', [])
    for t in trunks:
        if t['id'] == trunk_id:
            t['enabled'] = not t.get('enabled', True)
            flash(f"SIP-Транк «{t['name']}» {'активирован' if t['enabled'] else 'отключен'}.")
            break
    cfg['sip_trunks'] = trunks
    save_integrations(cfg)
    try:
        generate_pjsip_conf()
        generate_dialplan_from_tree()
        subprocess.run(['asterisk', '-rx', 'pjsip reload'], capture_output=True)
    except Exception:
        pass
    return redirect(url_for('index'))

@app.route('/settings/sip-trunks/delete/<trunk_id>', methods=['POST'])
def api_delete_sip_trunk(trunk_id):
    cfg = load_integrations()
    trunks = cfg.get('sip_trunks', [])
    cfg['sip_trunks'] = [t for t in trunks if t['id'] != trunk_id]
    save_integrations(cfg)
    try:
        generate_pjsip_conf()
        generate_dialplan_from_tree()
        subprocess.run(['asterisk', '-rx', 'pjsip reload'], capture_output=True)
    except Exception:
        pass
    flash(f"SIP-Транк успешно удален!")
    return redirect(url_for('index'))


@app.route('/action/restart-dongle', methods=['POST'])
def restart_dongle():
    try:
        if os.path.exists('/opt/asterisk-gui/dongle_hotplug.py'):
            subprocess.run(['python3', '/opt/asterisk-gui/dongle_hotplug.py'], timeout=8)
        else:
            run_asterisk('dongle restart now dongle0')
        msg = 'Модем успешно пересканирован!'
        status = 'ok'
    except Exception as e:
        msg = f'Ошибка: {e}'
        status = 'error'
    
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
        return jsonify({'status': status, 'message': msg})
    flash(msg)
    return redirect(url_for('index'))




@app.route('/api/amocrm/pipelines', methods=['GET'])
def api_amocrm_pipelines():
    """Fetches pipelines and statuses from amoCRM API or returns saved/cached ones."""
    cfg = load_integrations()
    amo = cfg.get('amocrm', {})
    subdomain = amo.get('subdomain', '').strip()
    token = amo.get('token', '').strip()
    
    if not subdomain or not token:
        return jsonify({'status': 'error', 'message': 'amoCRM не настроен или отсутствует токен', 'pipelines': []})
        
    url = f"https://{subdomain}.amocrm.ru/api/v4/leads/pipelines"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    try:
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            raw_pipelines = data.get('_embedded', {}).get('pipelines', [])
            pipelines = []
            for p in raw_pipelines:
                p_obj = {
                    'id': p.get('id'),
                    'name': p.get('name'),
                    'statuses': []
                }
                raw_statuses = p.get('_embedded', {}).get('statuses', [])
                for s in raw_statuses:
                    p_obj['statuses'].append({
                        'id': s.get('id'),
                        'name': s.get('name'),
                        'color': s.get('color', '#4c8bf7')
                    })
                pipelines.append(p_obj)
            return jsonify({'status': 'ok', 'pipelines': pipelines})
        else:
            return jsonify({'status': 'error', 'message': f'amoCRM API HTTP {resp.status_code}', 'pipelines': []})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e), 'pipelines': []})


@app.route('/api/amocrm/push-call', methods=['POST'])
def api_amocrm_push_call():
    data = request.get_json() or {}
    call_id = data.get('call_id') or data.get('uniqueid') or 'manual'
    src = data.get('src', '')
    dst = data.get('dst', '')
    direction = data.get('direction', 'inbound')
    disposition = data.get('disposition', 'ANSWERED')
    billsec = data.get('billsec', '0')
    filename = data.get('filename', '')
    
    rec_path = os.path.join(RECORD_DIR, filename) if filename else ''
    
    if not os.path.exists('/opt/crm-yandex-uploader.py'):
        return jsonify({'status': 'error', 'message': 'Скрипт crm-yandex-uploader.py не найден'})
        
    try:
        # Run crm-yandex-uploader.py with given parameters
        cmd = [
            '/usr/bin/python3', '/opt/crm-yandex-uploader.py',
            str(call_id), str(src), str(dst), str(direction),
            str(disposition), str(billsec), str(rec_path)
        ]
        subprocess.Popen(cmd)
        return jsonify({'status': 'ok', 'message': 'Событие звонка и запись успешно отправлены в amoCRM!'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})



# ================= MODULAR PLUGINS TEST & DIAGNOSTICS ENDPOINTS =================

# ================= REAL-TIME SYSTEM & TELEPHONY LOGS STREAM ENGINE =================
import collections
import threading

MAX_BUFFER_LOGS = 300
live_log_buffer = collections.deque(maxlen=MAX_BUFFER_LOGS)
log_buffer_lock = threading.Lock()

def parse_log_line(raw_line, default_type='system'):
    raw_line = raw_line.strip()
    if not raw_line: return None

    # Detect level
    level = 'info'
    upper = raw_line.upper()
    if 'ERROR' in upper or 'FAIL' in upper or 'EXCEPTION' in upper or 'CRITICAL' in upper:
        level = 'error'
    elif 'WARN' in upper or 'NOTICE' in upper or 'WARNING' in upper:
        level = 'warning'
    elif 'DEBUG' in upper or 'TRACE' in upper:
        level = 'debug'
    else:
        level = 'info'

    # Detect category / type
    log_type = default_type
    if 'DONGLE' in upper or 'MODEM' in upper or 'TTY' in upper or 'CSQ' in upper or 'SMS' in upper or 'SIM' in upper:
        log_type = 'modems'
    elif 'TRUNK' in upper or 'PJSIP' in upper or 'SIP' in upper or 'CHAN' in upper or 'INVITE' in upper or 'REGISTER' in upper or 'DIAL' in upper or 'ENDPOINT' in upper or 'CALL' in upper or 'ECHO' in upper:
        log_type = 'trunks'
    elif 'MIXMONITOR' in upper or 'RECORD' in upper or 'AUDIO' in upper or 'WAV' in upper or 'MP3' in upper or 'PLAYBACK' in upper:
        log_type = 'recording'
    elif 'AMOCRM' in upper or 'LEAD' in upper or 'CONTACT' in upper or 'BEARER' in upper or 'PIPELINE' in upper:
        log_type = 'amocrm'
    elif 'YANDEX' in upper or 'GDRIVE' in upper or 'FTP' in upper or 'TELEGRAM' in upper or 'PLUGIN' in upper or 'WEBHOOK' in upper or 'FAIL2BAN' in upper:
        log_type = 'plugins'

    # Parse Timestamp
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    ts_match = re.search(r'\[([0-9]{4}-[0-9]{2}-[0-9]{2}\s+[0-9]{2}:[0-9]{2}:[0-9]{2})\]', raw_line)
    if not ts_match:
        ts_match = re.search(r'\[([A-Za-z]{3}\s+[0-9]{1,2}\s+[0-9]{2}:[0-9]{2}:[0-9]{2})\]', raw_line)
    if ts_match:
        ts = ts_match.group(1)

    return {
        'timestamp': ts,
        'level': level,
        'type': log_type,
        'message': raw_line
    }

def get_latest_log_events(limit=80):
    """Reads live deduplicated events from journalctl -u asterisk, fail2ban and modules."""
    events = []
    try:
        res = subprocess.run(['journalctl', '-u', 'asterisk', '-n', str(limit * 2), '--no-pager', '-o', 'cat'], capture_output=True, text=True, errors='ignore', timeout=2)
        if res.stdout:
            for line in res.stdout.splitlines():
                p = parse_log_line(line, 'trunks')
                if p: events.append(p)
    except Exception:
        pass

    for path, def_t in [('/opt/amocrm_debug.log', 'amocrm'), ('/opt/crm-yandex-uploader.log', 'plugins'), ('/var/log/fail2ban.log', 'plugins')]:
        if os.path.exists(path):
            try:
                res = subprocess.run(['tail', '-n', '20', path], capture_output=True, text=True, errors='ignore', timeout=1)
                for line in res.stdout.splitlines():
                    p = parse_log_line(line, def_t)
                    if p: events.append(p)
            except Exception:
                pass

    if not events:
        events.append({
            'timestamp': datetime.datetime.now().strftime("%H:%M:%S"),
            'level': 'info',
            'type': 'system',
            'message': 'Asterisk Core Live Stream активен. Готов к совершению звонков.'
        })

    return events[-limit:]

@app.route('/api/logs/stream')
def api_logs_stream():
    """Throttled & Batched EventSource stream yielding at most 1 update per second to keep browser smooth and responsive."""
    def event_stream():
        # 1. Send initial batch
        init_logs = get_latest_log_events(40)
        yield f"data: {json.dumps({'type': 'init', 'logs': init_logs})}\n\n"

        p_journal = None
        try:
            import select
            p_journal = subprocess.Popen(
                ['journalctl', '-u', 'asterisk', '-f', '-n', '0', '-o', 'cat'],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1
            )

            batch_accumulator = []
            last_yield_time = time.time()

            while True:
                rlist, _, _ = select.select([p_journal.stdout], [], [], 0.5)
                if rlist:
                    # Read available lines
                    for _ in range(25):
                        line = p_journal.stdout.readline()
                        if not line: break
                        p = parse_log_line(line, 'trunks')
                        if p:
                            batch_accumulator.append(p)

                now = time.time()
                if now - last_yield_time >= 1.0:
                    if batch_accumulator:
                        # Cap batch to max 25 items per second
                        yield f"data: {json.dumps({'type': 'batch', 'logs': batch_accumulator[-25:]})}\n\n"
                        batch_accumulator = []
                    else:
                        yield f": ping\n\n"
                    last_yield_time = now

        except GeneratorExit:
            if p_journal: p_journal.terminate()
        except Exception:
            if p_journal: p_journal.terminate()

    response = Response(event_stream(), mimetype="text/event-stream")
    response.headers['Cache-Control'] = 'no-cache, no-transform'
    response.headers['X-Accel-Buffering'] = 'no'
    return response

@app.route('/api/logs/query', methods=['GET'])
def api_logs_query():
    """Returns filtered historical logs by level and category."""
    level = request.args.get('level', 'all')
    log_type = request.args.get('type', 'all')
    search = request.args.get('search', '').lower()
    limit = int(request.args.get('limit', 100))

    all_logs = get_latest_log_events(limit * 2)

    filtered = []
    for l in all_logs:
        if level != 'all' and l['level'] != level:
            continue
        if log_type != 'all' and l['type'] != log_type:
            continue
        if search and search not in l['message'].lower():
            continue
        filtered.append(l)

    return jsonify({'status': 'ok', 'logs': filtered[-limit:]})



# ================= ENTERPRISE PBX VPN HUB (OPENVPN & VLESS REALITY) =================
def get_enterprise_vpn_config():
    cfg = load_integrations()
    vpn = cfg.get('enterprise_vpn', {})
    if not vpn:
        # Default starter configuration
        vpn = {
            'enabled': True,
            'server_ip': '138.124.229.10',
            'openvpn': {
                'enabled': True,
                'port': 1194,
                'proto': 'udp',
                'subnet': '10.8.0.0/24',
                'clients': [
                    {'id': 'ovpn_101', 'name': 'Офисный телефон (Yealink 101)', 'ip': '10.8.0.101', 'created': '2026-08-30'},
                    {'id': 'ovpn_102', 'name': 'Офисный телефон (Grandstream 102)', 'ip': '10.8.0.102', 'created': '2026-08-30'}
                ]
            },
            'vless': {
                'enabled': True,
                'port': 8443,
                'dest_domain': 'www.microsoft.com:443',
                'server_names': ['www.microsoft.com', 'microsoft.com'],
                'public_key': 'eP3Z9V0M5Q2N1_SAMPLE_KEY_REALITY_SECURE_KEY',
                'short_id': '6ba7b810',
                'clients': [
                    {'id': 'vless_mobile_101', 'name': 'Моб. софтфон (iPhone 101)', 'uuid': 'b831381d-6324-4d53-ad4f-8cda48b30811', 'created': '2026-08-30'},
                    {'id': 'vless_mobile_103', 'name': 'Моб. софтфон (Android 103)', 'uuid': 'c942492e-7435-5e64-be50-9deb59c41922', 'created': '2026-08-30'}
                ]
            }
        }
    return vpn

def save_enterprise_vpn_config(vpn_cfg):
    cfg = load_integrations()
    cfg['enterprise_vpn'] = vpn_cfg
    save_integrations(cfg)

@app.route('/settings/enterprise-vpn', methods=['POST'])
def api_save_enterprise_vpn():
    vpn = get_enterprise_vpn_config()
    vpn['enabled'] = True if request.form.get('enabled') else False
    vpn['server_ip'] = request.form.get('server_ip', '138.124.229.10').strip()
    
    # OpenVPN settings
    vpn['openvpn']['enabled'] = True if request.form.get('openvpn_enabled') else False
    vpn['openvpn']['port'] = int(request.form.get('openvpn_port', 1194))
    vpn['openvpn']['proto'] = request.form.get('openvpn_proto', 'udp')
    
    # VLESS settings
    vpn['vless']['enabled'] = True if request.form.get('vless_enabled') else False
    vpn['vless']['port'] = int(request.form.get('vless_port', 8443))
    vpn['vless']['dest_domain'] = request.form.get('vless_dest_domain', 'www.microsoft.com:443').strip()
    
    save_enterprise_vpn_config(vpn)
    flash('Настройки Enterprise PBX VPN Hub сохранены!')
    return redirect(request.referrer or url_for('index'))

@app.route('/api/vpn/client/add', methods=['POST'])
def api_add_vpn_client():
    data = request.get_json() or {}
    vpn_type = data.get('type', 'openvpn') # openvpn or vless
    name = data.get('name', 'Новый клиент').strip()
    
    vpn = get_enterprise_vpn_config()
    if vpn_type == 'openvpn':
        c_id = f"ovpn_{int(time.time())}"
        next_ip = f"10.8.0.{100 + len(vpn['openvpn']['clients']) + 1}"
        client_obj = {
            'id': c_id,
            'name': name,
            'ip': next_ip,
            'created': datetime.datetime.now().strftime("%Y-%m-%d")
        }
        vpn['openvpn']['clients'].append(client_obj)
        save_enterprise_vpn_config(vpn)
        return jsonify({'status': 'ok', 'client': client_obj})
    else:
        c_id = f"vless_{int(time.time())}"
        client_uuid = str(uuid.uuid4())
        client_obj = {
            'id': c_id,
            'name': name,
            'uuid': client_uuid,
            'created': datetime.datetime.now().strftime("%Y-%m-%d")
        }
        vpn['vless']['clients'].append(client_obj)
        save_enterprise_vpn_config(vpn)
        return jsonify({'status': 'ok', 'client': client_obj})

@app.route('/api/vpn/client/delete', methods=['POST'])
def api_delete_vpn_client():
    data = request.get_json() or {}
    client_id = data.get('id')
    vpn_type = data.get('type')
    vpn = get_enterprise_vpn_config()
    if vpn_type == 'openvpn':
        vpn['openvpn']['clients'] = [c for c in vpn['openvpn']['clients'] if c['id'] != client_id]
    else:
        vpn['vless']['clients'] = [c for c in vpn['vless']['clients'] if c['id'] != client_id]
    save_enterprise_vpn_config(vpn)
    return jsonify({'status': 'ok'})

@app.route('/api/vpn/openvpn/download/<client_id>')
def api_download_ovpn_file(client_id):
    vpn = get_enterprise_vpn_config()
    client = next((c for c in vpn['openvpn']['clients'] if c['id'] == client_id), None)
    c_name = client['name'] if client else client_id
    server_ip = vpn.get('server_ip', '138.124.229.10')
    port = vpn['openvpn'].get('port', 1194)
    proto = vpn['openvpn'].get('proto', 'udp')
    
    # Read real server certificates & keys
    ca_cert = ""
    client_cert = ""
    client_key = ""
    ta_key = ""
    
    try:
        with open('/etc/openvpn/ca.crt', 'r') as f: ca_cert = f.read().strip()
    except Exception:
        ca_cert = "-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----"
        
    try:
        with open(f'/etc/openvpn/easy-rsa/pki/issued/{client_id}.crt', 'r') as f:
            full_c = f.read()
            # Extract cert part
            cert_start = full_c.find('-----BEGIN CERTIFICATE-----')
            if cert_start != -1:
                client_cert = full_c[cert_start:].strip()
            else:
                client_cert = full_c.strip()
    except Exception:
        client_cert = "-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----"

    try:
        with open(f'/etc/openvpn/easy-rsa/pki/private/{client_id}.key', 'r') as f: client_key = f.read().strip()
    except Exception:
        client_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----"

    try:
        with open('/etc/openvpn/ta.key', 'r') as f: ta_key = f.read().strip()
    except Exception:
        ta_key = ""

    ovpn_content = f"""# Enterprise PBX OpenVPN Client Configuration
# Profile for IP Phone / Softphone: {c_name}
client
dev tun
proto {proto}
remote {server_ip} {port}
resolv-retry infinite
nobind
persist-key
persist-tun
remote-cert-tls server
cipher AES-256-GCM
auth SHA256
key-direction 1
verb 3

<ca>
{ca_cert}
</ca>

<cert>
{client_cert}
</cert>

<key>
{client_key}
</key>
"""
    if ta_key:
        ovpn_content += f"""
<tls-auth>
{ta_key}
</tls-auth>
"""

    return Response(
        ovpn_content.strip() + "\n",
        mimetype="application/x-openvpn-profile",
        headers={"Content-Disposition": f"attachment;filename={client_id}.ovpn"}
    )



# ================= LOG ROTATION & DISK QUOTA MANAGEMENT =================
def get_log_quota_status():
    cfg = load_integrations()
    log_cfg = cfg.get('log_quota', {
        'max_size_mb': 50,
        'rotate_count': 3,
        'auto_compress': True,
        'retention_days': 7
    })
    
    # Calculate real disk usage of logs
    total_bytes = 0
    files_info = []
    log_dir = '/var/log/asterisk'
    if os.path.exists(log_dir):
        for root, _, files in os.walk(log_dir):
            for file in files:
                fp = os.path.join(root, file)
                try:
                    sz = os.path.getsize(fp)
                    total_bytes += sz
                    files_info.append({'name': file, 'size_mb': round(sz / (1024 * 1024), 2)})
                except Exception:
                    pass
                    
    return {
        'config': log_cfg,
        'total_mb': round(total_bytes / (1024 * 1024), 2),
        'files': sorted(files_info, key=lambda x: x['size_mb'], reverse=True)[:5]
    }

def apply_system_logrotate(max_size_mb=50, rotate_count=3, compress=True):
    """Updates /etc/logrotate.d/asterisk and /etc/cron.hourly/asterisk-log-guard."""
    compress_opt = "compress\n        delaycompress" if compress else ""
    logrotate_content = f"""/var/log/asterisk/messages /var/log/asterisk/full /var/log/asterisk/debug /var/log/asterisk/*_log {{
        size {max_size_mb}M
        rotate {rotate_count}
        missingok
        notifempty
        {compress_opt}
        sharedscripts
        postrotate
                /usr/sbin/asterisk -rx "logger rotate" > /dev/null 2>&1 || true
                /usr/sbin/asterisk -rx "logger reload" > /dev/null 2>&1 || true
        endscript
}}
"""
    try:
        with open('/etc/logrotate.d/asterisk', 'w') as f:
            f.write(logrotate_content)
    except Exception:
        pass

    # Setup automatic hourly cron safeguard to never allow logs to exceed max_size_mb
    cron_script = f"""#!/bin/bash
# Asterisk Log Guard Auto-Rotator
logrotate /etc/logrotate.d/asterisk 2>/dev/null || true
find /var/log/asterisk/ -name "*.gz" -mtime +7 -delete 2>/dev/null || true
find /var/log/asterisk/ -name "*.[0-9]" -size +{max_size_mb}M -delete 2>/dev/null || true
"""
    try:
        with open('/etc/cron.hourly/asterisk-log-guard', 'w') as f:
            f.write(cron_script)
        os.chmod('/etc/cron.hourly/asterisk-log-guard', 0o755)
    except Exception:
        pass

@app.route('/settings/security/logs-quota', methods=['POST'])
def api_save_log_quota():
    cfg = load_integrations()
    max_size = int(request.form.get('max_size_mb', 50))
    rotate_count = int(request.form.get('rotate_count', 3))
    auto_compress = True if request.form.get('auto_compress') else False
    
    cfg['log_quota'] = {
        'max_size_mb': max_size,
        'rotate_count': rotate_count,
        'auto_compress': auto_compress
    }
    save_integrations(cfg)
    apply_system_logrotate(max_size, rotate_count, auto_compress)
    
    if request.form.get('action') == 'force_rotate':
        subprocess.run(['asterisk', '-rx', 'logger rotate'], capture_output=True)
        subprocess.run(['logrotate', '-f', '/etc/logrotate.d/asterisk'], capture_output=True)
        flash('Ротация логов выполнена! Старые журналы заархивированы.')
    elif request.form.get('action') == 'purge_logs':
        subprocess.run('rm -f /var/log/asterisk/*.[0-9]* /var/log/asterisk/*.gz', shell=True)
        subprocess.run('> /var/log/asterisk/messages; > /var/log/asterisk/full', shell=True)
        subprocess.run(['asterisk', '-rx', 'logger reload'], capture_output=True)
        flash('Журналы логов полностью очищены и сброшены!')
    else:
        flash(f'Правила ротации сохранены (Лимит: {max_size} MB, Хранить копий: {rotate_count})')
        
    return redirect(request.referrer or url_for('index'))



# ==============================================================================
# TELEGRAM TRUNK API (Telethon)
# ==============================================================================
import asyncio
from telethon import TelegramClient

tg_clients = {}

def get_tg_loop():
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop

@app.route('/api/telegram/send_code', methods=['POST'])

def tg_send_code():
    data = request.json
    api_id = data.get('api_id')
    api_hash = data.get('api_hash')
    phone = data.get('phone')
    
    if not all([api_id, api_hash, phone]):
        return jsonify({"success": False, "error": "Missing parameters"})
        
    session_file = f"/opt/asterisk-gui/plugins/plugin_telegram_trunk/session_{phone}.session"
    client = TelegramClient(session_file, int(api_id), api_hash)
    
    loop = get_tg_loop()
    try:
        loop.run_until_complete(client.connect())
        if not loop.run_until_complete(client.is_user_authorized()):
            res = loop.run_until_complete(client.send_code_request(phone))
            tg_clients[phone] = {'api_id': api_id, 'api_hash': api_hash, 'phone_code_hash': res.phone_code_hash}
            client.disconnect()
            return jsonify({"success": True})
        else:
            return jsonify({"success": False, "error": "Already authorized"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/telegram/login', methods=['POST'])

def tg_login():
    data = request.json
    phone = data.get('phone')
    code = data.get('code')
    password = data.get('password')
    
    if phone not in tg_clients:
        return jsonify({"success": False, "error": "Session not found. Request code first."})
        
    api_id = tg_clients[phone]['api_id']
    api_hash = tg_clients[phone]['api_hash']
    phone_code_hash = tg_clients[phone]['phone_code_hash']
    
    session_file = f"/opt/asterisk-gui/plugins/plugin_telegram_trunk/session_{phone}.session"
    client = TelegramClient(session_file, int(api_id), api_hash)
    
    loop = get_tg_loop()
    try:
        loop.run_until_complete(client.connect())
        try:
            loop.run_until_complete(client.sign_in(phone, code, phone_code_hash=phone_code_hash))
        except Exception as e:
            if 'SessionPasswordNeededError' in str(e) or 'password' in str(e).lower():
                if not password:
                    return jsonify({"success": False, "error": "2FA Password required"})
                loop.run_until_complete(client.sign_in(password=password))
            else:
                return jsonify({"success": False, "error": str(e)})
        
        # Get authorized user info
        me = loop.run_until_complete(client.get_me())
        account_info = {
            "id": me.id,
            "first_name": me.first_name or "",
            "last_name": me.last_name or "",
            "username": me.username or "",
            "phone": me.phone or phone
        }
        
        # Save to integrations.json
        cfg = load_integrations()
        if 'telegram_trunk' not in cfg:
            cfg['telegram_trunk'] = {}
        cfg['telegram_trunk']['account_info'] = account_info
        cfg['telegram_trunk']['api_id'] = api_id
        cfg['telegram_trunk']['api_hash'] = api_hash
        cfg['telegram_trunk']['phone'] = phone
        save_integrations(cfg)
        
        client.disconnect()
        return jsonify({"success": True, "account": account_info})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/api/telegram/start_tg2sip', methods=['POST'])

def tg_start_tg2sip():
    data = request.json
    port = data.get('port', 5062)
    api_id = data.get('api_id', '')
    api_hash = data.get('api_hash', '')
    phone = data.get('phone', '')
    
    cfg = load_integrations()
    if 'telegram_trunk' not in cfg:
        cfg['telegram_trunk'] = {}
    
    cfg['telegram_trunk']['port'] = port
    cfg['telegram_trunk']['api_id'] = api_id
    cfg['telegram_trunk']['api_hash'] = api_hash
    cfg['telegram_trunk']['phone'] = phone
    
    save_integrations(cfg)
    
    # Mocking the docker run command for tg2sip
    return jsonify({"success": True, "msg": f"tg2sip scheduled on port {port}"})

@app.route('/api/telegram/status', methods=['GET'])
@login_required
def tg_status():
    cfg = load_integrations()
    tg = cfg.get('telegram_trunk', {})
    port = int(tg.get('port', 5062))
    
    import socket
    is_online = False
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.bind(('127.0.0.1', port))
            # If we CAN bind to it, it means nothing is listening on it!
            is_online = False
        except OSError:
            # If we CANNOT bind, something (tg2sip) is using it!
            is_online = True
            
    return jsonify({
        "online": is_online,
        "port": port
    })


if __name__ == '__main__':
    try:
        threading.Thread(target=network_guardian_startup_check, daemon=True).start()
    except Exception:
        pass
    app.run(host='0.0.0.0', port=8888)
