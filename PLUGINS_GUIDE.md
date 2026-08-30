# 🧩 Руководство по разработке модулей и плагинов для Asterisk PBX (Logic Core)

## 📌 Архитектура плагинов
Каждый модуль является автономным пакетом, изолированным в директории `plugins/plugin_<name>/`.

---

## 📁 Обязательная файловая структура нового плагина

```text
plugins/plugin_mycrm/
├── manifest.json       # Метаданные плагина (ID, имя, категория, версия, иконка, цвет)
├── plugin_main.py      # Исполняемый Python-код модуля (обработчики событий, хуки)
└── locales/            # 🌐 ОБЯЗАТЕЛЬНАЯ папка переводов на ТОП-10 мировых языков
    ├── ru.json         # Русский (базовый)
    ├── en.json         # English
    ├── es.json         # Español
    ├── ar.json         # العربية (поддержка RTL)
    ├── zh.json         # 中文
    ├── fr.json         # Français
    ├── de.json         # Deutsch
    ├── pt.json         # Português
    ├── ja.json         # 日本語
    └── hi.json         # हिन्दी
```

---

## 🌐 Правила локализации (i18n):
1. **Каждый плагин обязан содержать папку `locales/`** с локализованными `name` и `desc`.
2. Пример содержимого `locales/en.json`:
```json
{
  "name": "MyCRM Cloud Connector",
  "desc": "Automated two-way call logging, pop-up contact cards, and audio archiving for MyCRM."
}
```
3. При создании новых UI-страниц все статические строки, подсказки и кнопки добавляются в общесистемный словарь `locales/*.json` и `locales/i18n_matrix.json`.

---

## ⚙️ Пример `manifest.json`

```json
{
  "id": "mycrm",
  "name": "MyCRM Cloud Sync",
  "category": "crm",
  "category_name": "CRM & Бизнес",
  "version": "v1.0.0",
  "author": "MyCompany Dev Team",
  "icon": "hub",
  "color": "blue-500",
  "rating": "5.0 (100)",
  "price": "Free",
  "price_sub": "Included",
  "description": "Сквозная интеграция вызовов с MyCRM.",
  "entry_point": "plugin_main.py"
}
```

---

## 🐍 Пример `plugin_main.py`

```python
import requests
import json

def init_plugin(app, config):
    """Инициализация плагина при старте ядра PBX"""
    pass

def on_call_hangup(call_info, config):
    """
    Хук завершения звонка.
    call_info содержит:
      - call_id: Уникальный ID звонка в Asterisk
      - src: Номер звонящего (Caller)
      - dst: Номер получателя (Callee)
      - duration: Длительность разговора в секундах
      - disposition: ANSWERED, NO ANSWER, BUSY, FAILED
      - recording_url: Прямая ссылка на аудиозапись из облака
    """
    plugin_cfg = config.get("mycrm", {})
    if not plugin_cfg.get("enabled"):
        return None

    # Отправка в API вашей CRM
    # ...
    return True
```
