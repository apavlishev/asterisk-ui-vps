import asyncio
import os
import json
import time
import glob
import logging
from google import genai
from google.genai import types
import websockets

logging.basicConfig(level=logging.INFO, format='%(asctime)s - LiveTranscribe - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

MONITOR_DIR = "/var/spool/asterisk/monitor/"
CONFIG_PATH = "/opt/integrations_config.json"
clients = set()

def get_api_key():
    try:
        with open(CONFIG_PATH, 'r') as f:
            cfg = json.load(f)
            return cfg.get('live_transcribe', {}).get('api_key', '')
    except Exception:
        return ''

def get_clean_id(filename):
    name = os.path.basename(filename)
    for ext in ['_rx.wav', '_tx.wav', '.wav']:
        name = name.replace(ext, '')
    return name

async def broadcast(message):
    msg = json.dumps(message, ensure_ascii=False)
    try:
        call_id = message.get('call_id')
        if call_id:
            clean_id = get_clean_id(call_id)
            jsonl_path = os.path.join(MONITOR_DIR, f"{clean_id}.wav.jsonl")
            with open(jsonl_path, 'a', encoding='utf-8') as f:
                f.write(msg + chr(10))
    except Exception as e:
        logger.error(f"Failed to save JSONL: {e}")

    if not clients: return
    for client in list(clients):
        try:
            await client.send(msg)
        except Exception:
            clients.discard(client)

def run_post_call_diarize(call_id, full_wav_path):
    api_key = get_api_key()
    if not api_key: return
    try:
        if not os.path.exists(full_wav_path) or os.path.getsize(full_wav_path) < 1000:
            return

        client = genai.Client(api_key=api_key)
        audio_file = client.files.upload(file=full_wav_path)
        interaction = client.interactions.create(
            model="gemini-3.5-transcribe",
            input=[{
                "type": "audio",
                "uri": audio_file.uri,
                "mime_type": audio_file.mime_type,
            }],
            generation_config={
                "transcription_config": {
                    "language_codes": ["ru-RU"],
                    "mode": {
                        "type": "verbatim",
                        "diarization_mode": "speaker",
                        "timestamp_granularities": ["word"],
                    }
                }
            }
        )
        
        messages = []
        current_speaker = None
        current_words = []
        for step in interaction.steps:
            if getattr(step, 'content', None):
                for content in step.content:
                    if getattr(content, 'annotations', None):
                        for ann in content.annotations:
                            spk = 'client' if getattr(ann, 'speaker', '') == 'spk:0' else 'operator'
                            if spk != current_speaker:
                                if current_words:
                                    messages.append({
                                        "type": "transcription",
                                        "call_id": call_id,
                                        "speaker": current_speaker,
                                        "text": ' '.join(current_words),
                                        "timestamp": time.time()
                                    })
                                    current_words = []
                                current_speaker = spk
                            current_words.append(getattr(ann, 'text', ''))
                        if current_words:
                            messages.append({
                                "type": "transcription",
                                "call_id": call_id,
                                "speaker": current_speaker,
                                "text": ' '.join(current_words),
                                "timestamp": time.time()
                            })

        if messages:
            clean_id = get_clean_id(call_id)
            jsonl_path = os.path.join(MONITOR_DIR, f"{clean_id}.wav.jsonl")
            with open(jsonl_path, 'w', encoding='utf-8') as f:
                for m in messages:
                    f.write(json.dumps(m, ensure_ascii=False) + chr(10))
            logger.info(f"Saved final Russian diarization for {clean_id} ({len(messages)} phrases)")
    except Exception as e:
        logger.error(f"Post-call diarization error for {call_id}: {e}")

async def stream_channel_to_gemini_live(call_id, wav_path, speaker):
    api_key = get_api_key()
    if not api_key: return

    client = genai.Client(api_key=api_key)
    config = {
        "response_modalities": ["TEXT"],
        "system_instruction": {
            "parts": [{"text": "You are a real-time speech transcriber for phone conversations. Accurately transcribe Russian speech into Russian Cyrillic text with punctuation. Never translate into other languages."}]
        }
    }

    logger.info(f"[{speaker.upper()}] Starting Gemini 3.5 Live for {call_id}")

    try:
        async with client.aio.live.connect(model="gemini-3.5-transcribe-live", config=config) as session:
            logger.info(f"[{speaker.upper()}] Live connected for {call_id}")

            async def send_loop():
                while not os.path.exists(wav_path):
                    await asyncio.sleep(0.5)

                with open(wav_path, "rb") as f:
                    f.seek(44)
                    idle_count = 0

                    while True:
                        chunk = f.read(2048)
                        if chunk:
                            idle_count = 0
                            try:
                                await session.send(input=types.LiveClientRealtimeInput(
                                    media_chunks=[types.Blob(data=chunk, mime_type="audio/pcm;rate=8000")]
                                ))
                            except Exception as se:
                                logger.error(f"Send error ({speaker}): {se}")
                            await asyncio.sleep(0.12)
                        else:
                            idle_count += 1
                            await asyncio.sleep(0.4)

                        if idle_count >= 8:
                            break

            async def receive_loop():
                last_text = ""
                async for response in session.receive():
                    text = None
                    if response.server_content:
                        sc = response.server_content
                        if getattr(sc, 'input_transcription', None) and getattr(sc.input_transcription, 'text', None):
                            text = sc.input_transcription.text.strip()
                        elif getattr(sc, 'interim_input_transcription', None) and getattr(sc.interim_input_transcription, 'text', None):
                            text = sc.interim_input_transcription.text.strip()

                    if text and text != last_text:
                        last_text = text
                        logger.info(f"[{speaker.upper()} LIVE] {text}")
                        await broadcast({
                            "type": "transcription",
                            "call_id": call_id,
                            "speaker": speaker,
                            "text": text,
                            "timestamp": time.time()
                        })

            await asyncio.gather(send_loop(), receive_loop())

    except Exception as e:
        logger.error(f"Gemini 3.5 Live error ({speaker}): {e}")

async def watch_for_new_calls():
    seen_files = set()
    
    if os.path.exists(MONITOR_DIR):
        seen_files.update(glob.glob(os.path.join(MONITOR_DIR, "*_rx.wav")))

    while True:
        try:
            if not os.path.exists(MONITOR_DIR):
                await asyncio.sleep(1)
                continue
                
            rx_wavs = glob.glob(os.path.join(MONITOR_DIR, "*_rx.wav"))
            for rx in rx_wavs:
                if rx not in seen_files:
                    seen_files.add(rx)
                    
                    tx = rx.replace("_rx.wav", "_tx.wav")
                    call_id = get_clean_id(rx)
                    full_wav = rx.replace("_rx.wav", "")
                    
                    logger.info(f"New active call detected: {call_id}.wav")
                    
                    t1 = asyncio.create_task(stream_channel_to_gemini_live(call_id, rx, "client"))
                    t2 = asyncio.create_task(stream_channel_to_gemini_live(call_id, tx, "operator"))

                    async def handle_post_call(cid, f_wav, task1, task2):
                        await asyncio.gather(task1, task2)
                        await asyncio.sleep(1)
                        if os.path.exists(f_wav):
                            await asyncio.to_thread(run_post_call_diarize, cid, f_wav)

                    asyncio.create_task(handle_post_call(call_id, full_wav, t1, t2))
                    
        except Exception as e:
            logger.error(f"Watcher error: {e}")
            
        await asyncio.sleep(0.5)

async def ws_handler(websocket):
    clients.add(websocket)
    try:
        await websocket.wait_closed()
    finally:
        clients.discard(websocket)

async def main():
    logger.info("Starting Gemini 3.5 Transcribe Live Daemon on ws://0.0.0.0:8889")
    server = await websockets.serve(ws_handler, "0.0.0.0", 8889)
    watcher = asyncio.create_task(watch_for_new_calls())
    await asyncio.gather(server.wait_closed(), watcher)

if __name__ == "__main__":
    asyncio.run(main())
