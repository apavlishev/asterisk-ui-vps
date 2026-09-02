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
    except:
        return ''

async def broadcast(message):
    if not clients: return
    msg = json.dumps(message)
    # create a copy of the set to avoid RuntimeError: Set changed size during iteration
    for client in list(clients):
        try:
            await client.send(msg)
        except Exception:
            clients.discard(client)

async def stream_channel_to_gemini(call_id, wav_path, speaker):
    api_key = get_api_key()
    if not api_key: return

    client = genai.Client(api_key=api_key)
    
    # 8kHz PCM 16-bit mono
    config = types.LiveConnectConfig(
        response_modalities=[types.LiveModality.TEXT],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Aoede")
            )
        )
    )

    try:
        async with client.aio.live.connect(model="gemini-3.5-transcribe-live", config=config) as session:
            
            async def send_loop():
                # Wait for file to exist
                while not os.path.exists(wav_path):
                    await asyncio.sleep(0.5)
                
                with open(wav_path, "rb") as f:
                    # stream indefinitely while the file is growing
                    while True:
                        chunk = f.read(4096)
                        if not chunk:
                            # if Asterisk is still writing, wait
                            await asyncio.sleep(0.5)
                            continue
                        
                        await session.send(
                            input=types.LiveClientRealtimeInput(
                                media_chunks=[types.Blob(data=chunk, mime_type="audio/pcm;rate=8000")]
                            )
                        )
                        await asyncio.sleep(0.25)
            
            async def receive_loop():
                async for response in session.receive():
                    if response.text:
                        await broadcast({
                            "type": "transcription",
                            "call_id": call_id,
                            "speaker": speaker,
                            "text": response.text,
                            "timestamp": time.time()
                        })

            await asyncio.gather(send_loop(), receive_loop())

    except Exception as e:
        err_msg = f"Gemini API Error ({speaker}): {str(e)}"
        logger.error(err_msg)
        await broadcast({
            "type": "error",
            "call_id": call_id,
            "message": err_msg,
            "timestamp": time.time()
        })


async def watch_for_new_calls():
    seen_files = set()
    
    # Pre-populate seen files so we don't transcribe old ones
    if os.path.exists(MONITOR_DIR):
        seen_files.update(glob.glob(os.path.join(MONITOR_DIR, "*_rx.wav")))

    while True:
        try:
            if not os.path.exists(MONITOR_DIR):
                await asyncio.sleep(2)
                continue
                
            rx_wavs = glob.glob(os.path.join(MONITOR_DIR, "*_rx.wav"))
            for rx in rx_wavs:
                if rx not in seen_files:
                    seen_files.add(rx)
                    
                    tx = rx.replace("_rx.wav", "_tx.wav")
                    call_id = os.path.basename(rx).replace("_rx.wav", "")
                    
                    logger.info(f"New stereo call detected: {call_id}.wav. Spawning Gemini streams.")
                    
                    # Spawn both legs concurrently without blocking the watcher
                    asyncio.create_task(stream_channel_to_gemini(call_id, rx, "client"))
                    asyncio.create_task(stream_channel_to_gemini(call_id, tx, "operator"))
                    
        except Exception as e:
            logger.error(f"Watcher error: {e}")
            
        await asyncio.sleep(1)

async def ws_handler(websocket):
    clients.add(websocket)
    try:
        await websocket.wait_closed()
    finally:
        clients.discard(websocket)

async def main():
    logger.info("Starting Live Transcribe Daemon on ws://0.0.0.0:8889")
    
    server = await websockets.serve(ws_handler, "0.0.0.0", 8889)
    watcher = asyncio.create_task(watch_for_new_calls())
    
    await asyncio.gather(server.wait_closed(), watcher)

if __name__ == "__main__":
    asyncio.run(main())
