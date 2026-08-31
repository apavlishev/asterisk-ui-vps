#!/bin/bash
set -e
echo "Starting tg2sip-webrtc installation..."
echo "WARNING: This requires 4GB+ RAM and takes ~30 mins. A swap file is recommended."

# Check if swap exists, if not, create a 4GB one to prevent OOM
if [ $(free -m | awk '/^Swap:/ {print $2}') -eq 0 ]; then
    echo "Creating 4GB swap file..."
    fallocate -l 4G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' | tee -a /etc/fstab
fi

cd /opt
if [ ! -d "tg2sip-webrtc" ]; then
    git clone --recurse-submodules https://github.com/vladonv/tg2sip-webrtc.git
fi
cd tg2sip-webrtc

echo "Installing clang and dependencies..."
apt-get update
apt-get install -y clang cmake build-essential git libssl-dev pkg-config ninja-build libjansson-dev libopus-dev

echo "Building dependencies (pjsip, tdlib)..."
./buildenv/build-clang-libcxx-deps.sh

echo "Configuring cmake preset..."
cmake --preset clang-libcxx

echo "Building tg2sip-webrtc (this will take a long time)..."
cmake --build build-clang

echo "Setting up configuration folder..."
mkdir -p /etc/tg2sip-webrtc
cp build-clang/tg2sip.conf.sample /etc/tg2sip-webrtc/tg2sip.conf

# Create systemd service
cat << 'SERVICE' > /etc/systemd/system/tg2sip-webrtc.service
[Unit]
Description=Telegram to SIP WebRTC Gateway
After=network.target

[Service]
Type=simple
ExecStart=/opt/tg2sip-webrtc/build-clang/tg2sip-webrtc /etc/tg2sip-webrtc/tg2sip.conf
Restart=on-failure
RestartSec=5
WorkingDirectory=/opt/tg2sip-webrtc

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable tg2sip-webrtc
echo "Installation complete! To configure, edit /etc/tg2sip-webrtc/tg2sip.conf and run tg2sip-gendb to login."
