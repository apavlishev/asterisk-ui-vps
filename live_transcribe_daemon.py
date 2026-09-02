import asyncio
import os
import json
import time
import glob
import io
import wave
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

def make_wav_bytes(pcm_data, sample_rate=8000, channels=1, sampwidth=2):
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(channels)
        w.setsampwidth(sampwidth)
        w.setframerate(sample_rate)
        w.writeframes(pcm_data)
    return buf.getvalue()

async def broadcast(message):
    msg = json.dumps(message, ensure_ascii=False)
    try:
        call_id = message.get('call_id')
        if call_id:
            jsonl_path = os.path.join(MONITOR_DIR, f"{call_id}.wav.jsonl")
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

async def stream_channel_worker(call_id, wav_path, speaker):
    api_key = get_api_key()
    if not api_key:
        logger.warning("No Gemini API key configured")
        return

    client = genai.Client(api_key=api_key)
    logger.info(f"[{speaker.upper()}] Starting stream worker for {wav_path}")

    # Wait for file creation
    waited = 0
    while not os.path.exists(wav_path) and waited < 10:
        await asyncio.sleep(0.5)
        waited += 0.5

    if not os.path.exists(wav_path):
        logger.warning(f"File {wav_path} not found after 10s")
        return

    # Buffer & state
    last_size = 0
    idle_count = 0
    chunk_buffer = bytearray()
    CHUNK_THRESHOLD = 32000  # ~2 seconds of 8kHz 16-bit mono audio (16000 bytes/sec)

    try:
        with open(wav_path, "rb") as f:
            f.seek(44)  # Skip standard 44-byte WAV header

            while True:
                new_data = f.read(4096)
                if new_data:
                    chunk_buffer.extend(new_data)
                    idle_count = 0
                else:
                    idle_count += 1
                    await asyncio.sleep(0.5)

                # Process chunk if buffer reaches threshold or call has stopped growing
                if len(chunk_buffer) >= CHUNK_THRESHOLD or (idle_count >= 3 and len(chunk_buffer) >= 16000):
                    raw_pcm = bytes(chunk_buffer)
                    chunk_buffer.clear()
                    
                    try:
                        wav_payload = make_wav_bytes(raw_pcm)
                        resp = await client.aio.models.generate_content(
                            model='gemini-2.5-flash',
                            contents=[
                                types.Part.from_bytes(data=wav_payload, mime_type='audio/wav'),
                                'Транскрибируй короткую фразу телефонного разговора на русском языке. Напиши только распознанный текст без комментариев. Если звука нет или неразборчивый шум, напиши [тишина].'
                            ]
                        )
                        text = resp.text.strip() if resp and resp.text else ''
                        if text and '[тишина]' not in text.lower():
                            logger.info(f"[{speaker.upper()}] Live text: {text}")
                            await broadcast({
                                "type": "transcription",
                                "call_id": call_id,
                                "speaker": speaker,
                                "text": text,
                                "timestamp": time.time()
                            })
                    except Exception as ge:
                        logger.error(f"Gemini transcribe error ({speaker}): {ge}")

                # If file hasn't grown for 3 seconds, call is finished
                if idle_count >= 6:
                    break

        # Final flush for any remaining audio (at least 0.5s)
        if len(chunk_buffer) >= 8000:
            try:
                wav_payload = make_wav_bytes(bytes(chunk_buffer))
                resp = await client.aio.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=[
                        types.Part.from_bytes(data=wav_payload, mime_type='audio/wav'),
                        'Транскрибируй короткую фразу разговора на русском языке. Напиши только распознанный текст. Если звука нет, напиши [тишина].'
                    ]
                )
                text = resp.text.strip() if resp and resp.text else ''
                if text and '[тишина]' not in text.lower():
                    await broadcast({
                        "type": "transcription",
                        "call_id": call_id,
                        "speaker": speaker,
                        "text": text,
                        "timestamp": time.time()
                    })
            except Exception as ge:
                logger.error(f"Final flush error: {ge}")

        logger.info(f"[{speaker.upper()}] Worker finished for {call_id}")

    except Exception as e:
        logger.error(f"Worker exception ({speaker}): {e}")

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
                    call_id = os.path.basename(rx).replace("_rx.wav", "")
                    
                    logger.info(f"New active call detected: {call_id}.wav. Starting live transcription streams.")
                    
                    asyncio.create_task(stream_channel_worker(call_id, rx, "client"))
                    asyncio.create_task(stream_channel_worker(call_id, tx, "operator"))
                    
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
    logger.info("Starting Live Transcribe Streaming Daemon on ws://0.0.0.0:8889")
    server = await websockets.serve(ws_handler, "0.0.0.0", 8889)
    watcher = asyncio.create_task(watch_for_new_calls())
    await asyncio.gather(server.wait_closed(), watcher)

if __name__ == "__main__":
    asyncio.run(main())
