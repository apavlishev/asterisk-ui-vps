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
chmod +x /opt/asterisk-gui/app.py /opt/asterisk-gui/dongle_hotplug.py /opt/asterisk-gui/dongle_hotplug.sh /opt/crm-yandex-uploader.py /opt/tg-bot-daemon.py || true
# Обновляем Udev правила для горячей замены
cat << 'UDEVRULES' > /etc/udev/rules.d/99-huawei-dongle.rules
KERNEL=="ttyUSB*", MODE="0666", GROUP="dialout"
SUBSYSTEM=="tty", ATTRS{idVendor}=="12d1", RUN+="/bin/bash /opt/asterisk-gui/dongle_hotplug.sh"
ACTION=="remove", SUBSYSTEM=="tty", KERNEL=="ttyUSB*", RUN+="/bin/bash /opt/asterisk-gui/dongle_hotplug.sh"
UDEVRULES
udevadm control --reload-rules || true
udevadm trigger || true


# Запускаем скрипт миграций
echo "Running migrations..." >> /tmp/asterisk-update.log
/usr/bin/python3 /opt/asterisk-gui/migrate.py >> /tmp/asterisk-update.log 2>&1 || true


# Гарантия DNS прокси в /etc/hosts
if ! grep -q "telegram.dentaldate.ae" /etc/hosts; then
    echo "185.243.76.230 telegram.dentaldate.ae" >> /etc/hosts
fi

echo "Restarting services..." >> /tmp/asterisk-update.log
systemctl restart tg-bot.service || true
systemctl restart asterisk-gui.service || true

echo "Update completed successfully at $(date)." >> /tmp/asterisk-update.log
