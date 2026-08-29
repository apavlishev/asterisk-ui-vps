#!/usr/bin/env python3
import time
import subprocess
import urllib.request
import urllib.parse
import json
import re
import socket
import os
import datetime
import configparser


def get_tg_settings():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                tg = cfg.get("telegram", {})
                raw_cids = str(tg.get("chat_id", "")).strip()
                cids = [c.strip() for c in re.split(r"[,\s\n]+", raw_cids) if c.strip()]
                return tg.get("enabled", False), tg.get("token", "").strip(), cids
        except Exception:
            pass
    return False, "", ""

PJSIP_CONF = "/etc/asterisk/pjsip.conf"
CONFIG_FILE = "/opt/integrations_config.json"



# Интеллектуальный DNS резолвер для прокси telegram.dentaldate.ae -> 185.243.76.230
PROXY_HOST = "telegram.dentaldate.ae"
PROXY_IP = "185.243.76.230"

def ensure_hosts_entry():
    try:
        with open("/etc/hosts", "r") as f:
            content = f.read()
        if PROXY_HOST not in content:
            with open("/etc/hosts", "a") as f:
                f.write(f"\n{PROXY_IP} {PROXY_HOST}\n")
    except Exception:
        pass

ensure_hosts_entry()

def resolve_telegram_base_url():
    direct = "https://api.telegram.org"
    proxy = f"https://{PROXY_HOST}"
    try:
        req = urllib.request.Request(direct, headers={"User-Agent": "Mozilla/5.0"})
        import ssl; ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=3, context=ctx) as resp:
            print(f"[Telegram Startup Check] api.telegram.org is AVAILABLE. Working directly: {direct}")
            return direct
    except Exception as e:
        print(f"[Telegram Startup Check] api.telegram.org is BLOCKED/UNREACHABLE ({e}). Switching to proxy: {proxy} (IP: {PROXY_IP})")
        return proxy

TG_BASE_URL = resolve_telegram_base_url()

cached_public_ip = "Определяется..."
last_public_ip_check = 0

def run_cmd(cmd):
    try:
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=2)
        return res.stdout.strip()
    except Exception as e:
        return str(e)

def get_public_ip():
    global cached_public_ip, last_public_ip_check
    now = time.time()
    if now - last_public_ip_check < 300 and cached_public_ip != "Определяется...":
        return cached_public_ip
    for service in ["https://api.ipify.org", "https://ifconfig.me/ip"]:
        try:
            req = urllib.request.Request(service, headers={"User-Agent": "curl/7.88.1"})
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                val = resp.read().decode().strip()
                if val:
                    cached_public_ip = val
                    last_public_ip_check = now
                    return cached_public_ip
        except Exception:
            pass
    return cached_public_ip

def format_duration(seconds):
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    sec = seconds % 60
    parts = []
    if hours > 0: parts.append(f"{hours} ч")
    if minutes > 0 or hours > 0: parts.append(f"{minutes} мин")
    parts.append(f"{sec} сек")
    return " ".join(parts)

def get_integrations_status():
    amo_status = "❌ Выключено"
    gd_status = "❌ Выключено"
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                amo = cfg.get("amocrm", {})
                if amo.get("enabled"):
                    sub = amo.get("subdomain", "не указан")
                    has_tok = "есть" if amo.get("token") else "нет токена"
                    amo_status = f"✅ Активно (Субдомен: <code>{sub}</code>, Токен: {has_tok})"
                
                gd = cfg.get("gdrive", {})
                if gd.get("enabled"):
                    folder = gd.get("folder_id", "Корень диска (Root)")
                    has_tok = "есть" if gd.get("token") else "нет токена"
                    gd_status = f"✅ Активно (Folder: <code>{folder}</code>, Токен: {has_tok})"
        except Exception:
            pass

    local_ip = "192.168.0.109"
    try:
        res = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True, timeout=1)
        parts = res.stdout.strip().split()
        if "dev" in parts:
            interface = parts[parts.index("dev") + 1]
            res_ip = subprocess.run(["ip", "-o", "-f", "inet", "addr", "show", interface], capture_output=True, text=True, timeout=1)
            for line in res_ip.stdout.splitlines():
                tokens = line.split()
                if "inet" in tokens:
                    local_ip = tokens[tokens.index("inet") + 1].split('/')[0]
                    break
    except Exception: pass
    return (
        "⚙️ <b>СТАТУС ИНТЕГРАЦИЙ И АУДИОЗАПИСИ:</b>\n\n"
        f"🏢 <b>amoCRM (Выгрузка звонков):</b>\n{amo_status}\n\n"
        f"📁 <b>Google Drive (Записи звонков):</b>\n{gd_status}\n\n"
        f"<i>(Управление и ввод токенов в Веб-панели: http://{local_ip}:8080)</i>"
    )

