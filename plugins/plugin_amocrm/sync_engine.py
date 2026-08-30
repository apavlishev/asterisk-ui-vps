import os
import json
import re
import requests

def normalize_e164_phone(raw):
    """Normalizes phone numbers to standard E.164 (+7..., +971..., etc.)."""
    if not raw: return ""
    raw = str(raw).strip()
    if raw.startswith('+'):
        clean_digits = re.sub(r'\D', '', raw)
        return '+' + clean_digits

    clean = re.sub(r'\D', '', raw)
    if (clean.startswith('8') or clean.startswith('7')) and len(clean) == 11:
        return '+7' + clean[1:]
    elif clean.startswith('9') and len(clean) == 10:
        return '+7' + clean
    elif clean.startswith('0') and len(clean) in [10, 9]:
        return '+971' + clean[1:]
    elif clean.startswith('971') and len(clean) >= 11:
        return '+' + clean
    elif len(clean) >= 10:
        return '+' + clean

    return raw

def test_amocrm_connection(subdomain, token):
    """Tests amoCRM account connectivity and returns account details."""
    logs = []
    if not subdomain or not token:
        return False, "Субдомен или Bearer токен не указаны.", logs

    try:
        logs.append(f"[1/3] Проверка подключения к https://{subdomain}.amocrm.ru...")
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        r = requests.get(f"https://{subdomain}.amocrm.ru/api/v4/account", headers=headers, timeout=10)
        
        if r.status_code != 200:
            err = f"Ошибка amoCRM API [{r.status_code}]: {r.text}"
            logs.append(err)
            return False, err, logs

        acc = r.json()
        logs.append(f"✔ Авторизация успешна. Аккаунт ID: {acc.get('id')}, Название: {acc.get('name')}")

        logs.append("[2/3] Загрузка списка воронок и этапов продаж...")
        r_pipe = requests.get(f"https://{subdomain}.amocrm.ru/api/v4/leads/pipelines", headers=headers, timeout=10)
        if r_pipe.status_code == 200:
            pipes = r_pipe.json().get('_embedded', {}).get('pipelines', [])
            logs.append(f"✔ Обнаружено {len(pipes)} активных воронок продаж.")

        logs.append("[3/3] amoCRM готова к сквозной синхронизации звонков и сделок.")
        return True, "Интеграция с amoCRM полностью активна!", logs

    except Exception as e:
        err = f"Исключение при подключении к amoCRM: {str(e)}"
        logs.append(err)
        return False, err, logs

def upload_audio_to_amocrm_drive(token, file_path):
    """Directly uploads MP3/WAV recording to native amoCRM Drive storage."""
    if not token or not os.path.exists(file_path):
        return None
    try:
        fn = os.path.basename(file_path)
        file_size = os.path.getsize(file_path)
        if file_size <= 44: return None

        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        session_payload = {"file_name": fn, "file_size": file_size}
        r_session = requests.post("https://drive.amocrm.ru/v1.0/sessions", headers=headers, json=session_payload, timeout=15)
        
        if r_session.status_code not in [200, 201]:
            return None

        session_data = r_session.json()
        upload_url = session_data.get("upload_url")
        max_part_size = session_data.get("max_part_size", 524288) or 524288

        with open(file_path, "rb") as f:
            if file_size <= max_part_size:
                content = f.read()
                r_up = requests.post(upload_url, data=content, timeout=45)
            else:
                while True:
                    chunk = f.read(max_part_size)
                    if not chunk: break
                    r_up = requests.post(upload_url, data=chunk, timeout=45)

        if r_up.status_code in [200, 201]:
            return r_up.json().get("_links", {}).get("download", {}).get("href")
    except Exception as e:
        print(f"[amoCRM Drive Upload Error]: {e}")
    return None
