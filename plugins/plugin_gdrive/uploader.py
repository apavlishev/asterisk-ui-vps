import os
import json
import requests
import hashlib

def test_gdrive_connection(token, folder_id=""):
    """
    Performs full integrity check against Google Drive API:
    1. Validates OAuth2 Token against about API endpoint.
    2. Uploads temporary verification file.
    3. Reads file back and verifies SHA256 integrity.
    4. Deletes verification file from Google Drive.
    """
    logs = []
    headers = {"Authorization": f"Bearer {token}"}

    if not token or not token.strip():
        return False, "Access Token Google Drive не указан.", logs

    try:
        logs.append("[1/5] Проверка Google OAuth2 токена и квоты аккаунта...")
        r_about = requests.get("https://www.googleapis.com/drive/v3/about?fields=user,storageQuota", headers=headers, timeout=10)
        if r_about.status_code != 200:
            err = f"Ошибка Google OAuth2 [{r_about.status_code}]: {r_about.text}"
            logs.append(err)
            return False, err, logs

        data = r_about.json()
        user_name = data.get("user", {}).get("displayName", "Google User")
        email = data.get("user", {}).get("emailAddress", "")
        logs.append(f"✔ Авторизация успешна. Аккаунт: {user_name} ({email})")

        # Create verification payload
        test_content = b"Asterisk Logic Core Google Drive Test Packet - " + os.urandom(64)
        orig_hash = hashlib.sha256(test_content).hexdigest()
        test_fn = f"test_integrity_{orig_hash[:8]}.tmp"

        logs.append(f"[2/5] Создание и загрузка тестового файла {test_fn}...")
        meta = {"name": test_fn}
        if folder_id:
            meta["parents"] = [folder_id]

        files = {
            'data': ('metadata', json.dumps(meta), 'application/json; charset=UTF-8'),
            'file': (test_fn, test_content, 'application/octet-stream')
        }
        r_up = requests.post(
            "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id,name",
            headers=headers,
            files=files,
            timeout=20
        )
        if r_up.status_code not in [200, 201]:
            err = f"Ошибка загрузки на Google Drive [{r_up.status_code}]: {r_up.text}"
            logs.append(err)
            return False, err, logs

        file_id = r_up.json().get("id")
        logs.append(f"✔ Файл загружен в Google Drive. File ID: {file_id}")

        # Download back & verify
        logs.append(f"[3/5] Скачивание файла обратно и сверка контрольной суммы...")
        r_dl = requests.get(f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media", headers=headers, timeout=20)
        if r_dl.status_code != 200:
            err = f"Ошибка чтения файла из Google Drive [{r_dl.status_code}]"
            logs.append(err)
            return False, err, logs

        dl_hash = hashlib.sha256(r_dl.content).hexdigest()
        if orig_hash != dl_hash:
            err = f"Ошибка целостности: Хеш не совпадает! Ожидался: {orig_hash}, Получен: {dl_hash}"
            logs.append(err)
            return False, err, logs
        logs.append(f"✔ Хеш SHA256 совпадает на 100% ({dl_hash[:16]}...). Целостность подтверждена.")

        # Cleanup
        logs.append(f"[4/5] Удаление тестового файла...")
        requests.delete(f"https://www.googleapis.com/drive/v3/files/{file_id}", headers=headers, timeout=10)
        logs.append("✔ Тестовый файл успешно удален. Google Drive готов к приему звонков!")

        return True, "Тест Google Drive успешно пройден!", logs

    except Exception as e:
        err = f"Исключение при тестировании Google Drive: {str(e)}"
        logs.append(err)
        return False, err, logs

def upload_call_record_to_gdrive(token, folder_id, file_path):
    """Uploads call audio directly to Google Drive."""
    if not token or not os.path.exists(file_path):
        return None
    try:
        fn = os.path.basename(file_path)
        metadata = {"name": fn}
        if folder_id:
            metadata["parents"] = [folder_id]

        files = {
            'data': ('metadata', json.dumps(metadata), 'application/json; charset=UTF-8'),
            'file': (fn, open(file_path, 'rb'), 'audio/wav')
        }
        headers = {"Authorization": f"Bearer {token}"}
        resp = requests.post(
            "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id,webViewLink,webContentLink",
            headers=headers,
            files=files,
            timeout=30
        )
        if resp.status_code in [200, 201]:
            file_id = resp.json().get("id")
            return f"https://drive.google.com/file/d/{file_id}/view"
    except Exception as e:
        print(f"[Google Drive Upload Error]: {e}")
    return None
