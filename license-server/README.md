# Logic Core License Server

Сервер лицензирования для панели Asterisk UI: выдаёт **подписанные Ed25519**
лицензии, привязанные к железу сервера (fingerprint), и проверяет их.

## Возможности
- Лицензионные ключи с тарифами, лимитом абонентов и включёнными плагинами.
- Активация по fingerprint сервера (1 ключ = N активаций).
- **Offline-подпись**: клиент проверяет лицензию без сети; при недоступности
  сервера действует grace-период до `expires_at` (lease по умолчанию 30 дней).
- Отзыв активаций, аудит, rate-limiting, Pydantic-валидация.
- PostgreSQL в продакшене, SQLite для разработки.

## Структура
```
license-server/
├── app/
│   ├── main.py        # FastAPI: клиентские и админские endpoints
│   ├── models.py      # SQLAlchemy: licenses, activations, audit_log
│   ├── licensing.py   # бизнес-логика активации/валидации
│   ├── signing.py     # Ed25519 подпись/проверка
│   └── config.py      # настройки из окружения
├── manage.py          # CLI администратора
├── requirements.txt
└── deploy/            # systemd, nginx, инструкция
```

## Быстрый старт (dev)
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export LICENSE_DATABASE_URL="sqlite:///./license.db"
export LICENSE_ADMIN_TOKEN="dev-admin"
.venv/bin/uvicorn app.main:app --reload --port 8000
# выдать лицензию:
.venv/bin/python manage.py issue --tier Pro --max-users 50 --customer "ACME" --days 365
```

## API
| Метод | Путь | Назначение |
|-------|------|-----------|
| GET  | `/health` | Проверка живости |
| GET  | `/api/v1/public-key` | Публичный ключ подписи (Ed25519, base64) |
| POST | `/api/v1/challenge` | `{fingerprint}` → одноразовый nonce (TTL 120с) |
| POST | `/api/v1/activate` | `{license_key, fingerprint, nonce, device_pubkey, device_sig, hostname}` → подписанная лицензия |
| POST | `/api/v1/validate` | Продление lease (challenge-response) |
| POST | `/api/v1/deactivate` | Отзыв активации |
| POST | `/admin/licenses` | (admin) создать лицензию |
| GET  | `/admin/licenses` | (admin) список |
| GET  | `/admin/licenses/{key}/activations` | (admin) активации |
| POST | `/admin/licenses/{key}/revoke/{fp}` | (admin) отозвать |

Авторизация админки: `Authorization: Bearer $LICENSE_ADMIN_TOKEN`.

## Протокол (challenge-response)

Два ключа Ed25519:
- **сервера** — подписывает лицензию (клиент проверяет офлайн);
- **устройства** — клиент подписывает nonce (сервер проверяет онлайн).

```
1. POST /api/v1/challenge {fingerprint}          → {nonce}
2. message = "{nonce}|{fingerprint}|{license_key}"
   device_sig = Ed25519_sign(device_priv, message)
3. POST /api/v1/activate {..., nonce, device_pubkey, device_sig}
   сервер: nonce одноразовый → проверка подписи → привязка fp↔pubkey
   → подпись лицензии ключом сервера
```

nonce одноразовый (TTL 120с), `device_pubkey` привязывается к fingerprint при
первой активации. Это исключает replay и подмену ключа.

## Формат лицензии (ответ `/activate`)
```json
{
  "license_key": "LGC-...",
  "tier": "Pro",
  "server_fingerprint": "LGC-06AB-3288-0A65-0509",
  "max_users": 50,
  "features": {"webrtc": true},
  "issued_at": "...",
  "expires_at": "...",          // lease (продлевается)
  "hard_expires_at": "...",     // абсолютный срок лицензии
  "signature": "<base64 ed25519>",
  "public_key": "<base64>"
}
```
Подпись покрывает весь payload (кроме `signature` и `public_key`) в
каноническом виде (sorted keys, compact JSON).

## Клиент
Клиентская часть — в `license_mgr.py` панели:
`activate()`, `revalidate()`, `maybe_revalidate_daily()`,
`load_license()` (проверяет подпись/привязку/срок), откат на Free Core.
При активации клиент создаёт Ed25519-ключ устройства (`/opt/license_device_key.pem`,
0600) и проходит challenge-response.

## Деплой
См. `deploy/README.md`.
