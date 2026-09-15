#!/usr/bin/env python3
"""
Locale auto-translator.

Translates the remaining Russian strings in the non-Russian locale files via
the MyMemory public API, preserving placeholders and skipping identifiers.

Usage:
    python3 tools/translate_locales.py --lang en [--limit 50] [--dry-run]
    python3 tools/translate_locales.py --all [--limit 50]

Design notes:
- Only entries whose translated value is identical to the Russian source and
  contains Cyrillic are considered "untranslated".
- Keys that look like identifiers (nav_*, card_*_title, etc.) are translated by
  their human value, which is exactly what we want since the value is the RU text.
- Placeholders like {name}, %s, <b>...</b>, ${VAR} and URLs are protected: they
  are replaced with sentinels before the request and restored afterwards, so the
  machine translation cannot mangle them.
- Results are cached in tools/.translation_cache.json and committed per batch.
"""

import argparse
import glob
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request

try:
    import certifi
    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except Exception:
    SSL_CONTEXT = ssl.create_default_context()

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCALES_DIR = os.path.join(BASE, "locales")
CACHE_PATH = os.path.join(BASE, "tools", ".translation_cache.json")

MYMEMORY = "https://api.mymemory.translated.net/get"
GOOGLE = "https://translate.googleapis.com/translate_a/single"

LANG_MAP = {
    "en": "en",
    "es": "es",
    "ar": "ar",
    "de": "de",
    "fr": "fr",
    "hi": "hi",
    "ja": "ja",
    "pt": "pt",
    "zh": "zh-CN",
}

CYRILLIC = re.compile(r"[А-Яа-яЁё]")
PROTECT_PATTERNS = [
    (re.compile(r"\{[^{}]*\}"), "PLACEHOLDER"),
    (re.compile(r"\$\{[^}]*\}"), "SHELLVAR"),
    (re.compile(r"<[^>]+>"), "HTMLTAG"),
    (re.compile(r"https?://\S+"), "URL"),
    (re.compile(r"%[sd]"), "FMT"),
]


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_cache():
    if os.path.exists(CACHE_PATH):
        try:
            return load_json(CACHE_PATH)
        except Exception:
            pass
    return {}


def save_cache(cache):
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    save_json(CACHE_PATH, cache)


def protect(text):
    store = []

    def _repl(m):
        store.append(m.group(0))
        return f"__P{len(store)-1}__"

    for pattern, _label in PROTECT_PATTERNS:
        text = pattern.sub(_repl, text)
    return text, store


def restore(text, store):
    for i, original in enumerate(store):
        text = text.replace(f"__P{i}__", original)
    return text


def translate(text, target, source="ru", retries=3):
    """Returns translated text or None on failure."""
    if not text or not text.strip():
        return text
    if not CYRILLIC.search(text):
        return text

    protected, store = protect(text)
    result = None

    # Primary: Google Unofficial (stable, no key needed)
    try:
        params = urllib.parse.urlencode({
            "client": "dict-chrome-ex",
            "dt": "t",
            "sl": source,
            "tl": target,
            "q": protected,
        })
        url = f"{GOOGLE}?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "AsteriskLocalizer/1.0"})
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(req, timeout=20, context=SSL_CONTEXT) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                # data format: [[["TRANSLATION","original",...],...]]
                if isinstance(data, list) and data and isinstance(data[0], list):
                    translated = data[0][0][0]
                    if translated and translated.strip() != protected.strip():
                        result = restore(translated, store)
                        break
            except Exception:
                if attempt == retries - 1:
                    pass
                time.sleep(0.4 + attempt)
    except Exception:
        pass

    # Fallback: MyMemory
    if not result:
        try:
            params = urllib.parse.urlencode({
                "q": protected,
                "langpair": f"{source}|{target}",
            })
            url = f"{MYMEMORY}?{params}"
            req = urllib.request.Request(url, headers={"User-Agent": "AsteriskLocalizer/1.0"})
            for attempt in range(retries):
                try:
                    with urllib.request.urlopen(req, timeout=20, context=SSL_CONTEXT) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                    tdata = (data.get("responseData") or {})
                    t = tdata.get("translatedText")
                    if t and t.strip() != protected.strip():
                        result = restore(t, store)
                        break
                except Exception:
                    if attempt == retries - 1:
                        pass
                    time.sleep(0.4 + attempt)
        except Exception:
            pass

    if result:
        return result

    # Log once per text
    print(f"   [!] translate failed ({text[:50]!r})")
    return None


def untranslated_keys(ru, target_data):
    out = []
    for k, v in ru.items():
        if not isinstance(v, str):
            continue
        tv = target_data.get(k)
        if tv is None or tv == v:
            if CYRILLIC.search(v):
                out.append(k)
    return out


def process_lang(lang, limit=None, dry_run=False, delay=0.35):
    ru = load_json(os.path.join(LOCALES_DIR, "ru.json"))
    path = os.path.join(LOCALES_DIR, f"{lang}.json")
    data = load_json(path)
    target = LANG_MAP.get(lang)
    if not target:
        print(f"skip {lang}: no language mapping")
        return 0

    cache = load_cache()
    keys = untranslated_keys(ru, data)
    if limit:
        keys = keys[:limit]
    print(f"=== {lang}: {len(keys)} keys to translate -> {target}")

    done = 0
    for idx, key in enumerate(keys, 1):
        src = ru[key]
        cache_key = f"{lang}:{key}"
        translated = cache.get(cache_key)
        if not translated:
            translated = translate(src, target)
            if translated:
                cache[cache_key] = translated
                save_cache(cache)
            time.sleep(delay)
        if translated:
            data[key] = translated
            done += 1
        if idx % 25 == 0:
            print(f"   ... {idx}/{len(keys)}")
            if not dry_run:
                save_json(path, data)

    if not dry_run:
        save_json(path, data)
    print(f"=== {lang}: translated {done}")
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", action="append", default=[])
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    langs = args.lang
    if args.all:
        langs = ["en", "es", "ar", "de", "fr", "hi", "ja", "pt", "zh"]
    if not langs:
        print("nothing to do; pass --lang xx or --all")
        return 1

    total = 0
    for lang in langs:
        total += process_lang(lang, limit=args.limit, dry_run=args.dry_run)
    print(f"TOTAL translated: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
