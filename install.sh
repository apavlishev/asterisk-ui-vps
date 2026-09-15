#!/bin/bash
# Инициализация и автоматическая установка Asterisk PBX GUI & Integrations
set -e

GIT_REPO="${ASTERISK_GUI_REPO:-https://github.com/apavlishev/asterisk-ui-vps.git}"
INSTALL_DIR="/opt/asterisk-gui"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Начало установки Asterisk PBX GUI & Core Security ==="

# Проверка на root
if [ "$EUID" -ne 0 ]; then
  echo "Пожалуйста, запустите скрипт с правами root (sudo ./install.sh)"
  exit 1
fi

echo "1. Обновление системы и установка системных пакетов..."
apt-get update
apt-get install -y python3 python3-pip python3-venv ffmpeg sox curl wget git sudo lsof fail2ban iptables net-tools || true
DEBIAN_FRONTEND=noninteractive apt-get install -y asterisk asterisk-modules tzdata || true

# Настройка безопасных директорий Git
git config --global --add safe.directory "$INSTALL_DIR" || true
git config --global --add safe.directory /opt/asterisk-gui-repo || true

echo "2. Создание структуры директорий и прав..."
# 2.1 DNS Fallback для Telegram прокси
if ! grep -q "telegram.dentaldate.ae" /etc/hosts; then
    echo "185.243.76.230 telegram.dentaldate.ae" >> /etc/hosts
fi

mkdir -p "$INSTALL_DIR"
mkdir -p /var/log/asterisk/cdr-csv
mkdir -p /var/spool/asterisk/monitor
mkdir -p /var/lib/asterisk/sounds/custom
mkdir -p /var/run/asterisk
chown -R asterisk:asterisk /var/log/asterisk
chown -R asterisk:asterisk /var/spool/asterisk
chown -R asterisk:asterisk /var/lib/asterisk/sounds/custom
chown -R asterisk:asterisk /var/run/asterisk 2>/dev/null || true
chmod 2770 /var/spool/asterisk/monitor 2>/dev/null || chmod 777 /var/spool/asterisk/monitor

# Настройка беспарольного sudo для asterisk и user (нужно для netplan/iptables/fail2ban)
cat << 'SUDORULES' > /etc/sudoers.d/asterisk-gui
asterisk ALL=(ALL) NOPASSWD: ALL
user ALL=(ALL) NOPASSWD: ALL
SUDORULES
chmod 0440 /etc/sudoers.d/asterisk-gui

# Настройка Fail2ban для защиты Asterisk
echo "2.1 Настройка Fail2ban & Антифрод-фильтра..."
apt-get install -y fail2ban ipset iptables || true
mkdir -p /etc/fail2ban/filter.d
cat << 'EOF_FILTER' > /etc/fail2ban/filter.d/asterisk-antifraud.conf
[Definition]
failregex = Request '(?:REGISTER|INVITE|SUBSCRIBE|OPTIONS)' from .* failed for '<HOST>:\d+'
            failed for '<HOST>:\d+' - (?:No matching endpoint found|Failed to authenticate|Username/auth name mismatch|Device does not match ACL)
            Call from '.*' \(<HOST>:\d+\) to extension '.*' rejected
            <HOST> failed to authenticate
            Host <HOST> failed to authenticate
            No registration for peer '.*' \(from <HOST>\)
            failed to authenticate as '.*' \(from <HOST>\)

ignoreregex =
EOF_FILTER

cat << 'EOF_JAIL' > /etc/fail2ban/jail.local
[DEFAULT]
ignoreip = 127.0.0.1/8 ::1
bantime  = 86400
findtime = 300
maxretry = 3
backend  = systemd

[asterisk-antifraud]
enabled  = true
backend  = systemd
journalmatch = _SYSTEMD_UNIT=asterisk.service
port     = 5060,5061,5160,10000:20000
protocol = all
filter   = asterisk-antifraud
maxretry = 3
findtime = 300
bantime  = 86400
action   = iptables-allports[name=ASTERISK-ANTIFRAUD, protocol=all]
EOF_JAIL