def get_network_only():
    gateway = "Unknown"
    interface = "eth0"
    try:
        res = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True, timeout=1)
        parts = res.stdout.strip().split()
        if "via" in parts: gateway = parts[parts.index("via") + 1]
        if "dev" in parts: interface = parts[parts.index("dev") + 1]
    except Exception:
        pass

    local_ip = "Unknown"
    subnet = "Unknown"
    try:
        res = subprocess.run(["ip", "-o", "-f", "inet", "addr", "show", interface], capture_output=True, text=True, timeout=1)
        for line in res.stdout.splitlines():
            tokens = line.split()
            if "inet" in tokens:
                cidr = tokens[tokens.index("inet") + 1]
                local_ip = cidr.split('/')[0]
                subnet = cidr
                break
    except Exception:
        pass

    pub_ip = get_public_ip()
    return (
        "🌐 <b>СЕТЕВЫЕ ПАРАМЕТРЫ:</b>\n\n"
        f"📍 <b>Локальный IP:</b> <code>{local_ip}</code>\n"
        f"📐 <b>Подсеть:</b> <code>{subnet}</code>\n"
        f"🚪 <b>Шлюз:</b> <code>{gateway}</code>\n"
        f"🌍 <b>Внешний IP:</b> <code>{pub_ip}</code>\n\n"
        f"🔗 <b>Веб-панель:</b> http://{local_ip}:8080\n"
        f"📞 <b>SIP Порт:</b> {local_ip}:5060"
    )

def get_call_stats():
    csv_path = "/var/log/asterisk/cdr-csv/Master.csv"
    now = datetime.datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    week_ago = now - datetime.timedelta(days=7)

    today_calls = today_ans = today_sec = 0
    week_calls = week_ans = week_sec = 0
    total_calls = total_ans = total_sec = 0

    if os.path.exists(csv_path):
        try:
            with open(csv_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    parts = [p.strip('"') for p in line.strip().split('","')]
                    if len(parts) >= 15:
                        calldate_str = parts[9]
                        billsec = int(parts[13]) if parts[13].isdigit() else 0
                        disposition = parts[14]
                        try:
                            calldate = datetime.datetime.strptime(calldate_str, "%Y-%m-%d %H:%M:%S")
                        except Exception:
                            continue
                        total_calls += 1
                        if disposition == "ANSWERED":
                            total_ans += 1
                            total_sec += billsec
                        if calldate.date() == now.date():
                            today_calls += 1
                            if disposition == "ANSWERED":
                                today_ans += 1
                                today_sec += billsec
                        if calldate >= week_ago:
                            week_calls += 1
                            if disposition == "ANSWERED":
                                week_ans += 1
                                week_sec += billsec
        except Exception:
            pass

    return (
        "📈 <b>СТАТИСТИКА ЗВОНКОВ:</b>\n\n"
        f"📅 <b>За сегодня ({today_str}):</b>\n"
        f" • Всего: <b>{today_calls}</b> | Отвечено: <b>{today_ans}</b>\n"
        f" • Разговоры: <b>{format_duration(today_sec)}</b>\n\n"
        f"🗓 <b>За 7 дней (неделя):</b>\n"
        f" • Всего: <b>{week_calls}</b> | Отвечено: <b>{week_ans}</b>\n"
        f" • Разговоры: <b>{format_duration(week_sec)}</b>\n\n"
        f"📊 <b>За всё время:</b>\n"
        f" • Всего: <b>{total_calls}</b> | Отвечено: <b>{total_ans}</b>\n"
        f" • Разговоры: <b>{format_duration(total_sec)}</b>"
    )

def get_sip_accounts_text():
    accounts = []
    cfg = configparser.ConfigParser()
    if os.path.exists(PJSIP_CONF):
        cfg.read(PJSIP_CONF)
        for s in cfg.sections():
            if s == 'transport-udp': continue
            if 'type' in cfg[s] and cfg[s]['type'] == 'endpoint':
                pwd = '***'
                if f"{s}-auth" in cfg.sections() and 'password' in cfg[f"{s}-auth"]:
                    pwd = cfg[f"{s}-auth"]['password']
                accounts.append(f"• Номер: <code>{s}</code> | Пароль: <code>{pwd}</code>")
    local_ip = "192.168.0.109"
    try:
        res = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True, timeout=1)
        parts = res.stdout.strip().split()
        if "dev" in parts:
            interface = parts[parts.index("dev") + 1]
            res_ip = subprocess.run(["ip", "-o", "-f", "inet", "addr", "show", interface], capture_output=True, text=True, timeout=1)
            for line in res_ip.stdout.splitlines():
                tokens = line.split()
                if "inet" in tokens:
                    local_ip = tokens[tokens.index("inet") + 1].split('/')[0]
                    break
    except Exception: pass
    if not accounts:
        return "Нет созданных SIP учетных записей."
    return "👥 <b>СПИСОК SIP УЧЕТНЫХ ЗАПИСЕЙ:</b>\n\n" + "\n".join(accounts) + f"\n\n<i>Веб-панель: http://{local_ip}:8080</i>"

