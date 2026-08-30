import os
import ftplib
import io
import hashlib

def test_ftp_connection(host, port, user, password, remote_dir="/records", public_base_url=""):
    """
    Performs full FTP end-to-end connection & hash integrity test:
    1. Connects and authenticates to FTP server.
    2. Ensures remote directory exists.
    3. Uploads test file with known SHA256.
    4. Downloads test file and compares hashes.
    5. Deletes test file from FTP.
    """
    logs = []
    if not host or not user:
        return False, "FTP хост или логин не указаны.", logs

    try:
        port = int(port) if str(port).isdigit() else 21
        logs.append(f"[1/5] Подключение к FTP серверу {host}:{port} под пользователем {user}...")
        
        ftp = ftplib.FTP()
        ftp.connect(host, port, timeout=10)
        ftp.login(user, password)
        logs.append(f"✔ Подключение и авторизация на FTP сервере успешны.")

        # Ensure directory
        logs.append(f"[2/5] Проверка целевого каталога {remote_dir}...")
        try:
            ftp.cwd(remote_dir)
        except Exception:
            # Try to create
            ftp.mkd(remote_dir)
            ftp.cwd(remote_dir)
        logs.append(f"✔ Каталог {remote_dir} активен.")

        # Test file
        test_content = b"Asterisk Logic Core FTP Test Packet - " + os.urandom(64)
        orig_hash = hashlib.sha256(test_content).hexdigest()
        test_fn = f"test_integrity_{orig_hash[:8]}.tmp"

        logs.append(f"[3/5] Загрузка тестового пакета {test_fn} (SHA256: {orig_hash[:16]}...)...")
        bio_up = io.BytesIO(test_content)
        ftp.storbinary(f"STOR {test_fn}", bio_up)
        logs.append("✔ Тестовый файл загружен на FTP.")

        # Download back & verify hash
        logs.append(f"[4/5] Скачивание файла обратно с FTP и проверка хеша...")
        bio_dl = io.BytesIO()
        ftp.retrbinary(f"RETR {test_fn}", bio_dl.write)
        dl_content = bio_dl.getvalue()
        dl_hash = hashlib.sha256(dl_content).hexdigest()

        if orig_hash != dl_hash:
            err = f"Ошибка целостности: Хеш не совпадает! Исходный: {orig_hash}, Полученный: {dl_hash}"
            logs.append(err)
            ftp.quit()
            return False, err, logs
        logs.append(f"✔ Хеш SHA256 совпадает на 100% ({dl_hash[:16]}...). Целостность подтверждена.")

        # Cleanup
        logs.append(f"[5/5] Удаление тестового файла...")
        ftp.delete(test_fn)
        ftp.quit()
        logs.append("✔ Тестовый файл удален. FTP хранилище готово к приему аудиозаписей!")

        return True, "Тест FTP соединения и целостности успешно пройден!", logs

    except Exception as e:
        err = f"Исключение при тестировании FTP: {str(e)}"
        logs.append(err)
        return False, err, logs

def upload_call_record_to_ftp(host, port, user, password, remote_dir, public_base_url, file_path):
    """Uploads recording to FTP and returns public HTTP/HTTPS URL."""
    if not host or not os.path.exists(file_path):
        return None
    try:
        port = int(port) if str(port).isdigit() else 21
        fn = os.path.basename(file_path)
        ftp = ftplib.FTP()
        ftp.connect(host, port, timeout=15)
        ftp.login(user, password)
        
        try: ftp.cwd(remote_dir)
        except: 
            ftp.mkd(remote_dir)
            ftp.cwd(remote_dir)
            
        with open(file_path, "rb") as f:
            ftp.storbinary(f"STOR {fn}", f)
        ftp.quit()

        if public_base_url:
            clean_base = public_base_url.rstrip("/")
            return f"{clean_base}/{fn}"
        return f"ftp://{user}@{host}:{port}/{remote_dir.strip('/')}/{fn}"
    except Exception as e:
        print(f"[FTP Upload Error]: {e}")
    return None