# Убеждаемся, что fail2ban включён и запущен (иначе защита молча не работает)
systemctl enable fail2ban 2>/dev/null || true
systemctl restart fail2ban 2>/dev/null || true
if systemctl is-active --quiet fail2ban; then
    echo "    Fail2ban активен. Статус джейла:"
    fail2ban-client status asterisk-antifraud 2>/dev/null || echo "    [!] Джейл пока не виден, проверьте: journalctl -u fail2ban"
else
    echo "    [!] Fail2ban не запустился. Проверьте: systemctl status fail2ban"
fi

# Настройка фаервола: открываем только необходимые для работы порты.
# Панель сама определит уже установленный фаервол (ufw/firewalld/nft/iptables),
# а при их отсутствии поставит ufw.
echo "2.2 Настройка фаервола (автоопределение: ufw/firewalld/nftables/iptables)..."
apt-get install -y ufw nftables iptables || true

# Развертывание исходного кода (self-bootstrap: работает и из клона, и из curl | bash)
echo "3. Развертывание исходного кода..."
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "   Обновление существующей установки через git..."
    git -C "$INSTALL_DIR" fetch origin || true
    git -C "$INSTALL_DIR" reset --hard origin/main || true
elif [ -d "$SCRIPT_DIR/.git" ]; then
    echo "   Клонирование из локальной копии $SCRIPT_DIR..."
    rm -rf "$INSTALL_DIR"
    git clone "$SCRIPT_DIR" "$INSTALL_DIR" || cp -r "$SCRIPT_DIR" "$INSTALL_DIR"
else
    echo "   Клонирование из $GIT_REPO..."
    rm -rf "$INSTALL_DIR"
    git clone "$GIT_REPO" "$INSTALL_DIR"
fi

