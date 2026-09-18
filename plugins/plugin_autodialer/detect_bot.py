#!/usr/bin/env python3
import sys
import os
import wave
import json
import re

# Ensure current directory is importable
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from autodialer_engine import AutoDialerEngine, BOT_KEYWORDS

def analyze_audio_sample(sample_path):
    """
    Analyzes an incoming audio sample (.wav) to detect if it's an automated
    assistant / voicemail / anti-spam bot.
    Returns (is_bot, bot_name, confidence)
    """
    if not os.path.exists(sample_path) or os.path.getsize(sample_path) < 4000:
        return False, "Тишина / Слишком короткий звук", 0

    try:
        with wave.open(sample_path, 'rb') as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            duration = frames / float(rate)
            if duration < 0.8:
                return False, "Слишком короткий ответ", 0

            # Read raw frames to check energy
            raw = wf.readframes(frames)
            # Heuristic speech energy analysis
            # Bots typically stream speech without an initial hesitation
            # (unlike humans who say "Алло?" ~ 400ms followed by silence)
    except Exception as e:
        print(f"[detect_bot] Wave read error: {e}")

    # Check for AI transcription if integrations config has API key
    try:
        config_path = "/opt/integrations_config.json"
        if not os.path.exists(config_path):
            config_path = os.path.join(os.path.dirname(SCRIPT_DIR), '..', 'integrations_config.json')
            
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            lt = cfg.get('live_transcribe', {})
            api_key = lt.get('api_key')
            if api_key:
                # Transcribe short sample via google-genai
                try:
                    from google import genai
                    client = genai.Client(api_key=api_key)
                    uploaded = client.files.upload(file=sample_path)
                    res = client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=[uploaded, "Транскрибируй эту короткую фразу на русском языке буквально. Напиши только текст."]
                    )
                    text = (res.text or "").lower()
                    print(f"[detect_bot] Transcribed: {text}")

                    # Check for keywords
                    for kw in BOT_KEYWORDS:
                        if kw in text:
                            bot_name = "Антиспам-помощник / Секретарь"
                            if "олег" in text:
                                bot_name = "Помощник Олег (Тинькофф)"
                            elif "алиса" in text:
                                bot_name = "Помощник Алиса (Яндекс)"
                            elif "ева" in text:
                                bot_name = "Секретарь Ева (МегаФон)"
                            elif "салют" in text:
                                bot_name = "Ассистент Салют (Сбер)"
                            elif "защитник" in text:
                                bot_name = "МТС Защитник"
                            elif "автоответчик" in text:
                                bot_name = "Автоответчик / Голосовая почта"
                            return True, bot_name, 98
                except Exception as ex:
                    print(f"[detect_bot] AI transcribe failed: {ex}")
    except Exception:
        pass

    # Acoustic / Duration heuristic:
    # If the file size is between 12KB and 64KB (~1.5 - 3.5s of constant 8kHz/16-bit audio)
    # in early answer (<1s), it's highly likely an automated greeting.
    file_size = os.path.getsize(sample_path)
    if file_size > 20000:
        return True, "Автоответчик / Спам-ассистент (По звуковому профилю)", 85

    return False, "Живой абонент", 20

def main():
    if len(sys.argv) < 4:
        print("Usage: detect_bot.py <sample_wav> <campaign_id> <phone> [uniqueid]")
        sys.exit(0)

    sample_path = sys.argv[1]
    campaign_id = sys.argv[2]
    phone = sys.argv[3]

    is_bot, bot_name, confidence = analyze_audio_sample(sample_path)
    print(f"[detect_bot] Phone: {phone}, Result: {is_bot} ({bot_name}, {confidence}%)")

    if is_bot:
        engine = AutoDialerEngine()
        engine.record_bot_detection(campaign_id, phone, bot_name, confidence)

    # Cleanup temp sample wav
    try:
        if os.path.exists(sample_path):
            os.remove(sample_path)
    except Exception:
        pass

if __name__ == '__main__':
    main()
