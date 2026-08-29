#!/usr/bin/env python3
import os
import json
import glob
import subprocess
import importlib.util

CONFIG_FILE = "/opt/integrations_config.json"
VERSION_FILE = "/opt/asterisk-gui/version.json"
MIGRATIONS_DIR = "/opt/asterisk-gui/migrations"

def v_to_tuple(v):
    return tuple(map(int, (v.split("."))))

def main():
    if not os.path.exists(CONFIG_FILE):
        print("Config file not found. Nothing to migrate.")
        return

    # Загружаем текущий конфиг
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    
    current_version = cfg.get("version", "1.0.0")
    
    # Загружаем целевую версию
    target_version = "1.0.0"
    if os.path.exists(VERSION_FILE):
        with open(VERSION_FILE, "r", encoding="utf-8") as f:
            v_data = json.load(f)
            target_version = v_data.get("version", "1.0.0")

    print(f"Current version: {current_version}, Target version: {target_version}")

    if v_to_tuple(current_version) >= v_to_tuple(target_version):
        print("System is up to date.")
        return

    # Ищем скрипты миграций
    if os.path.exists(MIGRATIONS_DIR):
        migrations = glob.glob(os.path.join(MIGRATIONS_DIR, "migration_*.py"))
        
        # migration_1.0.1.py, migration_1.0.2.py
        def get_v(path):
            basename = os.path.basename(path)
            # migration_1.0.1.py -> 1.0.1
            v_str = basename.replace("migration_", "").replace(".py", "")
            return v_to_tuple(v_str)

        migrations.sort(key=get_v)

        for mig in migrations:
            mig_v = get_v(mig)
            if mig_v > v_to_tuple(current_version) and mig_v <= v_to_tuple(target_version):
                print(f"Running migration {os.path.basename(mig)}...")
                
                # Динамически импортируем и запускаем
                spec = importlib.util.spec_from_file_location("migration_module", mig)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                
                if hasattr(mod, 'run_migration'):
                    mod.run_migration(cfg)
                
                # Обновляем версию в конфиге пошагово
                mig_v_str = ".".join(map(str, mig_v))
                cfg["version"] = mig_v_str
                
                with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, indent=2, ensure_ascii=False)
                
                print(f"Migration {mig_v_str} applied successfully.")

    # В конце, если нет миграций, но версия увеличилась
    if cfg.get("version", "1.0.0") != target_version:
        cfg["version"] = target_version
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        print(f"Version bumped to {target_version}")

if __name__ == "__main__":
    main()
