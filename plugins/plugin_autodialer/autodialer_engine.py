import os
import json
import time
import uuid
import re
import socket
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor

# Try locating the data directory safely
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
os.makedirs(DATA_DIR, exist_ok=True)
CAMPAIGNS_FILE = os.path.join(DATA_DIR, 'campaigns.json')

AMI_HOST = "127.0.0.1"
AMI_PORT = 5038
AMI_USER = "admin"
AMI_SECRET = "supersecret"

SPOOL_DIR = "/var/spool/asterisk/outgoing"
SPOOL_TMP = "/var/spool/asterisk/tmp"
SOUNDS_DIR = "/var/lib/asterisk/sounds/custom"

# Q.850 Hangup Cause classification
CAUSE_MAP = {
    1: ('NOT_EXIST', 'Номер не существует / Не обслуживается (Cause 1)'),
    2: ('NOT_EXIST', 'Нет маршрута к транзитной сети (Cause 2)'),
    3: ('NOT_EXIST', 'Нет маршрута к абоненту (Cause 3)'),
    17: ('BUSY', 'Абонент занят (User Busy / Cause 17)'),
    18: ('NO_ANSWER', 'Абонент не отвечает (No user responding / Cause 18)'),
    19: ('NO_ANSWER', 'Таймаут ожидания ответа (No answer / Cause 19)'),
    20: ('UNAVAILABLE', 'Абонент не в сети / Вне зоны доступа (Subscriber absent / Cause 20)'),
    21: ('REJECTED', 'Вызов сброшен абонентом (Call rejected / Cause 21)'),
    27: ('UNAVAILABLE', 'Направление вне обслуживания (Destination out of order / Cause 27)'),
    28: ('NOT_EXIST', 'Неверный формат номера (Invalid number format / Cause 28)'),
    31: ('NO_ANSWER', 'Обычное разъединение без ответа (Cause 31)'),
    34: ('UNAVAILABLE', 'Сеть/каналы оператора перегружены (Circuit congestion / Cause 34)'),
    38: ('UNAVAILABLE', 'Сеть оператора временно недоступна (Network out of order / Cause 38)'),
}

# Bot / Anti-spam keywords signature list for Russian telecom market
BOT_KEYWORDS = [
    "помощник", "ассистент", "секретарь", "автоответчик",
    "олег", "алиса", "ева", "салют", "защитник",
    "что передать", "оставьте сообщение", "после сигнала",
    "вас не слышно", "по какому вопросу", "виртуальный",
    "робот", "не может сейчас ответить", "соединяю с автоответчиком",
    "записываю сообщение", "вы позвонили"
]

