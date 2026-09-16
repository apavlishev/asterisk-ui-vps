# Deployment of the Logic Core License Server

## 1. Requirements
- Ubuntu 22.04+ / Debian 12+ VPS
- Python 3.10+
- PostgreSQL 14+ (or SQLite for a single small deployment)

## 2. Install
```bash
sudo useradd -r -s /usr/sbin/nologin license
sudo mkdir -p /opt/license-server && cd /opt/license-server
# copy this folder's contents here
sudo python3 -m venv .venv
sudo .venv/bin/pip install -r requirements.txt
sudo mkdir -p keys && sudo chmod 700 keys
```

## 3. Configure
Create `/opt/license-server/.env`:
```ini
LICENSE_DATABASE_URL=postgresql+psycopg2://license:STRONG_PASS@127.0.0.1:5432/license
LICENSE_ADMIN_TOKEN=CHANGE_ME_LONG_RANDOM
LICENSE_SIGNING_KEY=/opt/license-server/keys/license_ed25519.pem
LICENSE_PUBLIC_BASE_URL=https://license.example.com
LICENSE_LEASE_DAYS=30
LICENSE_MAX_ACTIVATIONS=1
LICENSE_RATE_LIMIT_PER_MIN=30
```
```bash
sudo chmod 600 /opt/license-server/.env
sudo chown -R license:license /opt/license-server
```

## 4. Database
```bash
sudo -u postgres psql -c "CREATE USER license WITH PASSWORD 'STRONG_PASS';"
sudo -u postgres psql -c "CREATE DATABASE license OWNER license;"
sudo -u license /opt/license-server/.venv/bin/python -c "from app.models import init_db; init_db()"
```

## 5. systemd
```bash
sudo cp deploy/license-server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now license-server
```

## 6. TLS (nginx + Let's Encrypt)
```nginx
server {
    listen 443 ssl http2;
    server_name license.example.com;
    ssl_certificate     /etc/letsencrypt/live/license.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/license.example.com/privkey.pem;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```
```bash
sudo certbot --nginx -d license.example.com
```

## 7. First license
```bash
cd /opt/license-server
sudo -u license .venv/bin/python manage.py issue --tier Pro --max-users 50 --customer "ACME" --days 365
sudo -u license .venv/bin/python manage.py public-key   # <-- put into the panel
```

## 8. Point the panel at the server
On each PBX panel set environment variables (e.g. in `asterisk-gui.service`):
```ini
Environment=LICENSE_SERVER_URL=https://license.example.com
Environment=LICENSE_KEY=LGC-XXXXXXXXXXXX
Environment=LICENSE_PUBLIC_KEY=<base64 public key from step 7>
```
Then activate once:
```bash
python3 -c "import license_mgr; print(license_mgr.activate())"
```
Add a daily revalidation (cron):
```
15 4 * * * cd /opt/asterisk-gui && python3 -c "import license_mgr; print(license_mgr.maybe_revalidate_daily())"
```

## Security checklist
- [ ] `LICENSE_ADMIN_TOKEN` is long and random; never committed.
- [ ] Signing private key (`keys/*.pem`) is `0600`, backed up securely, never shipped to clients.
- [ ] HTTPS only (no plain HTTP to `/api/v1/*`).
- [ ] DB not exposed to the internet.
- [ ] Regular DB backups.
