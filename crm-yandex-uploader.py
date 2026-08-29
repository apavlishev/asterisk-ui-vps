import os
import sys
import json
import time
import glob
import re
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry
import datetime

def log_debug(msg):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    print(line, end="")
    try:
        with open("/opt/amocrm_debug.log", "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def get_robust_session():
    s = requests.Session()
    retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retries)
    s.mount('https://', adapter)
    s.mount('http://', adapter)
    return s

http = get_robust_session()


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
        r = http.get(direct, timeout=3, verify=False)
        return direct
    except Exception as e:
        log_debug(f"[Telegram Check] api.telegram.org unreachable ({e}). Using proxy: {proxy} (IP: {PROXY_IP})")
        return proxy

TG_BASE_URL = resolve_telegram_base_url()

LOCK_DIR = "/var/spool/asterisk/call_locks"

def acquire_call_lock(call_id):
    if not call_id: return True
    try:
        os.makedirs(LOCK_DIR, mode=0o777, exist_ok=True)
    except Exception:
        pass

    clean_id = re.sub(r'[^a-zA-Z0-9_.-]', '_', str(call_id))
    lock_file = os.path.join(LOCK_DIR, f"{clean_id}.lock")
    try:
        fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        log_debug(f"[Dedup] Call {call_id} is already processed by another handler. Skipping duplicate.")
        return False
    except Exception as e:
        log_debug(f"[Dedup Lock Warning]: {e}")
        return True


def normalize_phone_number(raw):
    """Converts any raw incoming/outgoing phone number to standard international E.164 (+7XXXXXXXXXX, +971XXXXXXXX, etc.)."""
    if not raw: return ""
    raw = str(raw).strip()
    if raw.startswith('+'):
        clean_digits = re.sub(r'\D', '', raw)
        return '+' + clean_digits

    clean = re.sub(r'\D', '', raw)
    # Russian Federation / Kazakhstan (11 digits starting with 8 or 7)
    if (clean.startswith('8') or clean.startswith('7')) and len(clean) == 11:
        return '+7' + clean[1:]
    # Russian Federation without country code (10 digits starting with 9)
    elif clean.startswith('9') and len(clean) == 10:
        return '+7' + clean
    # UAE (e.g. 050XXXXXXX -> +97150XXXXXXX, or 971XXXXXXXX)
    elif clean.startswith('0') and len(clean) in [10, 9]:
        return '+971' + clean[1:]
    elif clean.startswith('971') and len(clean) >= 11:
        return '+' + clean
    # Generic international number with 10+ digits
    elif len(clean) >= 10:
        return '+' + clean

    return raw

CONFIG_FILE = '/opt/integrations_config.json'

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log_debug(f"Error loading config: {e}")
    return {}


def upload_to_amocrm_drive(token, file_path):
    if not token or not os.path.exists(file_path):
        return None
    try:
        fn = os.path.basename(file_path)
        file_size = os.path.getsize(file_path)
        if file_size <= 44:
            return None

        log_debug(f"Uploading audio ({file_size} bytes) directly to amoCRM Drive...")
        
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        session_payload = {"file_name": fn, "file_size": file_size}
        r_session = http.post("https://drive.amocrm.ru/v1.0/sessions", headers=headers, json=session_payload, timeout=15)
        
        if r_session.status_code not in [200, 201]:
            log_debug(f"amoCRM Drive session error [{r_session.status_code}]: {r_session.text}")
            return None

        session_data = r_session.json()
        upload_url = session_data.get("upload_url")
        max_part_size = session_data.get("max_part_size", 524288) or 524288

        with open(file_path, "rb") as f:
            if file_size <= max_part_size:
                content = f.read()
                r_up = http.post(upload_url, data=content, timeout=45)
            else:
                # Chunked upload
                part = 1
                while True:
                    chunk = f.read(max_part_size)
                    if not chunk:
                        break
                    r_up = http.post(upload_url, data=chunk, timeout=45)
                    part += 1

        if r_up.status_code in [200, 201]:
            up_res = r_up.json()
            download_url = up_res.get("_links", {}).get("download", {}).get("href")
            if download_url:
                log_debug(f"amoCRM Drive upload SUCCESS (Native URL): {download_url}")
                return download_url
            else:
                log_debug(f"amoCRM Drive response without download link: {r_up.text}")
        else:
            log_debug(f"amoCRM Drive upload chunk failed [{r_up.status_code}]: {r_up.text}")

    except Exception as e:
        log_debug(f"amoCRM Drive upload Exception: {e}")
    return None