class AutoDialerEngine:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(AutoDialerEngine, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.campaigns = {}
        self.active_threads = {}
        self.stop_events = {}
        self.pause_events = {}
        self.load_campaigns()

    def load_campaigns(self):
        """Loads campaigns from disk."""
        if os.path.exists(CAMPAIGNS_FILE):
            try:
                with open(CAMPAIGNS_FILE, 'r', encoding='utf-8') as f:
                    self.campaigns = json.load(f)
            except Exception as e:
                print(f"[AutoDialer] Error loading campaigns: {e}")
                self.campaigns = {}
        else:
            self.campaigns = {}

    def save_campaigns(self):
        """Persists campaigns to disk atomically."""
        try:
            tmp_path = CAMPAIGNS_FILE + '.tmp'
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(self.campaigns, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, CAMPAIGNS_FILE)
        except Exception as e:
            print(f"[AutoDialer] Error saving campaigns: {e}")

    @staticmethod
    def normalize_phone(raw):
        """Standardizes phone numbers: removes non-digits, formats 8/7 prefixes."""
        digits = re.sub(r'\D', '', str(raw or ''))
        if not digits:
            return ""
        if len(digits) == 11 and digits.startswith('8'):
            digits = '7' + digits[1:]
        elif len(digits) == 10:
            digits = '7' + digits
        return digits

    def parse_numbers_input(self, text_or_lines):
        """Parses multi-line, comma, or semicolon-separated phone numbers."""
        raw_items = []
        if isinstance(text_or_lines, str):
            # Split by newlines, commas, semicolons, or tabs
            raw_items = re.split(r'[\r\n,;\t]+', text_or_lines)
        elif isinstance(text_or_lines, list):
            for item in text_or_lines:
                if isinstance(item, str):
                    raw_items.extend(re.split(r'[\r\n,;\t]+', item))
                elif isinstance(item, dict) and 'phone' in item:
                    raw_items.append(str(item['phone']))

        unique_numbers = []
        seen = set()
        for item in raw_items:
            norm = self.normalize_phone(item.strip())
            if norm and len(norm) >= 5 and norm not in seen:
                seen.add(norm)
                unique_numbers.append(norm)
        return unique_numbers

    def create_campaign(self, data):
        """Creates a new auto-dialer campaign."""
        camp_id = f"camp_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        name = data.get('name') or f"Кампания {time.strftime('%d.%m.%Y %H:%M')}"
        mode = data.get('mode') or 'broadcast'  # 'broadcast' or 'ping'
        ping_mode = data.get('ping_mode') or 'flash'  # 'flash' (drop on ring) or 'deep' (1-3s answer)
        
        # Phone numbers list
        raw_numbers = data.get('numbers', [])
        phones = self.parse_numbers_input(raw_numbers)

        trunk = data.get('trunk') or 'default'
        caller_id = str(data.get('caller_id') or '').strip()
        concurrency = max(1, min(30, int(data.get('concurrency', 3))))
        delay = max(0.1, float(data.get('delay_between_calls', 0.5)))
        ring_timeout = max(10, min(90, int(data.get('ring_timeout', 30))))
        retries = max(0, min(3, int(data.get('retries', 0))))

        # Broadcast specific
        audio_file = data.get('audio_file') or ''
        dtmf_action = data.get('dtmf_action') or 'none'  # 'operator', 'repeat', 'none'
        dtmf_operator = data.get('dtmf_operator') or '101'

        numbers_map = {}
        for p in phones:
            numbers_map[p] = {
                'phone': p,
                'status': 'pending',  # pending, calling, answered, active_ringing, antispam_bot, unavailable, not_exist, busy, rejected, no_answer, failed
                'operator_cause': 'Ожидает очереди',
                'cause_code': 0,
                'dialstatus': '',
                'attempts': 0,
                'duration': 0,
                'billsec': 0,
                'dtmf_pressed': '',
                'called_at': None
            }

        campaign = {
            'id': camp_id,
            'name': name,
            'mode': mode,
            'ping_mode': ping_mode,
            'status': 'ready',  # ready, running, paused, completed, stopped
            'trunk': trunk,
            'caller_id': caller_id,
            'concurrency': concurrency,
            'delay_between_calls': delay,
            'ring_timeout': ring_timeout,
            'retries': retries,
            'audio_file': audio_file,
            'dtmf_action': dtmf_action,
            'dtmf_operator': dtmf_operator,
            'created_at': int(time.time()),
            'updated_at': int(time.time()),
            'started_at': None,
            'finished_at': None,
            'numbers': numbers_map,
            'stats': {
                'total': len(phones),
                'processed': 0,
                'answered': 0,
                'active_ringing': 0,
                'antispam_bots': 0,
                'unavailable': 0,
                'not_exist': 0,
                'busy': 0,
                'rejected': 0,
                'no_answer': 0,
                'failed': 0
            }
        }

        self.campaigns[camp_id] = campaign
        self.save_campaigns()
        return campaign

    def get_campaign(self, camp_id):
        return self.campaigns.get(camp_id)

    def get_all_campaigns(self):
        # Return sorted by creation date descending
        return sorted(list(self.campaigns.values()), key=lambda c: c.get('created_at', 0), reverse=True)

    def delete_campaign(self, camp_id):
        self.stop_campaign(camp_id)
        if camp_id in self.campaigns:
            del self.campaigns[camp_id]
            self.save_campaigns()
            return True
        return False

    def start_campaign(self, camp_id):
        """Starts or resumes a campaign in a background worker."""
        camp = self.campaigns.get(camp_id)
        if not camp:
            return False, "Кампания не найдена"

        if camp['status'] == 'running':
            return True, "Кампания уже выполняется"

        # Check if already completed
        all_done = all(n['status'] != 'pending' for n in camp['numbers'].values())
        if all_done and camp['status'] == 'completed':
            return False, "Кампания уже завершена. Создайте новую или перезапустите звонки."

        stop_ev = threading.Event()
        pause_ev = threading.Event()
        self.stop_events[camp_id] = stop_ev
        self.pause_events[camp_id] = pause_ev

        camp['status'] = 'running'
        if not camp.get('started_at'):
            camp['started_at'] = int(time.time())
        camp['updated_at'] = int(time.time())
        self.save_campaigns()

        t = threading.Thread(target=self._campaign_worker, args=(camp_id, stop_ev, pause_ev), daemon=True)
        self.active_threads[camp_id] = t
        t.start()
        return True, "Кампания успешно запущена"

    def pause_campaign(self, camp_id):
        camp = self.campaigns.get(camp_id)
        if not camp or camp['status'] != 'running':
            return False, "Кампания не выполняется"
        camp['status'] = 'paused'
        camp['updated_at'] = int(time.time())
        self.save_campaigns()
        if camp_id in self.pause_events:
            self.pause_events[camp_id].set()
        return True, "Кампания приостановлена"

    def resume_campaign(self, camp_id):
        camp = self.campaigns.get(camp_id)
        if not camp or camp['status'] != 'paused':
            return False, "Кампания не на паузе"
        camp['status'] = 'running'
        camp['updated_at'] = int(time.time())
        self.save_campaigns()
        if camp_id in self.pause_events:
            self.pause_events[camp_id].clear()
        return True, "Кампания возобновлена"

    def stop_campaign(self, camp_id):
        camp = self.campaigns.get(camp_id)
        if not camp:
            return False, "Кампания не найдена"
        if camp_id in self.stop_events:
            self.stop_events[camp_id].set()
        camp['status'] = 'stopped'
        camp['finished_at'] = int(time.time())
        camp['updated_at'] = int(time.time())
        self.save_campaigns()
        return True, "Кампания остановлена"

    def _resolve_channel(self, phone, trunk):
        """Constructs Asterisk channel dial string."""
        if not trunk or trunk == 'default':
            return f"Local/{phone}@from-internal"
        elif trunk.startswith('dongle') or trunk == 'gsm':
            return f"Dongle/dongle0/{phone}"
        elif trunk == 'telegram_trunk':
            return f"PJSIP/tg_trunk_endpoint/{phone}"
        else:
            return f"PJSIP/{phone}@{trunk}"

    def _originate_call(self, camp, phone):
        """Initiates a single call via AMI or Call-File."""
        camp_id = camp['id']
        mode = camp['mode']
        ping_mode = camp.get('ping_mode', 'flash')
        trunk = camp.get('trunk', 'default')
        caller_id = camp.get('caller_id') or 'Autodialer'
        ring_timeout = camp.get('ring_timeout', 30)
        audio_file = camp.get('audio_file', '')
        dtmf_action = camp.get('dtmf_action', 'none')
        dtmf_operator = camp.get('dtmf_operator', '101')

        channel = self._resolve_channel(phone, trunk)
        context = f"autodialer-{mode}"
        dtmf_enable = "1" if dtmf_action != 'none' else "0"

        # Update number status to calling
        camp['numbers'][phone]['status'] = 'calling'
        camp['numbers'][phone]['attempts'] += 1
        camp['numbers'][phone]['called_at'] = int(time.time())
        camp['numbers'][phone]['operator_cause'] = 'Идет вызов...'

        variables = [
            f"AUTODIAL_CAMP_ID={camp_id}",
            f"AUTODIAL_PHONE={phone}",
            f"AUTODIAL_SOUND={audio_file}",
            f"AUTODIAL_PING_MODE={ping_mode}",
            f"AUTODIAL_OPERATOR={dtmf_operator}",
            f"AUTODIAL_DTMF_ENABLE={dtmf_enable}",
            f"AUTODIAL_DTMF_ACTION={dtmf_action}"
        ]

        # 1. Try AMI Originate
        ami_ok = False
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2.0)
            s.connect((AMI_HOST, AMI_PORT))
            banner = s.recv(1024).decode('utf-8', 'ignore')
            login_cmd = f"Action: Login\r\nUsername: {AMI_USER}\r\nSecret: {AMI_SECRET}\r\n\r\n"
            s.sendall(login_cmd.encode())
            resp = s.recv(1024).decode('utf-8', 'ignore')
            if "Response: Success" in resp:
                orig_cmd = [
                    "Action: Originate",
                    f"Channel: {channel}",
                    f"Context: {context}",
                    "Exten: s",
                    "Priority: 1",
                    f"Timeout: {ring_timeout * 1000}",
                    f"CallerID: {caller_id} <{caller_id}>",
                    "Async: true"
                ]
                for v in variables:
                    orig_cmd.append(f"Variable: {v}")
                orig_cmd.append("\r\n")
                s.sendall("\r\n".join(orig_cmd).encode())
                orig_resp = s.recv(2048).decode('utf-8', 'ignore')
                s.sendall(b"Action: Logoff\r\n\r\n")
                s.close()
                if "Response: Success" in orig_resp:
                    ami_ok = True
            else:
                s.close()
        except Exception:
            ami_ok = False

        # 2. Fallback to Asterisk Call File if AMI is not reachable
        if not ami_ok:
            try:
                os.makedirs(SPOOL_TMP, exist_ok=True)
                os.makedirs(SPOOL_DIR, exist_ok=True)
                call_file_name = f"autodial_{camp_id}_{phone}_{int(time.time())}.call"
                tmp_file = os.path.join(SPOOL_TMP, call_file_name)
                final_file = os.path.join(SPOOL_DIR, call_file_name)

                content = [
                    f"Channel: {channel}",
                    f"CallerID: {caller_id} <{caller_id}>",
                    f"WaitTime: {ring_timeout}",
                    f"Context: {context}",
                    "Extension: s",
                    "Priority: 1",
                    "MaxRetries: 0",
                    "Archive: no"
                ]
                for v in variables:
                    content.append(f"Set: {v}")

                with open(tmp_file, 'w', encoding='utf-8') as cf:
                    cf.write("\n".join(content) + "\n")

                # Atomic move
                os.replace(tmp_file, final_file)
                ami_ok = True
            except Exception as e:
                print(f"[AutoDialer] Call-file error: {e}")

        # 3. Fallback CLI command
        if not ami_ok:
            try:
                var_str = ",".join(variables)
                cmd = f'asterisk -rx "channel originate {channel} extension s@{context}"'
                subprocess.run(cmd, shell=True, timeout=5)
            except Exception as e:
                print(f"[AutoDialer] CLI originate error: {e}")

    def record_call_result(self, camp_id, phone, uniqueid, cause_code, dialstatus, disposition, billsec=0, duration=0, dtmf=""):
        """Called by hangup_handler when call finishes in Asterisk dialplan."""
        camp = self.campaigns.get(camp_id)
        if not camp or phone not in camp['numbers']:
            return

        item = camp['numbers'][phone]
        item['uniqueid'] = uniqueid
        item['billsec'] = int(billsec or 0)
        item['duration'] = int(duration or 0)
        item['dtmf_pressed'] = str(dtmf or '')
        item['dialstatus'] = str(dialstatus or '').upper()

        try:
            cause = int(cause_code or 0)
        except Exception:
            cause = 0
        item['cause_code'] = cause

        mode = camp.get('mode', 'broadcast')
        ping_mode = camp.get('ping_mode', 'flash')

        # Determine category and human explanation
        status = 'failed'
        desc = 'Неизвестный статус'

        if item['dialstatus'] == 'ANSWER' or disposition == 'ANSWERED':
            if mode == 'ping' and ping_mode == 'flash':
                status = 'active_ringing'
                desc = 'Активен (Ранний ответ оператора)'
            else:
                status = 'answered'
                if item['dtmf_pressed'] == '1':
                    desc = 'Ответил -> Переведен на оператора (DTMF 1)'
                elif item['dtmf_pressed'] == '2':
                    desc = 'Ответил -> Повтор аудиосообщения (DTMF 2)'
                elif item['billsec'] > 0:
                    desc = f'Успешно прослушано ({item["billsec"]} сек)'
                else:
                    desc = 'Вызов принят абонентом'
        elif item['dialstatus'] == 'CANCEL':
            if mode == 'ping' and ping_mode == 'flash':
                status = 'active_ringing'
                desc = 'Активен (Гудки подтверждены / Flash Drop)'
            else:
                status = 'no_answer'
                desc = 'Вызов отменен'
        elif item['dialstatus'] == 'BUSY' or cause == 17:
            status = 'busy'
            desc = 'Занято (Абонент разговаривает)'
        elif item['dialstatus'] == 'NOANSWER' or cause in (18, 19):
            status = 'no_answer'
            desc = 'Не отвечает (Истек таймаут вызова)'
        elif cause in CAUSE_MAP:
            mapped_status, mapped_desc = CAUSE_MAP[cause]
            status = mapped_status.lower()
            desc = mapped_desc
        elif item['dialstatus'] == 'CONGESTION':
            status = 'unavailable'
            desc = 'Перегрузка сети оператора / Недоступен'
        elif item['dialstatus'] == 'CHANUNAVAIL':
            status = 'not_exist'
            desc = 'Канал недоступен / Ошибка номера'
        else:
            status = 'failed'
            desc = f'Отбивка оператора: {item["dialstatus"] or "FAILED"} (Код: {cause})'

        # Don't overwrite if bot detector already marked it as antispam_bot
        if item.get('status') != 'antispam_bot':
            item['status'] = status
            item['operator_cause'] = desc

        self._recalculate_stats(camp)
        self.save_campaigns()

    def record_bot_detection(self, camp_id, phone, bot_type="Антиспам-помощник", confidence=95):
        """Marks a phone as identified spam assistant / voicemail."""
        camp = self.campaigns.get(camp_id)
        if not camp or phone not in camp['numbers']:
            return

        item = camp['numbers'][phone]
        item['status'] = 'antispam_bot'
        item['operator_cause'] = f"🤖 {bot_type} ({confidence}% уверенность)"
        self._recalculate_stats(camp)
        self.save_campaigns()

    def _recalculate_stats(self, camp):
        """Updates aggregated campaign counters."""
        stats = {
            'total': len(camp['numbers']),
            'processed': 0,
            'answered': 0,
            'active_ringing': 0,
            'antispam_bots': 0,
            'unavailable': 0,
            'not_exist': 0,
            'busy': 0,
            'rejected': 0,
            'no_answer': 0,
            'failed': 0
        }

        for n in camp['numbers'].values():
            st = n.get('status', 'pending')
            if st != 'pending' and st != 'calling':
                stats['processed'] += 1

            if st == 'answered':
                stats['answered'] += 1
            elif st == 'active_ringing':
                stats['active_ringing'] += 1
            elif st == 'antispam_bot':
                stats['antispam_bots'] += 1
            elif st == 'unavailable':
                stats['unavailable'] += 1
            elif st == 'not_exist':
                stats['not_exist'] += 1
            elif st == 'busy':
                stats['busy'] += 1
            elif st == 'rejected':
                stats['rejected'] += 1
            elif st == 'no_answer':
                stats['no_answer'] += 1
            elif st == 'failed':
                stats['failed'] += 1

        camp['stats'] = stats
        camp['updated_at'] = int(time.time())

    def _campaign_worker(self, camp_id, stop_event, pause_event):
        """Main dialing worker loop with concurrency limiter."""
        camp = self.campaigns.get(camp_id)
        if not camp:
            return

        concurrency = camp.get('concurrency', 3)
        delay = camp.get('delay_between_calls', 0.5)
        sem = threading.Semaphore(concurrency)

        def dial_task(phone):
            if stop_event.is_set():
                return
            try:
                self._originate_call(camp, phone)
                # Wait for call execution (timeout + grace period)
                to = camp.get('ring_timeout', 30) + 5
                deadline = time.time() + to
                while time.time() < deadline:
                    if stop_event.is_set():
                        break
                    # Check if status has transitioned from calling
                    st = camp['numbers'][phone].get('status')
                    if st != 'calling':
                        break
                    time.sleep(1)
                
                # If still calling after deadline, set timeout
                if camp['numbers'][phone].get('status') == 'calling':
                    self.record_call_result(camp_id, phone, '', 19, 'NOANSWER', 'NO ANSWER')
            except Exception as e:
                print(f"[AutoDialer] Worker error for {phone}: {e}")
                self.record_call_result(camp_id, phone, '', 0, 'FAILED', 'FAILED')
            finally:
                sem.release()

        # Iterate over pending numbers
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            for phone, info in list(camp['numbers'].items()):
                if stop_event.is_set():
                    break

                # Handle pause
                while pause_event.is_set():
                    if stop_event.is_set():
                        break
                    time.sleep(1)

                if info['status'] != 'pending':
                    continue

                sem.acquire()
                executor.submit(dial_task, phone)
                time.sleep(delay)

        # Worker finished
        if not stop_event.is_set():
            camp['status'] = 'completed'
            camp['finished_at'] = int(time.time())
        camp['updated_at'] = int(time.time())
        self._recalculate_stats(camp)
        self.save_campaigns()

    def export_numbers(self, camp_id, category='clean'):
        """
        Exports numbers according to cleansing filter:
        - 'clean': only valid & live (answered, active_ringing, rejected)
        - 'bots': spam assistants & voicemails
        - 'unavailable': out of reach, turned off, no answer
        - 'not_exist': unallocated, invalid number
        - 'all': complete CSV with all details
        """
        camp = self.campaigns.get(camp_id)
        if not camp:
            return ""

        lines = []
        if category == 'clean':
            # Live numbers (answered, flash ringing, or rejected where person pressed drop)
            valid_statuses = {'answered', 'active_ringing', 'rejected'}
            for p, item in camp['numbers'].items():
                if item.get('status') in valid_statuses:
                    lines.append(p)
            return "\n".join(lines)
        elif category == 'bots':
            for p, item in camp['numbers'].items():
                if item.get('status') == 'antispam_bot':
                    lines.append(f"{p},{item.get('operator_cause', 'Бот')}")
            return "\n".join(lines)
        elif category == 'unavailable':
            for p, item in camp['numbers'].items():
                if item.get('status') in ('unavailable', 'no_answer'):
                    lines.append(f"{p},{item.get('operator_cause', 'Недоступен')}")
            return "\n".join(lines)
        elif category == 'not_exist':
            for p, item in camp['numbers'].items():
                if item.get('status') == 'not_exist':
                    lines.append(f"{p},{item.get('operator_cause', 'Не существует')}")
            return "\n".join(lines)
        else:
            # Full CSV format
            lines.append("Номер,Статус,Категория,Причина отбивки,Код причины,DIALSTATUS,Длительность (сек),DTMF,Дата вызова")
            for p, item in camp['numbers'].items():
                called = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(item['called_at'])) if item.get('called_at') else '-'
                lines.append(f"\"{p}\",\"{item.get('status', '')}\",\"{self._get_category_label(item.get('status', ''))}\",\"{item.get('operator_cause', '')}\",\"{item.get('cause_code', 0)}\",\"{item.get('dialstatus', '')}\",\"{item.get('billsec', 0)}\",\"{item.get('dtmf_pressed', '')}\",\"{called}\"")
            return "\n".join(lines)

    @staticmethod
    def _get_category_label(status):
        labels = {
            'answered': 'Живой (Ответил)',
            'active_ringing': 'Живой (Гудки)',
            'rejected': 'Живой (Сбросил)',
            'antispam_bot': 'Антиспам-помощник / Бот',
            'unavailable': 'Недоступен / Вне сети',
            'not_exist': 'Не существует / Ошибка',
            'busy': 'Занято',
            'no_answer': 'Не ответил',
            'pending': 'В очереди',
            'calling': 'Идет звонок',
            'failed': 'Ошибка'
        }
        return labels.get(status, status)
