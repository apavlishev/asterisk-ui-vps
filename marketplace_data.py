import os
import json

PLUGINS_DIR = os.path.join(os.path.dirname(__file__), 'plugins')

def load_marketplace_plugins(active_plugin_ids=None, disabled_plugin_ids=None, lang_code='ru'):
    """Dynamically reads all plugin manifests and applies per-plugin localized translations from /locales."""
    if active_plugin_ids is None:
        active_plugin_ids = []
    if disabled_plugin_ids is None:
        disabled_plugin_ids = []

    plugins = []
    if os.path.exists(PLUGINS_DIR):
        for entry in os.listdir(PLUGINS_DIR):
            plugin_path = os.path.join(PLUGINS_DIR, entry)
            manifest_path = os.path.join(plugin_path, 'manifest.json')
            if os.path.exists(manifest_path):
                try:
                    with open(manifest_path, 'r', encoding='utf-8') as f:
                        meta = json.load(f)
                    p_id = meta.get('id')
                    meta['installed'] = True
                    meta['enabled'] = (p_id not in disabled_plugin_ids)
                    meta['dir_name'] = entry
                    
                    # 🌐 Check for Plugin-Specific Locale
                    loc_file = os.path.join(plugin_path, 'locales', f'{lang_code}.json')
                    if not os.path.exists(loc_file):
                        loc_file = os.path.join(plugin_path, 'locales', 'en.json')
                    if os.path.exists(loc_file):
                        try:
                            with open(loc_file, 'r', encoding='utf-8') as lf:
                                loc_data = json.load(lf)
                                if 'name' in loc_data: meta['name'] = loc_data['name']
                                if 'desc' in loc_data: meta['description'] = loc_data['desc']
                        except Exception:
                            pass

                    # Ensure category is set for filtering
                    if 'category' not in meta:
                        if 'crm' in p_id or 'telegram' in p_id:
                            meta['category'] = 'crm'
                            meta['category_name'] = 'CRM & Бизнес'
                        elif 'storage' in p_id or 'disk' in p_id or 'ftp' in p_id:
                            meta['category'] = 'storage'
                            meta['category_name'] = 'Облачные Диски & FTP'
                        elif 'ai' in p_id or 'voice' in p_id or 'transcribe' in p_id or 'whisper' in p_id:
                            meta['category'] = 'ai'
                            meta['category_name'] = 'AI & Аналитика'
                        else:
                            meta['category'] = 'core'
                            meta['category_name'] = 'Ядро & Емкость'
                            
                    plugins.append(meta)
                except Exception as e:
                    print(f"Error loading plugin {entry}: {e}")

    # Built-in extra marketplace catalog items
    extra_items = [
        {
            "id": "neural_transcribe",
            "name": "Neural Transcribe & AI Analytics",
            "category": "ai",
            "category_name": "AI & Voice",
            "version": "v2.5.0",
            "icon": "graphic_eq",
            "color": "primary",
            "rating": "4.9 (1.8k)",
            "price": "$19",
            "price_sub": "/ mo",
            "installed": True,
            "enabled": ("neural_transcribe" not in disabled_plugin_ids and "ai_whisper" not in disabled_plugin_ids),
            "description": "Локальное или удаленное (OpenAI/Groq/Ollama) распознавание речи, разделение по ролям (L: оператор, R: клиент), суммаризация и оценка качества."
        },
        {
            "id": "user_expansion_50",
            "name": "Multi-SIP Extension Pack (50 Users)",
            "category": "core",
            "category_name": "Core Capacity",
            "version": "v1.0.0",
            "icon": "group_add",
            "color": "secondary",
            "rating": "5.0 (340)",
            "price": "$29",
            "price_sub": "/ one-time",
            "installed": False,
            "enabled": False,
            "description": "Enterprise expansion license unlocking +50 internal SIP extensions and real-time concurrent call capacity."
        }
    ]

    existing_ids = [p['id'] for p in plugins]
    for item in extra_items:
        if item['id'] not in existing_ids and item['id'] != 'neural_transcribe':
            plugins.append(item)

    return plugins