def upload_to_gdrive(token, folder_id, file_path):
    if not token or not os.path.exists(file_path):
        return None
    try:
        log_debug(f"Uploading file {file_path} to Google Drive...")
        fn = os.path.basename(file_path)
        metadata = {"name": fn}
        if folder_id:
            metadata["parents"] = [folder_id]

        files = {
            'data': ('metadata', json.dumps(metadata), 'application/json; charset=UTF-8'),
            'file': (fn, open(file_path, 'rb'), 'audio/wav')
        }
        headers = {"Authorization": f"Bearer {token}"}
        resp = http.post(
            "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id,webViewLink,webContentLink",
            headers=headers,
            files=files,
            timeout=30
        )
        if resp.status_code in [200, 201]:
            data = resp.json()
            file_id = data.get("id")
            direct_audio_link = f"https://drive.google.com/file/d/{file_id}/view"
            return direct_audio_link
    except Exception as e:
        log_debug(f"Google Drive Exception: {e}")
    return None

def sync_amocrm(cfg, call_id, src, dst, direction, disposition, billsec, rec_path, gdrive_url, ftp_url=None):
    amo = cfg.get("amocrm", {})
    if not amo.get("enabled"):
        log_debug("amoCRM sync is DISABLED in settings.")
        return

    send_internal = amo.get("send_internal", False)
    if direction == "internal" and not send_internal:
        log_debug(f"Skipping internal call {src} -> {dst} (send_internal is False).")
        return

    subdomain = amo.get("subdomain", "").strip()
    token = amo.get("token", "").strip()

    if not subdomain or not token:
        log_debug("amoCRM Error: Subdomain or Bearer Token is missing!")
        return

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    base_url = f"https://{subdomain}.amocrm.ru"

    # 1. INTERNATIONAL PHONE NUMBER NORMALIZATION
    target_raw = src if direction == "inbound" else dst
    amo_phone = normalize_phone_number(target_raw)

    operator_ext = dst if direction == "inbound" else src
    user_mapping = amo.get("user_mapping", {})
    
    # 2. RESPONSIBLE USER
    resp_user_id = None
    if operator_ext in user_mapping and user_mapping[operator_ext]:
        try: resp_user_id = int(user_mapping[operator_ext])
        except: pass
    if not resp_user_id and user_mapping.get("default"):
        try: resp_user_id = int(user_mapping["default"])
        except: pass
    if not resp_user_id:
        try:
            r_acc = http.get(f"{base_url}/api/v4/account", headers=headers, timeout=5)
            if r_acc.status_code == 200:
                resp_user_id = r_acc.json().get("current_user_id")
        except Exception:
            pass
    if not resp_user_id:
        resp_user_id = 10967978

    duration_s = int(billsec) if str(billsec).isdigit() else 0
    call_status = 4 if disposition == "ANSWERED" else (6 if disposition == "BUSY" else 2)

    # 3. DETERMINE AUDIO PLAYBACK LINK BASED ON SETTINGS
    # audio_mode: 'amocrm_drive' (upload directly), 'cloud_link' (use Yandex/GDrive/FTP), 'none' (no audio)
    audio_mode = amo.get("audio_mode", "cloud_link")
    cloud_provider_pref = amo.get("cloud_provider", "auto") # 'yandex_disk', 'gdrive', 'ftp', 'auto'
    playback_link = ""

    # Fetch public yandex link if available
    yandex_url = ""
    yd_cfg = cfg.get("yandex_disk", {})
    if yd_cfg.get("token") and os.path.exists(rec_path):
        fn = os.path.basename(rec_path)
        yandex_url = f"https://disk.yandex.ru/client/disk/app/records/{fn}"

    if audio_mode == "amocrm_drive":
        log_debug("Uploading audio directly to amoCRM Drive storage...")
        amocrm_audio_link = upload_to_amocrm_drive(token, rec_path)
        playback_link = amocrm_audio_link or ""
    elif audio_mode == "cloud_link":
        if cloud_provider_pref == "ftp" and ftp_url:
            playback_link = ftp_url
        elif cloud_provider_pref == "gdrive" and gdrive_url:
            playback_link = gdrive_url
        elif cloud_provider_pref == "yandex_disk" and yandex_url:
            playback_link = yandex_url
        else:
            # Auto fallback
            playback_link = yandex_url or gdrive_url or ftp_url or ""
    elif audio_mode == "none":
        playback_link = ""

    # 4. DETERMINE PIPELINE & STAGE FOR NEW LEADS (By Inbound Trunk / Channel OR By Operator Participant)
    routing_mode = amo.get("routing_mode", "by_operator") # 'by_channel', 'by_operator', 'default'
    target_pipeline_id = None
    target_status_id = None

    # Channel/Trunk Mapping e.g. {"trunk_test_6044": {"pipeline_id": "123", "status_id": "456"}}
    channel_mapping = amo.get("channel_mapping", {})
    operator_pipeline_mapping = amo.get("operator_pipeline_mapping", {})

    # Detect channel key (from REC_FILE or src/dst tag)
    channel_key = "default"
    if "trunk_" in rec_path or "KEY_" in rec_path:
        for t in cfg.get("sip_trunks", []):
            if t.get("id") and t["id"] in rec_path:
                channel_key = t["id"]
                break

    if routing_mode == "by_channel" and channel_key in channel_mapping:
        target_pipeline_id = channel_mapping[channel_key].get("pipeline_id")
        target_status_id = channel_mapping[channel_key].get("status_id")
    elif routing_mode == "by_operator" and operator_ext in operator_pipeline_mapping:
        target_pipeline_id = operator_pipeline_mapping[operator_ext].get("pipeline_id")
        target_status_id = operator_pipeline_mapping[operator_ext].get("status_id")

    if not target_pipeline_id:
        target_pipeline_id = amo.get("pipeline_id")
    if not target_status_id:
        target_status_id = amo.get("status_id")

    log_debug(f"=== amoCRM START SYNC ===")
    log_debug(f"Call: ID={call_id}, Direction={direction}, Phone={amo_phone}, Duration={duration_s}s, RoutingMode={routing_mode}, Pipeline={target_pipeline_id}/{target_status_id}, AudioLink={playback_link}")

    # 5. ПОИСК СУЩЕСТВУЮЩЕГО КОНТАКТА
    contact_id = None
    search_queries = [amo_phone]
    clean_dig = re.sub(r'\D', '', amo_phone)
    if clean_dig.startswith('7') and len(clean_dig) == 11:
        search_queries.append('8' + clean_dig[1:])
        search_queries.append(clean_dig[1:])
    elif clean_dig.startswith('971') and len(clean_dig) >= 11:
        search_queries.append('0' + clean_dig[3:])
        search_queries.append(clean_dig[3:])

    for q in search_queries:
        try:
            search_url = f"{base_url}/api/v4/contacts?query={q}"
            r = http.get(search_url, headers=headers, timeout=8)
            if r.status_code == 200:
                res_data = r.json()
                contacts = res_data.get("_embedded", {}).get("contacts", [])
                if contacts:
                    contact_id = contacts[0]["id"]
                    log_debug(f"Found existing contact ID: {contact_id} using query: {q}")
                    break
        except Exception as e:
            log_debug(f"Search contact query {q} exception: {e}")

    # 6. ЕСЛИ КОНТАКТА НЕТ — СОЗДАЕМ НОВЫЙ В МЕЖДУНАРОДНОМ ФОРМАТЕ
    if not contact_id:
        try:
            log_debug(f"Creating new contact for {amo_phone}...")
            new_contact_payload = [{
                "name": f"Клиент {amo_phone}",
                "responsible_user_id": int(resp_user_id) if resp_user_id else 10967978,
                "custom_fields_values": [
                    {
                        "field_code": "PHONE",
                        "values": [{"value": amo_phone, "enum_code": "WORK"}]
                    }
                ]
            }]
            r = http.post(f"{base_url}/api/v4/contacts", headers=headers, json=new_contact_payload, timeout=10)
            if r.status_code in [200, 201]:
                res_data = r.json()
                contact_id = res_data["_embedded"]["contacts"][0]["id"]
                log_debug(f"Created Contact ID: {contact_id}")
        except Exception as e:
            log_debug(f"Create contact exception: {e}")

    # 7. ПРОВЕРЯЕМ НАЛИЧИЕ АКТИВНОЙ СДЕЛКИ У КОНТАКТА
    lead_id = None
    if contact_id:
        try:
            r = http.get(f"{base_url}/api/v4/contacts/{contact_id}?with=leads", headers=headers, timeout=10)
            if r.status_code == 200:
                c_data = r.json()
                leads = c_data.get("_embedded", {}).get("leads", [])
                if leads:
                    # Check if lead is active (not closed successfully or lost)
                    lead_id = leads[0]["id"]
                    log_debug(f"Contact has existing Lead ID: {lead_id}")
        except Exception as e:
            log_debug(f"Get contact leads exception: {e}")

    # 8. ЕСЛИ АКТИВНОЙ СДЕЛКИ НЕТ — СОЗДАЕМ НОВУЮ В ЦЕЛЕВУЮ ВОРОНКУ И ЭТАП
    if contact_id and not lead_id:
        try:
            log_debug(f"No active lead found for contact. Creating new Lead in Pipeline={target_pipeline_id}, Stage={target_status_id}...")
            lead_payload = [{
                "name": f"Звонок: {amo_phone}",
                "responsible_user_id": int(resp_user_id) if resp_user_id else 10967978,
                "_embedded": {
                    "contacts": [{"id": contact_id}]
                }
            }]
            if target_pipeline_id:
                try: lead_payload[0]["pipeline_id"] = int(target_pipeline_id)
                except: pass
            if target_status_id:
                try: lead_payload[0]["status_id"] = int(target_status_id)
                except: pass

            r = http.post(f"{base_url}/api/v4/leads", headers=headers, json=lead_payload, timeout=10)
            if r.status_code in [200, 201]:
                l_res = r.json()
                lead_id = l_res["_embedded"]["leads"][0]["id"]
                log_debug(f"Successfully Created Lead ID: {lead_id} (Pipeline: {target_pipeline_id}, Stage: {target_status_id})")
            else:
                log_debug(f"Create lead error [{r.status_code}]: {r.text}")
        except Exception as e:
            log_debug(f"Create lead exception: {e}")

    # 9. ОТПРАВЛЯЕМ КАРТОЧКУ ЗВОНКА В /api/v4/calls С АУДИОССЫЛКОЙ
    try:
        call_payload = [{
            "uniq": str(call_id),
            "direction": "inbound" if direction == "inbound" else "outbound",
            "duration": duration_s,
            "source": "Asterisk PBX",
            "phone": amo_phone,
            "responsible_user_id": int(resp_user_id) if resp_user_id else 10967978,
            "call_status": call_status,
            "call_result": f"Статус: {disposition}. Длительность: {duration_s} сек."
        }]
        if playback_link:
            call_payload[0]["link"] = playback_link

        log_debug(f"Posting call event to /api/v4/calls: {json.dumps(call_payload, ensure_ascii=False)}")
        r = http.post(f"{base_url}/api/v4/calls", headers=headers, json=call_payload, timeout=10)
        log_debug(f"Call post response [{r.status_code}]: {r.text}")
    except Exception as e:
        log_debug(f"Call event exception: {e}")

    log_debug(f"=== amoCRM SYNC COMPLETE ===")