chmod +x "$INSTALL_DIR"/*.py "$INSTALL_DIR"/*.sh 2>/dev/null || true

# 3.1 Каталог плагинов, который ожидает crm-yandex-uploader.py при запуске из /opt
if [ -d /opt/plugins ] && [ ! -L /opt/plugins ]; then
    rm -rf /opt/plugins
fi
ln -sfn "$INSTALL_DIR/plugins" /opt/plugins

# Копируем демоны в /opt (обратная совместимость: диалплан вызывает /opt/crm-yandex-uploader.py)
cp "$INSTALL_DIR/crm-yandex-uploader.py" /opt/ 2>/dev/null || true
cp "$INSTALL_DIR/tg-bot-daemon.py" /opt/ 2>/dev/null || true
cp "$INSTALL_DIR/live_transcribe_daemon.py" /opt/ 2>/dev/null || true
chmod +x /opt/*.py 2>/dev/null || true

# Установка Python зависимостей (после деплоя, т.к. requirements.txt в репозитории)
echo "4. Установка Python зависимостей..."
if [ -f "$INSTALL_DIR/requirements.txt" ]; then
    pip3 install -r "$INSTALL_DIR/requirements.txt" --break-system-packages 2>/dev/null \
        || pip3 install -r "$INSTALL_DIR/requirements.txt" 2>/dev/null \
        || echo "   [!] Не все Python-зависимости установились автоматически. Проверьте pip вручную."
else
    pip3 install flask requests paramiko werkzeug telethon google-genai websockets \
        google-api-python-client google-auth-httplib2 google-auth-oauthlib pydrive \
        --break-system-packages 2>/dev/null \
        || pip3 install flask requests paramiko werkzeug telethon google-genai websockets \
            google-api-python-client google-auth-httplib2 google-auth-oauthlib pydrive 2>/dev/null \
        || true
fi

# Базовый конфиг integrations_config.json
if [ ! -f /opt/integrations_config.json ]; then
    echo "5. Создание базового конфигурационного файла..."
    echo '{"amocrm": {"enabled": false}, "gdrive": {"enabled": false}, "telegram": {"enabled": false}, "routing": {"inbound_target": "ALL"}, "ivr_tree": {"enabled": false, "debug_enabled": true, "debug_exten": "888", "nodes": []}, "update_url": "https://raw.githubusercontent.com/apavlishev/asterisk-ui-vps/main/"}' > /opt/integrations_config.json
    chown asterisk:asterisk /opt/integrations_config.json
fi

# 5.1 Стандартный PJSIP шаблон (со случайными паролями при первой установке)
echo "5.1 Настройка PJSIP..."
if [ ! -f /etc/asterisk/pjsip.conf ] || ! grep -q "auth_type=userpass" /etc/asterisk/pjsip.conf; then
GEN_PW_100="$(python3 -c "import secrets;print(secrets.token_urlsafe(12))")"
GEN_PW_101="$(python3 -c "import secrets;print(secrets.token_urlsafe(12))")"
GEN_PW_102="$(python3 -c "import secrets;print(secrets.token_urlsafe(12))")"
cat << PJSIPCONF > /etc/asterisk/pjsip.conf
[transport-udp]
type=transport
protocol=udp
bind=0.0.0.0:5060
local_net=192.168.0.0/16

[100]
type=aor
max_contacts=5
remove_existing=yes

[100]
type=auth
auth_type=userpass
username=100
password=${GEN_PW_100}

[100]
type=endpoint
context=from-internal
disallow=all
allow=alaw
allow=ulaw
allow=g722
allow=slin16
direct_media=no
rtp_symmetric=yes
force_rport=yes
rewrite_contact=yes
auth=100
outbound_auth=100
aors=100

[101]
type=aor
max_contacts=5
remove_existing=yes

[101]
type=auth
auth_type=userpass
username=101
password=${GEN_PW_101}

[101]
type=endpoint
context=from-internal
disallow=all
allow=alaw
allow=ulaw
allow=g722
allow=slin16
direct_media=no
rtp_symmetric=yes
force_rport=yes
rewrite_contact=yes
auth=101
outbound_auth=101
aors=101

[102]
type=aor
max_contacts=5
remove_existing=yes

[102]
type=auth
auth_type=userpass
username=102
password=${GEN_PW_102}

[102]
type=endpoint
context=from-internal
disallow=all
allow=alaw
allow=ulaw
allow=g722
allow=slin16
direct_media=no
rtp_symmetric=yes
force_rport=yes
rewrite_contact=yes
auth=102
outbound_auth=102
aors=102
PJSIPCONF
chown asterisk:asterisk /etc/asterisk/pjsip.conf
chmod 644 /etc/asterisk/pjsip.conf
echo "    Сгенерированы SIP-аккаунты (сохраните пароли!):"
echo "      100 -> ${GEN_PW_100}"
echo "      101 -> ${GEN_PW_101}"
echo "      102 -> ${GEN_PW_102}"
fi

# Udev правила для горячего подключения модемов (Hot-plug)
echo "5.2 Настройка Udev правил для модемов..."
cat << 'UDEVRULES' > /etc/udev/rules.d/99-huawei-dongle.rules
KERNEL=="ttyUSB*", MODE="0666", GROUP="dialout"
SUBSYSTEM=="tty", ATTRS{idVendor}=="12d1", RUN+="/bin/bash /opt/asterisk-gui/dongle_hotplug.sh"
ACTION=="remove", SUBSYSTEM=="tty", KERNEL=="ttyUSB*", RUN+="/bin/bash /opt/asterisk-gui/dongle_hotplug.sh"
UDEVRULES

udevadm control --reload-rules 2>/dev/null || true
udevadm trigger 2>/dev/null || true

# 5.3 Автоматическая генерация эталонного диалплана
echo "5.3 Генерация эталонного диалплана..."
cd "$INSTALL_DIR" && python3 -c "import sys; sys.path.insert(0, '$INSTALL_DIR'); import app; app.generate_dialplan_from_tree(); app.generate_pjsip_conf()" 2>/dev/null || echo "   [!] Автогенерация диалплана будет выполнена при первом сохранении настроек в UI."

echo "6. Настройка Systemd сервисов..."
cat << 'SERVICE' > /etc/systemd/system/asterisk-gui.service
[Unit]
Description=Asterisk PBX Web GUI (Logic Core)
After=network.target asterisk.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/asterisk-gui
ExecStart=/usr/bin/python3 /opt/asterisk-gui/app.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
SERVICE

cat << 'SERVICE2' > /etc/systemd/system/tg-bot.service
[Unit]
Description=Telegram Bot Daemon & Notifier for Asterisk PBX
After=network.target asterisk.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt
ExecStart=/usr/bin/python3 /opt/tg-bot-daemon.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SERVICE2

cat << 'SERVICE3' > /etc/systemd/system/live-transcribe.service
[Unit]
Description=Live Speech Transcription Daemon (Gemini/Whisper)
After=network.target asterisk.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/asterisk-gui
ExecStart=/usr/bin/python3 /opt/live_transcribe_daemon.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SERVICE3

systemctl daemon-reload
systemctl enable asterisk-gui.service
systemctl enable tg-bot.service 2>/dev/null || true
systemctl enable live-transcribe.service 2>/dev/null || true
systemctl restart asterisk-gui.service
systemctl restart tg-bot.service 2>/dev/null || true
systemctl restart live-transcribe.service 2>/dev/null || true
systemctl restart asterisk.service 2>/dev/null || true

# 6.1 Открываем стандартные порты АТС через встроенный менеджер фаервола
echo "6.1 Открытие стандартных портов (SSH, 8888, SIP, RTP)..."
python3 - <<'FWEOF' || echo "   [!] Не удалось применить правила фаервола (сделайте это в панели: Безопасность → Фаервол)."
import sys
sys.path.insert(0, "/opt/asterisk-gui")
import firewall_mgr
if not firewall_mgr.is_available():
    print("   [!] Фаервол не найден и ufw не установился — пропуск настройки фаервола.")
    sys.exit(1)
state = firewall_mgr.load_state()
state = firewall_mgr.ensure_defaults(state)
state["enabled"] = True
firewall_mgr.save_state(state)
# safe=False: это первичная установка, watchdog-откат не нужен (SSH уже открыт)
ok, msg = firewall_mgr.apply_rules(state, safe=False)
print("   ", msg)
sys.exit(0 if ok else 1)
FWEOF

# 7. Проверка работоспособности
echo "7. Проверка работоспособности..."
sleep 3
GUI_OK=1
if ! systemctl is-active --quiet asterisk-gui.service; then
    GUI_OK=0
    echo "   [!] Сервис asterisk-gui.service не запустился. Логи:"
    journalctl -u asterisk-gui.service -n 20 --no-pager 2>/dev/null || true
fi
if [ "$GUI_OK" = "1" ]; then
    for i in 1 2 3 4 5; do
        if curl -fsS "http://127.0.0.1:8888/login" >/dev/null 2>&1; then
            break
        fi
        sleep 2
    done
    if ! curl -fsS "http://127.0.0.1:8888/login" >/dev/null 2>&1; then
        GUI_OK=0
        echo "   [!] Веб-панель не отвечает на порту 8888. Логи:"
        journalctl -u asterisk-gui.service -n 20 --no-pager 2>/dev/null || true
    fi
fi

echo "=== Установка завершена! ==="
echo "Панель управления доступна по адресу: http://<IP_СЕРВЕРА>:8888"

if [ "$GUI_OK" != "1" ]; then
    echo "=== [!] Есть проблемы с запуском. См. сообщения выше. ==="
    exit 1
fi
