import os
import json
import requests
import hashlib

def test_yandex_disk_connection(token, remote_dir="app:/records"):
    """
    Performs full end-to-end integrity test:
    1. Uploads temporary verification file with known SHA256 hash.
    2. Downloads it back from Yandex.Disk API.
    3. Verifies downloaded SHA256 matches original payload.
    4. Cleans up test file from remote storage.
    """
    logs = []
    headers = {"Authorization": f"OAuth {token}"}
    
    if not token or not token.strip():
        return False, "Токен Яндекс.Диска не указан.", logs

    try:
        logs.append("[1/5] Проверка токена и доступности дискового пространства...")
        r_info = requests.get("https://cloud-api.yandex.net/v1/disk/", headers=headers, timeout=10)
        if r_info.status_code != 200:
            err = f"Ошибка авторизации Яндекс.Диска [{r_info.status_code}]: {r_info.text}"
            logs.append(err)
            return False, err, logs

        user_info = r_info.json()
        total_gb = round(user_info.get("total_space", 0) / (1024**3), 2)
        used_gb = round(user_info.get("used_space", 0) / (1024**3), 2)
        logs.append(f"✔ Авторизация успешна. Диск: занято {used_gb} GB из {total_gb} GB.")

        # Ensure directory
        logs.append(f"[2/5] Проверка целевой папки {remote_dir}...")
        requests.put(f"https://cloud-api.yandex.net/v1/disk/resources?path={remote_dir}", headers=headers, timeout=10)

        # Generate test payload
        test_content = b"Asterisk Logic Core Yandex.Disk Test Integrity Packet - " + os.urandom(64)
        orig_hash = hashlib.sha256(test_content).hexdigest()
        test_fn = f"test_integrity_{orig_hash[:8]}.tmp"
        test_remote_path = f"{remote_dir}/{test_fn}"

        # Upload
        logs.append(f"[3/5] Загрузка тестового файла {test_fn} (SHA256: {orig_hash[:16]}...)...")
        r_get_upload = requests.get(
            f"https://cloud-api.yandex.net/v1/disk/resources/upload?path={test_remote_path}&overwrite=true",
            headers=headers,
            timeout=10
        )
        if r_get_upload.status_code != 200:
            err = f"Не удалось получить URL загрузки [{r_get_upload.status_code}]: {r_get_upload.text}"
            logs.append(err)
            return False, err, logs

        upload_url = r_get_upload.json().get("href")
        r_upload = requests.put(upload_url, data=test_content, timeout=15)
        if r_upload.status_code not in [200, 201]:
            err = f"Ошибка отправки данных файла [{r_upload.status_code}]"
            logs.append(err)
            return False, err, logs
        logs.append("✔ Тестовый файл успешно загружен на Яндекс.Диск.")

        # Download & verify
        logs.append(f"[4/5] Скачивание файла обратно и проверка целостности хеша...")
        r_get_dl = requests.get(
            f"https://cloud-api.yandex.net/v1/disk/resources/download?path={test_remote_path}",
            headers=headers,
            timeout=10
        )
        if r_get_dl.status_code != 200:
            err = f"Не удалось получить URL для чтения [{r_get_dl.status_code}]"
            logs.append(err)
            return False, err, logs

        dl_url = r_get_dl.json().get("href")
        r_dl = requests.get(dl_url, timeout=15)
        dl_content = r_dl.content
        dl_hash = hashlib.sha256(dl_content).hexdigest()

        if orig_hash != dl_hash:
            err = f"Ошибка целостности: Хеш не совпадает! Исходный: {orig_hash}, Полученный: {dl_hash}"
            logs.append(err)
            return False, err, logs

        logs.append(f"✔ Хеш совпадает на 100% ({dl_hash[:16]}...). Целостность подтверждена.")

        # Cleanup
        logs.append(f"[5/5] Очистка тестового пакета на диске...")
        requests.delete(f"https://cloud-api.yandex.net/v1/disk/resources?path={test_remote_path}&permanently=true", headers=headers, timeout=10)
        logs.append("✔ Тестовый файл удален. Хранилище полностью готово к сохранению аудиозаписей звонков!")

        return True, "Тест соединения и целостности успешно пройден!", logs

    except Exception as e:
        err = f"Исключение при тестировании Яндекс.Диска: {str(e)}"
        logs.append(err)
        return False, err, logs

def upload_call_record_to_yandex(token, file_path, remote_dir="app:/records"):
    """Uploads a WAV/MP3 recording to Yandex.Disk."""
    if not token or not os.path.exists(file_path):
        return None
    try:
        fn = os.path.basename(file_path)
        headers = {"Authorization": f"OAuth {token}"}
        # Ensure dir
        requests.put(f"https://cloud-api.yandex.net/v1/disk/resources?path={remote_dir}", headers=headers, timeout=5)
        
        target_path = f"{remote_dir}/{fn}"
        r_up = requests.get(f"https://cloud-api.yandex.net/v1/disk/resources/upload?path={target_path}&overwrite=true", headers=headers, timeout=10)
        if r_up.status_code == 200:
            upload_url = r_up.json().get("href")
            with open(file_path, "rb") as f:
                r_put = requests.put(upload_url, data=f, timeout=45)
            if r_put.status_code in [200, 201]:
                return f"https://disk.yandex.ru/client/disk/app/records/{fn}"
    except Exception as e:
        print(f"[Yandex Disk Upload Error]: {e}")
    return None