def send_telegram_notification(cfg, call_id, src, dst, direction, disposition, billsec, rec_path, playback_url=None):
    tg = cfg.get("telegram", {})
    if not tg.get("enabled"): return
    token = tg.get("token", "").strip()
    chat_ids_raw = str(tg.get("chat_id", "")).strip()
    if not token or not chat_ids_raw: return

    chat_ids = [c.strip() for c in re.split(r'[,\s\n]+', chat_ids_raw) if c.strip()]
    if not chat_ids: return

    duration_m = int(billsec) // 60
    duration_s = int(billsec) % 60
    duration_str = f"{duration_m} мин {duration_s} сек" if duration_m > 0 else f"{duration_s} сек"

    text = f"📞 <b>Новый звонок!</b>\n\n"
    text += f"🔹 <b>Направление:</b> {direction}\n"
    text += f"🔹 <b>Кто (Src):</b> {src}\n"
    text += f"🔹 <b>Кому (Dst):</b> {dst}\n"
    text += f"🔹 <b>Статус:</b> {disposition}\n"
    text += f"🔹 <b>Время разговора:</b> {duration_str}\n"
    if playback_url:
        text += f"🎧 <b>Запись разговора:</b> <a href=\"{playback_url}\">Слушать / Скачать</a>\n"
    text += f"🕒 <b>Время:</b> {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

    for cid in chat_ids:
        try:
            url = f"{TG_BASE_URL}/bot{token}/sendMessage"
            params = {'chat_id': cid, 'text': text, 'parse_mode': 'HTML'}
            r_tg = http.post(url, params=params, timeout=10, verify=False)
            if r_tg.status_code != 200:
                log_debug(f"Telegram sendMessage error [{r_tg.status_code}]: {r_tg.text}")
            else:
                log_debug(f"Telegram notification sent to {cid} successfully.")
        except Exception as e:
            log_debug(f"Telegram notification to {cid} exception: {e}")

