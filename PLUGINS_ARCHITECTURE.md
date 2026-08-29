# Архитектура и Спецификация Модулей (Плагинов) Logic Core PBX

Данный документ описывает стандарт разработки, криптографической упаковки, структуры файлов и жизненного цикла модулей (расширений) для платформы **Asterisk PBX Logic Core**.

---

## 1. Концептуальная Схема Архитектуры Модулей

```
┌────────────────────────────────────────────────────────────────────────┐
│                      1. МАРКЕТПЛЕЙС & ДИСТРИБУЦИЯ                      │
│   ┌────────────────────────┐         ┌─────────────────────────────┐   │
│   │ Logic Core Marketplace │ ──────► │ Cryptographic Sign (SHA-256)│   │
│   └────────────────────────┘         └──────────────┬──────────────┘   │
└─────────────────────────────────────────────────────┼──────────────────┘
                                                      │ .zip OTA Package
                                                      ▼
┌────────────────────────────────────────────────────────────────────────┐
│                   2. CORE PBX ENGINE (plugin_manager.py)               │
│   ┌────────────────────────┐         ┌─────────────────────────────┐   │
│   │ Signature Verification │ ──────► │ Safe Unpack to /plugins/<id>│   │
│   └────────────────────────┘         └──────────────┬──────────────┘   │
│                                                     │                  │
│       ┌─────────────────────────────────────────────┴───────────┐      │
│       ▼                                                         ▼      │
│   ┌────────────────────────┐         ┌─────────────────────────────┐   │
│   │ AMI/CDR Call Hooks     │         │ Dynamic REST / Webhook APIs │   │
│   └────────────────────────┘         └─────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────────┘
                                                      │
                                                      ▼
┌────────────────────────────────────────────────────────────────────────┐
│                   3. WEB GUI & ПОЛЬЗОВАТЕЛЬСКИЙ ИНТЕРФЕЙС              │
│   ├── Боковое меню: Раскрывающийся аккордеон «Расширения»              │
│   ├── Персональная вкладка настроек: /tab-plugin-<id>                  │
│   └── OAuth2 / Device Code кнопки авторизации (Яндекс ID, Google, amo) │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Стандартная Структура Файлов Модуля

Каждый плагин представляет собой изолированную папку внутри директории `/plugins/` (или упакованный `.zip` архив для маркетплейса) со следующей обязательной структурой:

```
plugins/plugin_sample/
├── manifest.json         # [Обязательно] Метаданные, права, категория, UI-роуты
├── plugin_main.py        # [Обязательно] Точка входа: хуки звонков, бэкенд логика
├── settings_schema.json  # [Опционально] Схема полей формы для автогенерации UI
├── assets/               # [Опционально] Иконки, звуковые пресеты, картинки
│   └── icon.svg
├── templates/            # [Опционально] Кастомные HTML-шаблоны виджетов
│   └── settings.html
└── README.md             # Документация модуля и changelog
```

---

## 3. Спецификация `manifest.json`

Файл `manifest.json` содержит строго типизированный дескриптор расширения:

```json
{
  "id": "sample_integration",
  "name": "Sample CRM & Event Router",
  "version": "1.0.0",
  "author": "Logic Core Team",
  "category": "crm",
  "category_name": "CRM & Бизнес",
  "icon": "sync_alt",
  "color": "primary",
  "rating": "5.0 (120)",
  "price": "Free",
  "price_sub": "/ included",
  "description": "Пример эталонного плагина: перехват звонков из AMI, отправка вебхуков и синхронизация контактов.",
  "permissions": [
    "ami_events",
    "cdr_read",
    "call_recordings",
    "network_outbound"
  ],
  "entry_point": "plugin_main.py",
  "settings_route": "/settings/sample-integration",
  "api_routes": [
    {
      "path": "/api/sample/status",
      "methods": ["GET"],
      "handler": "get_status"
    },
    {
      "path": "/api/sample/sync",
      "methods": ["POST"],
      "handler": "sync_now"
    }
  ]
}
```

### Доступные категории (`category`):
* `crm` — Интеграции с CRM-системами (amoCRM, Bitrix24, МойСклад);
* `storage` — Хранилища записей (Яндекс.Диск, Google Drive, AWS S3, FTP/SFTP);
* `ai` — AI-транскрибация речи (Whisper, ChatGPT, распознавание эмоций);
* `core` — Расширение емкости абонентов, транков и маршрутизации.

---

## 4. Эталонная Реализация Точки Входа (`plugin_main.py`)

Модуль обязан реализовывать базовый интерфейс жизненного цикла `BasePlugin`:

```python
"""
plugin_main.py - Эталонный модуль интеграции Logic Core
"""
import logging

logger = logging.getLogger("PluginSample")

class Plugin:
    def __init__(self, core_context):
        """
        Инициализация плагина.
        :param core_context: Контекст ядра (доступ к Asterisk AMI, базе CDR и конфигурации)
        """
        self.context = core_context
        self.config = core_context.get_plugin_config("sample_integration")
        self.enabled = self.config.get("enabled", False)

    def on_load(self):
        """Вызывается при старте ядра PBX или установке плагина."""
        logger.info("[PluginSample] Модуль успешно загружен и инициализирован.")

    def on_unload(self):
        """Вызывается при отключении или удалении плагина."""
        logger.info("[PluginSample] Модуль выгружен.")

    # ================= EVENT HOOKS =================

    def on_call_incoming(self, call_data):
        """
        Срабатывает при поступлении входящего звонка в транк.
        :param call_data: dict { 'caller_id': '79991234567', 'exten': '101', 'uniqueid': '...' }
        """
        if not self.enabled:
            return

        caller = call_data.get('caller_id')
        logger.info(f"[PluginSample] Входящий вызов от {caller}. Поиск клиента в базе...")

    def on_call_hangup(self, cdr_record):
        """
        Срабатывает после завершения звонка и записи разговора.
        :param cdr_record: dict { 'duration': 45, 'disposition': 'ANSWERED', 'record_file': '/var/spool/asterisk/monitor/...' }
        """
        if not self.enabled:
            return

        rec_file = cdr_record.get('record_file')
        logger.info(f"[PluginSample] Звонок завершен. Отправка записи {rec_file} во внешнюю систему...")

    # ================= API HANDLERS =================

    def get_status(self, request):
        """REST API хендлер для Web GUI."""
        return {
            "success": True,
            "status": "online" if self.enabled else "disabled",
            "version": "1.0.0"
        }

    def sync_now(self, request):
        """Ручной запуск синхронизации."""
        return {
            "success": True,
            "message": "Синхронизация успешно выполнена."
        }
```

---

## 5. Процесс Сборки и Криптографической Защиты (.ZIP OTA)

Для публикации в Маркетплейсе модуль упаковывается в `.zip` архив:

```bash
# 1. Архивация исходников модуля
cd plugins/plugin_sample/
zip -r ../sample_integration.zip ./* -x "*.pyc" "__pycache__/*"

# 2. Создание контрольной суммы (SHA-256 Signature)
shasum -a 256 ../sample_integration.zip > ../sample_integration.sig
```

При загрузке архива в панель Asterisk Web GUI через **«Установить из .ZIP»**:
1. `plugin_manager.py` проверяет контрольную сумму и целостность архива;
2. Валидирует `manifest.json` на наличие обязательных полей;
3. Безопасно распаковывает файлы в `/plugins/<id>`;
4. Регистрирует персональный экран настроек в левом боковом меню **«Расширения»** без перезагрузки АТС.
