#!/bin/bash
# Инициализация и автоматическая установка Asterisk PBX GUI & Integrations
set -e

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
git config --global --add safe.directory /opt/asterisk-gui || true
git config --global --add safe.directory /opt/asterisk-gui-repo || true

echo "2. Установка Python зависимостей..."
pip3 install flask requests paramiko werkzeug google-api-python-client google-auth-httplib2 google-auth-oauthlib pydrive --break-system-packages 2>/dev/null || pip3 install flask requests paramiko werkzeug google-api-python-client google-auth-httplib2 google-auth-oauthlib pydrive || true

echo "3. Создание структуры директорий и прав..."
# 3.1 DNS Fallback для Telegram прокси
if ! grep -q "telegram.dentaldate.ae" /etc/hosts; then
    echo "185.243.76.230 telegram.dentaldate.ae" >> /etc/hosts
fi

mkdir -p /opt/asterisk-gui
mkdir -p /opt/plugins
mkdir -p /var/log/asterisk/cdr-csv
mkdir -p /var/spool/asterisk/monitor
mkdir -p /var/lib/asterisk/sounds/custom
mkdir -p /var/run/asterisk
chmod 777 /var/spool/asterisk/monitor
chown -R asterisk:asterisk /var/log/asterisk
chown -R asterisk:asterisk /var/spool/asterisk
chown -R asterisk:asterisk /var/lib/asterisk/sounds/custom
chown -R asterisk:asterisk /var/run/asterisk 2>/dev/null || true

# Настройка беспарольного sudo для asterisk и user
cat << 'SUDORULES' > /etc/sudoers.d/asterisk-gui
asterisk ALL=(ALL) NOPASSWD: ALL
user ALL=(ALL) NOPASSWD: ALL
SUDORULES
chmod 0440 /etc/sudoers.d/asterisk-gui

# Настройка Fail2ban для защиты Asterisk
echo "3.1 Настройка Fail2ban & Антифрод-фильтра..."
mkdir -p /etc/fail2ban/filter.d
cat << 'EOF_FILTER' > /etc/fail2ban/filter.d/asterisk-antifraud.conf
[Definition]
failregex = Request '(?:REGISTER|INVITE|SUBSCRIBE|OPTIONS)' from .* failed for '<HOST>:\d+'
            failed for '<HOST>:\d+' - (?:No matching endpoint found|Failed to authenticate|Username/auth name mismatch|Device does not match ACL)
            Call from '.*' \(<HOST>:\d+\) to extension '.*' rejected
            <HOST> failed to authenticate
            Host <HOST> failed to authenticate
            No registration for peer '.*' \(from <HOST>\)

ignoreregex =
EOF_FILTER

cat << 'EOF_JAIL' > /etc/fail2ban/jail.local
[DEFAULT]
ignoreip = 127.0.0.1/8 ::1 91.226.93.233
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

systemctl restart fail2ban 2>/dev/null || true

# Клонирование / копирование исходного кода
echo "4. Развертывание исходного кода..."
if [ -d "/opt/asterisk-gui/.git" ]; then
    cd /opt/asterisk-gui && git pull || true
else
    cp -r * /opt/asterisk-gui/ 2>/dev/null || true
    cp /opt/asterisk-gui/crm-yandex-uploader.py /opt/ 2>/dev/null || true
    cp /opt/asterisk-gui/tg-bot-daemon.py /opt/ 2>/dev/null || true
fi

chmod +x /opt/asterisk-gui/*.py /opt/asterisk-gui/*.sh 2>/dev/null || true
chmod +x /opt/*.py 2>/dev/null || true

# Базовый конфиг integrations_config.json
if [ ! -f /opt/integrations_config.json ]; then
    echo "5. Создание базового конфигурационного файла..."
    echo '{"amocrm": {"enabled": false}, "gdrive": {"enabled": false}, "telegram": {"enabled": false}, "routing": {"inbound_target": "ALL"}, "ivr_tree": {"enabled": false, "debug_enabled": true, "debug_exten": "888", "nodes": []}, "update_url": "https://raw.githubusercontent.com/apavlishev/asterisk-ui-vps/main/"}' > /opt/integrations_config.json
    chown asterisk:asterisk /opt/integrations_config.json
fi

# 5.1 Стандартный PJSIP шаблон
echo "5.1 Настройка PJSIP..."
if [ ! -f /etc/asterisk/pjsip.conf ] || ! grep -q "auth_type=userpass" /etc/asterisk/pjsip.conf; then
cat << 'PJSIPCONF' > /etc/asterisk/pjsip.conf
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
password=SecretPassword100!

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
password=101

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
aors=101

[102]
type=aor
max_contacts=5
remove_existing=yes

[102]
type=auth
auth_type=userpass
username=102
password=102

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
aors=102
PJSIPCONF
chown asterisk:asterisk /etc/asterisk/pjsip.conf
chmod 644 /etc/asterisk/pjsip.conf
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
python3 -c "import sys; sys.path.insert(0, '/opt/asterisk-gui'); import app; app.generate_dialplan_from_tree(); app.generate_pjsip_conf()" 2>/dev/null || true

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

systemctl daemon-reload
systemctl enable asterisk-gui.service
systemctl enable tg-bot.service 2>/dev/null || true
systemctl restart asterisk-gui.service
systemctl restart asterisk.service 2>/dev/null || true

echo "=== Установка успешно завершена! ==="
echo "Панель управления доступна по адресу: http://<IP_СЕРВЕРА>:8888"
