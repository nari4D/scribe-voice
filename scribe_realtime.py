import threading, pyaudio, json, base64, websockets, asyncio, time
from websockets.exceptions import ConnectionClosedOK
import tkinter as tk
import ctypes
import re
import configparser  # 追加：設定ファイルを読み込むための道具
import os            # 追加：ファイルの存在を確認するための道具
from pynput import keyboard

# --- [設定の読み込み] ---
config = configparser.ConfigParser()

# scribe_realtime.py と同じ場所にある config.ini を探す
config_path = os.path.join(os.path.dirname(__file__), 'config.ini')

if not os.path.exists(config_path):
    print("Error: config.ini が見つかりません。作成してください。")
    input("Enterキーで終了します...") # エラーで即座に閉じないように
    exit()

config.read(config_path, encoding='utf-8')

try:
    API_KEY = config['SETTINGS']['API_KEY']
except KeyError:
    print("Error: config.ini の中に API_KEY の設定が見つかりません。")
    exit()

MODEL_ID = "scribe_v2_realtime"
URI = f"wss://api.elevenlabs.io/v1/speech-to-text/realtime?model_id={MODEL_ID}&commit_strategy=vad&vad_silence_threshold_secs=0.3"


kb_controller = keyboard.Controller()
is_recording = False
was_hiragana = False 

def sanitize_text(text):
    allowed_pattern = re.compile(r'[^\u0020-\u007E\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\uFF00-\uFFEF]+')
    return allowed_pattern.sub('', text)

# --- [IME制御ロジック] ---
def get_ime_status():
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd: return 0
        imm32 = ctypes.windll.imm32
        handle = imm32.ImmGetDefaultIMEWnd(hwnd)
        return ctypes.windll.user32.SendMessageW(handle, 0x0283, 0x0005, 0)
    except Exception:
        return 0

def toggle_ime():
    with kb_controller.pressed(keyboard.Key.shift):
        kb_controller.press(keyboard.Key.caps_lock)
        kb_controller.release(keyboard.Key.caps_lock)

# --- [UI設定：位置を復旧] ---
BG_COLOR = "#1A1A1A"; ACCENT_ACTIVE = "#00E676"
root = tk.Tk()
root.title("SCRIBE")
root.geometry("220x100+100+100") # ← ここで位置(100, 100)を指定
root.configure(bg=BG_COLOR)
root.attributes("-topmost", True)
root.overrideredirect(True)
root.withdraw()

label_main = tk.Label(root, text="LISTENING...", fg=ACCENT_ACTIVE, bg=BG_COLOR, font=("Arial", 14, "bold"))
label_main.pack(pady=(25, 0))
label_sub = tk.Label(root, text="F9 to Stop", fg=ACCENT_ACTIVE, bg=BG_COLOR, font=("Arial", 9))
label_sub.pack()
root.config(highlightbackground=ACCENT_ACTIVE, highlightthickness=2)

def update_ui(active):
    if active:
        root.deiconify()
        root.attributes("-topmost", True)
    else:
        root.withdraw()

# --- [コアロジック：差分入力] ---
async def run_scribe():
    global is_recording
    headers = {"xi-api-key": API_KEY}
    
    try:
        async with websockets.connect(URI, additional_headers=headers) as ws:
            p = pyaudio.PyAudio()
            stream = p.open(format=pyaudio.paInt16, channels=1, rate=16000, input=True, frames_per_buffer=1024)
            
            async def receive_text():
                typed_len = 0
                while is_recording:
                    try:
                        resp = json.loads(await ws.recv())
                        m_type = resp.get("message_type")
                        raw_text = resp.get("text", "").strip()
                        if not raw_text: continue

                        clean_text = sanitize_text(raw_text)
                        
                        # 差分だけを打つ
                        stable_text = clean_text[:-1] if m_type == "partial_transcript" and len(clean_text) > 1 else clean_text

                        if len(stable_text) > typed_len:
                            new_chars = stable_text[typed_len:]
                            kb_controller.type(new_chars)
                            typed_len = len(stable_text)

                        if m_type == "committed_transcript":
                            final_delta = clean_text[typed_len:]
                            kb_controller.type(final_delta + " ")
                            typed_len = 0
                    except ConnectionClosedOK:
                        break
                    except Exception as e:
                        if is_recording:
                            print(f"Receive error: {e}")
                        break

            async def send_audio():
                while is_recording:
                    try:
                        chunk = stream.read(1024, exception_on_overflow=False)
                        await ws.send(json.dumps({"message_type": "input_audio_chunk", "audio_base_64": base64.b64encode(chunk).decode("utf-8")}))
                        await asyncio.sleep(0.01)
                    except ConnectionClosedOK:
                        break
                    except Exception as e:
                        if is_recording:
                            print(f"Send error: {e}")
                        break

            await asyncio.gather(send_audio(), receive_text())
            stream.stop_stream(); stream.close(); p.terminate()
    except Exception as e: print(f"Error: {e}")

def toggle():
    global is_recording, was_hiragana
    if not is_recording:
        was_hiragana = (get_ime_status() != 0)
        if was_hiragana: toggle_ime()
        is_recording = True
        root.after(0, lambda: update_ui(True))
        threading.Thread(target=lambda: asyncio.run(run_scribe()), daemon=True).start()
    else:
        is_recording = False
        root.after(0, lambda: update_ui(False))
        if was_hiragana: threading.Timer(0.3, toggle_ime).start()

listener = keyboard.GlobalHotKeys({'<f9>': toggle}).start()
root.mainloop()
