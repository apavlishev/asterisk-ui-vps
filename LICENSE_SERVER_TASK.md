# Лицензирование установки на сервер (задача)

> Статус: **база реализована, требуется внедрение и доработки**.
> Дата: 2026-09-16

## Суть

Лицензия выдаётся на **конкретную установку панели на конкретном сервере** и
привязывается к железу/серверу через fingerprint. Перенос лицензии на другой
сервер без переактивации невозможен.

Никаких мобильных платформ (Android/Huawei/Apple) — только серверная панель.

## Что уже есть

### Сервер лицензий — `license-server/`
- FastAPI + SQLAlchemy, Ed25519-подпись лицензий.
- Endpoints: `/api/v1/activate`, `/validate`, `/deactivate`, `/api/v1/public-key`.
- Админ-API: создание/список лицензий, список/отзыв активаций (Bearer-токен).
- Rate-limiting, аудит в БД, CLI `manage.py` (issue/list/activations/revoke/public-key).
- Деплой: `license-server/deploy/` (systemd + nginx + Let's Encrypt).

### Клиент — `license_mgr.py` (в панели)
- `get_server_fingerprint()` — стабильный ID сервера (DMI UUID/machine-id + MAC).
- `load_license()` — проверка подписи Ed25519, привязки к fingerprint, срока.
- `activate()` / `revalidate()` / `maybe_revalidate_daily()`.
- Откат на Free Core (2 абонента), если лицензии нет/истекла.
- Лимит абонентов и активация плагинов через `get_max_allowed_users()` /
  `is_plugin_active()`.

### Формат лицензии (ответ сервера)
```json
{
  "license_key": "LGC-...",
  "tier": "Pro",
  "server_fingerprint": "LGC-06AB-3288-0A65-0509",
  "max_users": 50,
  "features": {"webrtc": true},
  "issued_at": "...",
  "expires_at": "...",        // lease, продлевается (default 30 дней)
  "hard_expires_at": "...",   // абсолютный срок
  "signature": "<base64 ed25519>",
  "public_key": "<base64>"
}
```

## Чеклист внедрения

- [ ] Развернуть `license-server/` (PostgreSQL + nginx + TLS) по `deploy/README.md`.
- [ ] Сгенерировать Ed25519-ключ, забрать `manage.py public-key`.
- [ ] Вшить публичный ключ в панель (`LICENSE_PUBLIC_KEY` или константа в `license_mgr.py`).
- [ ] Прописать на сервере панели `LICENSE_SERVER_URL` и `LICENSE_KEY`.
- [ ] Выполнить первичную активацию: `python3 -c "import license_mgr; print(license_mgr.activate())"`.
- [ ] Настроить ежедневную ревалидацию (cron) — `maybe_revalidate_daily()`.
- [ ] Проверить, что при отсутствии/истечении лицензии панель корректно
      ограничивает функции (Free Core).

## Доработки (to-do)

- [ ] UI в панели: раздел «Лицензия» — статус, тариф, срок, кнопка активировать/
      обновить, отображение fingerprint для передачи вендору.
- [ ] Защита от подмены: не хранить лицензию в открытом виде; проверять подпись
      при каждом старте.
- [ ] Привязка дополнительных возможностей к `features` (webrtc, transcribe,
      количество транков), а не только `max_users`.
- [ ] Оффлайн grace: если сервер недоступен — работать до `expires_at`, затем
      предупреждение и мягкая деградация.
- [ ] Изящная обработка смены fingerprint (переезд на другое железо):
      деактивация старой + активация новой с лимитом.
- [ ] Аудит активаций на стороне панели (кто/когда/с какого IP).
- [ ] Резервное копирование БД лицензий и защита приватного ключа подписи.
- [ ] Опционально: биллинг/интеграция с платёжной системой для выдачи ключей.

## Открытые вопросы

- [ ] Сколько активаций на один ключ (1 сервер или пул)?
- [ ] Тарифы и лимиты (Free/Pro/Enterprise, max_users, транки, плагины).
- [ ] Где хостить сервер лицензий (этот VPS или отдельный).
- [ ] Требуется ли обязательная онлайн-проверка (без offline-grace) или grace допустим.

## Ключевые файлы

- `license-server/app/main.py` — API.
- `license-server/app/licensing.py` — логика активации/валидации.
- `license-server/app/signing.py` — Ed25519.
- `license-server/manage.py` — CLI.
- `license_mgr.py` — клиент в панели.
- `app.py` — проверка лимита абонентов (`get_max_allowed_users`).