def main():
    if len(sys.argv) < 8:
        print("Usage: crm-yandex-uploader.py <CALL_ID> <SRC> <DST> <DIRECTION> <DISPOSITION> <BILLSEC> <REC_PATH>")
        return

    call_id = sys.argv[1]
    src = sys.argv[2]
    dst = sys.argv[3]
    direction = sys.argv[4]
    disposition = sys.argv[5]
    billsec = sys.argv[6]
    rec_path = sys.argv[7]

    if not acquire_call_lock(call_id):
        return

    # Fallback if Asterisk didn't pass full REC_PATH
    if (not rec_path or not os.path.exists(rec_path)) and (src or dst):
        mon_dir = "/var/spool/asterisk/monitor"
        clean_src = re.sub(r'[^0-9]', '', str(src))
        clean_dst = re.sub(r'[^0-9]', '', str(dst))
        try:
            now = time.time()
            candidates = []
            for fn in os.listdir(mon_dir):
                if fn.endswith('.wav'):
                    matched = False
                    if clean_src and (clean_src in fn or (len(clean_src) >= 7 and clean_src[-7:] in fn)):
                        matched = True
                    elif clean_dst and (clean_dst in fn or (len(clean_dst) >= 7 and clean_dst[-7:] in fn)):
                        matched = True
                    elif "ALL" in fn or "main" in fn:
                        matched = True

                    if matched:
                        fp = os.path.join(mon_dir, fn)
                        mtime = os.path.getmtime(fp)
                        if now - mtime < 300 and os.path.getsize(fp) > 44:
                            candidates.append((mtime, fp))
            if candidates:
                candidates.sort(reverse=True)
                rec_path = candidates[0][1]
                log_debug(f"Auto-discovered recent audio recording: {rec_path} ({os.path.getsize(rec_path)} bytes)")
        except Exception as e:
            log_debug(f"Audio auto-discovery exception: {e}")

    cfg = load_config()
    
    # 1. Загрузка на FTP / Web-хост
    ftp_url = None
    ftp_cfg = cfg.get("ftp", {})
    if ftp_cfg.get("enabled"):
        ftp_url = upload_to_ftp(ftp_cfg, rec_path)

    # 2. Загрузка в Google Drive (архив)
    gdrive_url = None
    gd_cfg = cfg.get("gdrive", {})
    if gd_cfg.get("enabled"):
        gdrive_url = upload_to_gdrive(gd_cfg.get("token"), gd_cfg.get("folder_id"), rec_path)

    # 3. Синхронизация с amoCRM (чистая карточка звонка с плеером)
    sync_amocrm(cfg, call_id, src, dst, direction, disposition, billsec, rec_path, gdrive_url, ftp_url)

    # 4. Отправка в Telegram (после создания карточки amoCRM со ссылкой)
    send_telegram_notification(cfg, call_id, src, dst, direction, disposition, billsec, rec_path, playback_url=(ftp_url or gdrive_url))

if __name__ == "__main__":
    main()

