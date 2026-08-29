import os
import json

PLUGINS_DIR = os.path.join(os.path.dirname(__file__), 'plugins')

def load_marketplace_plugins(active_plugin_ids=None):
    """Dynamically reads all plugin manifests from /plugins folder."""
    if active_plugin_ids is None:
        active_plugin_ids = []

    plugins = []
    if os.path.exists(PLUGINS_DIR):
        for entry in os.listdir(PLUGINS_DIR):
            manifest_path = os.path.join(PLUGINS_DIR, entry, 'manifest.json')
            if os.path.exists(manifest_path):
                try:
                    with open(manifest_path, 'r', encoding='utf-8') as f:
                        meta = json.load(f)
                    p_id = meta.get('id')
                    meta['installed'] = (p_id in active_plugin_ids)
                    meta['dir_name'] = entry
                    
                    # Ensure category is set for filtering
                    if 'category' not in meta:
                        if 'crm' in p_id or 'telegram' in p_id:
                            meta['category'] = 'crm'
                            meta['category_name'] = 'CRM & Бизнес'
                        elif 'storage' in p_id or 'disk' in p_id or 'ftp' in p_id:
                            meta['category'] = 'storage'
                            meta['category_name'] = 'Облачные Диски & FTP'
                        elif 'ai' in p_id or 'voice' in p_id or 'transcribe' in p_id:
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
            "version": "v2.4.1",
            "icon": "graphic_eq",
            "color": "primary",
            "rating": "4.9 (1.2k)",
            "price": "$19",
            "price_sub": "/ mo",
            "installed": ("neural_transcribe" in active_plugin_ids),
            "description": "Real-time AI voice transcription (Local Whisper / Custom OpenAI API) with dual-channel speaker diarization and sentiment analysis."
        },
        {
            "id": "user_expansion_50",
            "name": "Multi-SIP Extension Pack (50 Users)",
            "category": "core",
            "category_name": "Capacity & Core",
            "version": "v1.5.0",
            "icon": "group_add",
            "color": "primary",
            "rating": "5.0 (3.1k)",
            "price": "$29",
            "price_sub": "one-time",
            "installed": ("user_expansion_50" in active_plugin_ids),
            "description": "Expands your base Free Core limit from 2 SIP extensions up to 50 active internal subscribers."
        },
        {
            "id": "ivr_visual_designer",
            "name": "Visual IVR Tree & Schedule Router",
            "category": "voice",
            "category_name": "AI & Voice",
            "version": "v2.0.0",
            "icon": "account_tree",
            "color": "tertiary",
            "rating": "4.8 (850)",
            "price": "$12",
            "price_sub": "/ mo",
            "installed": ("ivr_visual_designer" in active_plugin_ids),
            "description": "Interactive drag-and-drop IVR menu builder, DTMF key branching, working hours and holiday schedule routing."
        }
    ]

    existing_ids = {p['id'] for p in plugins}
    for item in extra_items:
        if item['id'] not in existing_ids:
            plugins.append(item)

    return plugins

if __name__ == '__main__':
    loaded = load_marketplace_plugins(['amocrm_pro', 'telegram_alerts'])
    print(f"Loaded {len(loaded)} plugins:")
    for p in loaded:
        print(f"- [{p['id']}] {p['name']} (Installed: {p.get('installed')})")

MARKETPLACE_PLUGINS = load_marketplace_plugins()
