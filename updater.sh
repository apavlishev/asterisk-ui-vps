#!/bin/bash
set -e
echo "Starting update process at $(date)" > /tmp/asterisk-update.log

# Переходим в директорию проекта
cd /opt/asterisk-gui

export GIT_SSH_COMMAND="ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"

# Обновление исходников
if [ -d ".git" ]; then
    echo "Pulling latest changes from git..." >> /tmp/asterisk-update.log
    git fetch origin >> /tmp/asterisk-update.log 2>&1
    git reset --hard origin/main >> /tmp/asterisk-update.log 2>&1
else
    echo "Git not found in /opt/asterisk-gui, skipping git fetch." >> /tmp/asterisk-update.log
fi

# Копируем демоны
cp /opt/asterisk-gui/crm-yandex-uploader.py /opt/ || true
cp /opt/asterisk-gui/tg-bot-daemon.py /opt/ || true
cp /opt/asterisk-gui/live_transcribe_daemon.py /opt/ || true
chmod +x /opt/asterisk-gui/app.py /opt/asterisk-gui/dongle_hotplug.py /opt/asterisk-gui/dongle_hotplug.sh /opt/crm-yandex-uploader.py /opt/tg-bot-daemon.py /opt/live_transcribe_daemon.py || true

# Каталог плагинов, который ожидает crm-yandex-uploader.py при запуске из /opt
if [ -d /opt/plugins ] && [ ! -L /opt/plugins ]; then
    rm -rf /opt/plugins
fi
ln -sfn /opt/asterisk-gui/plugins /opt/plugins

# Обновляем Udev правила для горячей замены
cat << 'UDEVRULES' > /etc/udev/rules.d/99-huawei-dongle.rules
KERNEL=="ttyUSB*", MODE="0666", GROUP="dialout"
SUBSYSTEM=="tty", ATTRS{idVendor}=="12d1", RUN+="/bin/bash /opt/asterisk-gui/dongle_hotplug.sh"
ACTION=="remove", SUBSYSTEM=="tty", KERNEL=="ttyUSB*", RUN+="/bin/bash /opt/asterisk-gui/dongle_hotplug.sh"
UDEVRULES
udevadm control --reload-rules || true
udevadm trigger || true

# Установка/обновление Python зависимостей (важно для новых модулей)
echo "Installing Python requirements..." >> /tmp/asterisk-update.log
if [ -f /opt/asterisk-gui/requirements.txt ]; then
    pip3 install -r /opt/asterisk-gui/requirements.txt --break-system-packages >> /tmp/asterisk-update.log 2>&1 \
        || pip3 install -r /opt/asterisk-gui/requirements.txt >> /tmp/asterisk-update.log 2>&1 \
        || echo "[!] requirements install failed" >> /tmp/asterisk-update.log
fi

# Гарантируем наличие unit-файла live-transcribe
if [ ! -f /etc/systemd/system/live-transcribe.service ]; then
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
    systemctl enable live-transcribe.service 2>/dev/null || true
fi

# Гарантируем корректную конфигурацию fail2ban (systemd backend + journalmatch).
# Обновляем только если установлен fail2ban; не трогаем кастомный whitelist,
# если он уже задан в [DEFAULT] (он сохраняется через настройки в UI).
if command -v fail2ban-client >/dev/null 2>&1; then
    apt-get install -y ipset iptables >> /tmp/asterisk-update.log 2>&1 || true

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

    # Если jail.local отсутствует или не содержит наш джейл — создаём базовый.
    if [ ! -f /etc/fail2ban/jail.local ] || ! grep -q "asterisk-antifraud" /etc/fail2ban/jail.local; then
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
    fi

    systemctl enable fail2ban >> /tmp/asterisk-update.log 2>&1 || true
    systemctl restart fail2ban >> /tmp/asterisk-update.log 2>&1 || true
    echo "fail2ban reconfigured." >> /tmp/asterisk-update.log
fi

# Фаервол: гарантируем наличие nftables/iptables и базовых правил (без сброса
# пользовательских настроек — добавляем только отсутствующие базовые порты).
apt-get install -y nftables iptables >> /tmp/asterisk-update.log 2>&1 || true
/usr/bin/python3 - <<'FWEOF' >> /tmp/asterisk-update.log 2>&1 || true
import sys
sys.path.insert(0, "/opt/asterisk-gui")
import firewall_mgr
state = firewall_mgr.load_state()
had_defaults = state.get("installed_defaults", False)
state = firewall_mgr.ensure_defaults(state)
if state.get("enabled"):
    firewall_mgr.apply_rules(state)
print("[firewall] defaults ensured:", had_defaults, "->", state.get("installed_defaults"))
FWEOF

# Запускаем скрипт миграций
echo "Running migrations..." >> /tmp/asterisk-update.log
/usr/bin/python3 /opt/asterisk-gui/migrate.py >> /tmp/asterisk-update.log 2>&1 || true

# Гарантия DNS прокси в /etc/hosts
if ! grep -q "telegram.dentaldate.ae" /etc/hosts; then
    echo "185.243.76.230 telegram.dentaldate.ae" >> /etc/hosts
fi

echo "Restarting services..." >> /tmp/asterisk-update.log
systemctl restart tg-bot.service || true
systemctl restart live-transcribe.service || true
systemctl restart asterisk-gui.service || true

echo "Update completed successfully at $(date)." >> /tmp/asterisk-update.log
