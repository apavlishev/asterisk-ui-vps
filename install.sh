#!/bin/bash
# Инициализация и автоматическая установка Asterisk PBX GUI & Integrations
set -e

echo "=== Начало установки Asterisk PBX GUI ==="

# Проверка на root
if [ "$EUID" -ne 0 ]; then
  echo "Пожалуйста, запустите скрипт с правами root (sudo ./install.sh)"
  exit 1
fi

echo "1. Обновление системы и установка зависимостей..."
apt-get update
apt-get install -y python3 python3-pip python3-venv ffmpeg curl wget git sudo lsof || true
apt-get install -y asterisk || true

# Настройка безопасных директорий Git
git config --global --add safe.directory /opt/asterisk-gui || true
git config --global --add safe.directory /opt/asterisk-gui-repo || true

echo "2. Установка Python зависимостей..."
pip3 install flask requests paramiko werkzeug --break-system-packages 2>/dev/null || pip3 install flask requests paramiko werkzeug || true

echo "3. Создание структуры директорий и прав..."
# 3.1 DNS Fallback для Telegram прокси
if ! grep -q "telegram.dentaldate.ae" /etc/hosts; then
    echo "185.243.76.230 telegram.dentaldate.ae" >> /etc/hosts
fi

mkdir -p /opt/asterisk-gui
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

# 5.1 Стандартный PJSIP шаблон (для любых SIP софтфонов MicroSIP/Zoiper и межатс-подключений)
echo "5.1 Настройка PJSIP (универсальный SIP-клиент/софтфон стандарт)..."
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

# 5.2 Настройка dongle.conf
echo "5.2 Настройка GSM Dongle модема..."
if [ -f /etc/asterisk/dongle.conf ]; then
    sed -i 's/exten=+79000000000/exten=s/g' /etc/asterisk/dongle.conf
    sed -i 's/rxgain=4/rxgain=0/g' /etc/asterisk/dongle.conf
    sed -i 's/txgain=4/txgain=0/g' /etc/asterisk/dongle.conf
fi

# Udev правила для горячего подключения модемов (Hot-plug)
echo "5.3 Настройка Udev правил для модемов..."
cat << 'UDEVRULES' > /etc/udev/rules.d/99-huawei-dongle.rules
KERNEL=="ttyUSB*", MODE="0666", GROUP="dialout"
SUBSYSTEM=="tty", ATTRS{idVendor}=="12d1", RUN+="/bin/bash /opt/asterisk-gui/dongle_hotplug.sh"
ACTION=="remove", SUBSYSTEM=="tty", KERNEL=="ttyUSB*", RUN+="/bin/bash /opt/asterisk-gui/dongle_hotplug.sh"
UDEVRULES

udevadm control --reload-rules 2>/dev/null || true
udevadm trigger 2>/dev/null || true

# 5.4 Автоматическая генерация эталонного диалплана с записью входящих вызовов
echo "5.4 Генерация эталонного диалплана..."
python3 -c "import sys; sys.path.insert(0, '/opt/asterisk-gui'); import app; app.generate_dialplan_from_tree(); app.generate_pjsip_conf()" 2>/dev/null || true

echo "6. Настройка Systemd сервисов..."
cat << 'SERVICE' > /etc/systemd/system/asterisk-gui.service
[Unit]
Description=Asterisk Web GUI
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
echo "Панель управления доступна по адресу: http://<ip-адрес-малины>:8080"
