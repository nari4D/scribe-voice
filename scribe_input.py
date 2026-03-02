import threading
import pyaudio
import wave
from io import BytesIO
from elevenlabs.client import ElevenLabs
from pynput import keyboard

# --- Paste your key directly here ---
API_KEY = "sk_6ae3e08d4a03abb42a2b58149d26467e928139141223c869"  

client = ElevenLabs(api_key=API_KEY)
kb_controller = keyboard.Controller()

audio_frames = []
is_recording = False

def mic_callback(in_data, frame_count, time_info, status):
    if is_recording:
        audio_frames.append(in_data)
    return (None, pyaudio.paContinue)

p = pyaudio.PyAudio()
mic_stream = p.open(format=pyaudio.paInt16, channels=1, rate=16000, 
                    input=True, stream_callback=mic_callback)

def process_audio():
    global audio_frames
    if not audio_frames:
        return
        
    print("\n[TRANSCRIBING...]")
    
    audio_data = BytesIO()
    with wave.open(audio_data, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(p.get_sample_size(pyaudio.paInt16))
        wf.setframerate(16000)
        wf.writeframes(b''.join(audio_frames))
    
    audio_data.seek(0)
    audio_data.name = "dictation.wav"
    audio_frames = []
    
    try:
        transcription = client.speech_to_text.convert(
            file=audio_data,
            model_id="scribe_v2",
        )
        
        text = transcription.text.strip()
        if text:
            print(f"Typing: {text}")
            kb_controller.type(text + " ")
            
    except Exception as e:
        print(f"API Error: {e}")

def toggle_recording():
    global is_recording, audio_frames
    if not is_recording:
        # START
        print("\n[LISTENING... Press Ctrl + ` again to stop]")
        audio_frames = []
        is_recording = True
    else:
        # STOP
        is_recording = False
        print("[STOPPED - Processing]")
        threading.Thread(target=process_audio).start()

# This uses the GlobalHotkeys syntax for cleaner combinations
hotkey_map = {
    '<ctrl>+`': toggle_recording
}

print("==========================================")
print("  ELEVENLABS SCRIBE: ACTIVE")
print("  Hotkey: Ctrl + ` (Backtick)")
print("  Close CMD window to shut down.")
print("==========================================")

with keyboard.GlobalHotkeys(hotkey_map) as listener:
    listener.join()