def send_message(token, chat_id, text, keyboard=None):
    if not token or not chat_id: return
    url = f"{TG_BASE_URL}/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if keyboard: payload["reply_markup"] = keyboard
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        import ssl; ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE; urllib.request.urlopen(req, timeout=4, context=ctx)
    except Exception as e:
        print("Send error:", e)

def get_keyboard():
    return {
        "keyboard": [
            [{"text": "📊 Полный отчет"}, {"text": "📈 Статистика звонков"}],
            [{"text": "👥 SIP Аккаунты"}, {"text": "⚙️ amoCRM / GDrive"}],
            [{"text": "🌐 Сеть и IP"}, {"text": "📡 Статус модема"}],
            [{"text": "🔄 Перезапустить PBX"}]
        ],
        "resize_keyboard": True
    }

def process_command(token, chat_id, text):
    t = text.strip()
    if t in ["/start", "меню", "help"]:
        send_message(token, chat_id, "👋 Меню управления Asterisk PBX:", get_keyboard())
    elif "amoCRM" in t or "GDrive" in t or "Drive" in t or t == "/crm":
        send_message(token, chat_id, get_integrations_status(), get_keyboard())
    elif "Полный отчет" in t or t == "/report":
        net = get_network_only()
        uptime = run_cmd("uptime -p")
        dongle = run_cmd("asterisk -rx 'dongle show devices'")
        crm = get_integrations_status()
        full = f"📊 <b>ПОЛНЫЙ СТАТУС:</b>\n\n⏱ <b>Uptime:</b> {uptime}\n\n{net}\n\n{crm}\n\n📡 <b>Модем:</b>\n<pre>{dongle}</pre>"
        send_message(token, chat_id, full, get_keyboard())
    elif "Сеть и IP" in t or t == "/ip":
        send_message(token, chat_id, get_network_only(), get_keyboard())
    elif "Статистика" in t or t == "/stats":
        send_message(token, chat_id, get_call_stats(), get_keyboard())
    elif "SIP Аккаунты" in t or t == "/sip_list":
        send_message(token, chat_id, get_sip_accounts_text(), get_keyboard())
    elif "модем" in t.lower() or t == "/dongle":
        stat = run_cmd("asterisk -rx 'dongle show devices'")
        send_message(token, chat_id, f"📡 <b>Статус модема:</b>\n<pre>{stat}</pre>", get_keyboard())
    elif "Перезапустить" in t or t == "/restart":
        res = run_cmd("asterisk -rx 'core restart gracefully'")
        send_message(token, chat_id, f"🔄 <b>Asterisk перезапущен:</b>\n<code>{res}</code>", get_keyboard())
    else:
        send_message(token, chat_id, "Команда принята: " + t, get_keyboard())

def main():
    offset = 0
    get_public_ip()
    
    # Send startup message
    enabled, token, admin_chat_ids = get_tg_settings()
    if enabled and token and admin_chat_ids:
        send_message(token, admin_chat_ids, "🔄 <b>Asterisk PBX сервис запущен (или перезагружен)</b>\nБот готов к приему команд.", get_keyboard())
    
    while True:
        try:
            enabled, token, admin_chat_ids = get_tg_settings()
            if not enabled or not token or not admin_chat_ids:
                time.sleep(5)
                continue

            url = f"{TG_BASE_URL}/bot{token}/getUpdates?offset={offset}&timeout=2"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            import ssl; ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
            with urllib.request.urlopen(req, timeout=5, context=ctx) as resp:
                data = json.loads(resp.read().decode())
                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    msg = update.get("message", {})
                    chat_id = msg.get("chat", {}).get("id")
                    text = msg.get("text", "")
                    if chat_id and str(chat_id) == admin_chat_ids and text:
                        process_command(token, chat_id, text)
        except Exception as e:
            time.sleep(1)

if __name__ == "__main__":
    main()

