import threading, time, re, os, sys, socket, configparser
import pyaudio
import numpy as np
import tkinter as tk
from pynput import keyboard
from faster_whisper import WhisperModel

# pythonw（コンソール無し）で起動された場合、print/エラーの行き先が無いのでログファイルへ退避
if sys.stdout is None or sys.stderr is None:
    _logf = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scribe.log'),
                 'a', encoding='utf-8', buffering=1)
    sys.stdout = _logf
    sys.stderr = _logf
    print(f"\n===== scribe start {time.strftime('%Y-%m-%d %H:%M:%S')} =====")

# --- [多重起動の防止] ---
# 複数インスタンスが同時に動くと、各々が右Ctrlを検知して同時に入力し、
# 文字が交錯・二重化する（例:「進められない仕様」→「進め進らめれらな…」）。
# ローカルポートを1つ占有し、既に使われていれば後発は即終了する。
_lock_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    _lock_sock.bind(('127.0.0.1', 53411))
except OSError:
    print("[scribe] Another instance is already running. Exiting.")
    sys.exit(0)

# --- [設定の読み込み：任意] ---
# config.ini があれば [SETTINGS] から上書き。無くてもデフォルトで動く。
config = configparser.ConfigParser()
config_path = os.path.join(os.path.dirname(__file__), 'config.ini')
if os.path.exists(config_path):
    config.read(config_path, encoding='utf-8')

def cfg(key, default):
    try:
        val = config['SETTINGS'][key]
        return val if val is not None else default
    except (KeyError, configparser.Error):
        return default

MODEL_SIZE   = cfg('MODEL', 'large-v3-turbo')  # large-v3 / large-v3-turbo / medium ...
LANGUAGE     = (cfg('LANGUAGE', '') or '').strip() or None  # '' => 自動判定(日/英)
DEVICE       = cfg('DEVICE', 'cuda')
COMPUTE_TYPE = cfg('COMPUTE_TYPE', 'float16')

# --- [音声パラメータ] ---
RATE = 16000
CHANNELS = 1
CHUNK = 1024
FORMAT = pyaudio.paInt16
MIN_SECONDS = 0.3  # これより短い録音は誤認識（ハルシネーション）防止のため破棄

# --- [状態] ---
kb_controller = keyboard.Controller()
mic_active = False       # 録音バッファに追記中か（右Ctrl押下中）
is_busy = False          # 文字起こし処理中か（多重起動を防ぐ）
recording_frames = []    # 押下中に貯める生PCMチャンク

PTT_KEY = keyboard.Key.ctrl_r   # 押している間だけ聞くキー（右Ctrl）

def sanitize_text(text):
    allowed_pattern = re.compile(r'[^ -~぀-ゟ゠-ヿ一-鿿＀-￯]+')
    return allowed_pattern.sub('', text)

# --- [UI設定] ---
BG_COLOR = "#1A1A1A"; ACCENT_ACTIVE = "#00E676"; ACCENT_BUSY = "#FFB300"
root = tk.Tk()
root.title("SCRIBE")
root.geometry("220x100+100+100")
root.configure(bg=BG_COLOR)
root.attributes("-topmost", True)
root.overrideredirect(True)
root.withdraw()

label_main = tk.Label(root, text="LISTENING...", fg=ACCENT_ACTIVE, bg=BG_COLOR, font=("Arial", 14, "bold"))
label_main.pack(pady=(20, 0))
label_sub = tk.Label(root, text="hold R-Ctrl", fg=ACCENT_ACTIVE, bg=BG_COLOR, font=("Arial", 9), wraplength=200)
label_sub.pack()
root.config(highlightbackground=ACCENT_ACTIVE, highlightthickness=2)

def show_state(state):
    # state: 'rec' 録音中 / 'busy' 文字起こし中 / 'hide' 非表示
    if state == 'rec':
        label_main.config(text="LISTENING...", fg=ACCENT_ACTIVE)
        label_sub.config(text="release to type", fg=ACCENT_ACTIVE)
        root.config(highlightbackground=ACCENT_ACTIVE)
        root.deiconify(); root.attributes("-topmost", True)
    elif state == 'busy':
        label_main.config(text="TRANSCRIBING", fg=ACCENT_BUSY)
        label_sub.config(text="…", fg=ACCENT_BUSY)
        root.config(highlightbackground=ACCENT_BUSY)
        root.deiconify(); root.attributes("-topmost", True)
    else:
        root.withdraw()

# --- [Whisperモデルのロード（起動時に一度だけVRAMへ）] ---
print(f"[scribe] Loading Whisper model '{MODEL_SIZE}' on {DEVICE} ({COMPUTE_TYPE}) ...")
print("[scribe] 初回はモデルDL(約1.6GB)で時間がかかります。")
# 窓無し起動でも「読み込み中」が分かるよう、mainloop前にオーバーレイを手動描画
label_main.config(text="LOADING…", fg=ACCENT_BUSY)
label_sub.config(text="starting model", fg=ACCENT_BUSY)
root.config(highlightbackground=ACCENT_BUSY)
root.deiconify(); root.attributes("-topmost", True); root.update()
model = WhisperModel(MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE)
root.withdraw()
print("[scribe] Model ready. 右Ctrlを押している間だけ聞き取ります。(終了: Ctrl+Alt+Q)")

# --- [録音：右Ctrl押下中、常時マイクを読み必要時だけバッファへ] ---
_pa = pyaudio.PyAudio()
_stream = _pa.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNK)

def reader_loop():
    # 永続的にマイクを読み続け、mic_active の間だけバッファに貯める
    while True:
        try:
            data = _stream.read(CHUNK, exception_on_overflow=False)
        except Exception as e:
            print(f"[scribe] mic read error: {e}")
            time.sleep(0.05)
            continue
        if mic_active:
            recording_frames.append(data)

threading.Thread(target=reader_loop, daemon=True).start()

# --- [文字起こし → クリップボード貼り付けで一括入力] ---
def insert_text(text):
    # 1文字ずつのキー入力はIMEに再変換されて化ける（例:「進められない仕様」→乱れ）。
    # クリップボード経由の貼り付け(Ctrl+V)なら変換を通らず確実に入力できる。
    done = threading.Event()

    def do_paste():
        try:
            try:
                prev = root.clipboard_get()   # 既存クリップボードを退避
            except Exception:
                prev = None                   # 空 or 画像など非テキスト
            root.clipboard_clear()
            root.clipboard_append(text)
            root.update_idletasks()
            with kb_controller.pressed(keyboard.Key.ctrl):
                kb_controller.press('v'); kb_controller.release('v')

            def restore():
                if prev is not None:
                    try:
                        root.clipboard_clear(); root.clipboard_append(prev); root.update_idletasks()
                    except Exception:
                        pass
                done.set()
            root.after(250, restore)          # 貼り付け完了を待ってから元に戻す
        except Exception as e:
            print(f"[scribe] paste error: {e}")
            done.set()

    root.after(0, do_paste)   # クリップボード操作はTkのメインスレッドで実行
    done.wait(timeout=3)

def transcribe_and_type():
    global is_busy
    try:
        frames = b"".join(recording_frames)
        n_samples = len(frames) // 2  # int16 = 2byte
        if n_samples < int(MIN_SECONDS * RATE):
            return  # 短すぎる録音は破棄

        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0

        segments, info = model.transcribe(
            audio,
            language=LANGUAGE,             # None なら自動判定（日/英）
            vad_filter=True,               # 無音を除去してハルシネーション低減
            condition_on_previous_text=False,
            beam_size=5,
        )
        text = "".join(seg.text for seg in segments).strip()
        text = sanitize_text(text).strip()
        print(f"[scribe] ({getattr(info, 'language', '?')}) -> {text!r}")

        if text:
            insert_text(text)  # 右Ctrlは既に離れているので安全に貼り付けできる
    except Exception as e:
        print(f"[scribe] transcribe error: {e}")
    finally:
        is_busy = False
        root.after(0, lambda: show_state('hide'))

# --- [PTT：右Ctrlを押している間だけ聞く] ---
def start_recording():
    global mic_active, is_busy
    if mic_active or is_busy:
        return  # キーリピート／前回処理中は無視
    recording_frames.clear()
    mic_active = True
    root.after(0, lambda: show_state('rec'))

def stop_recording():
    global mic_active, is_busy
    if not mic_active:
        return
    mic_active = False
    is_busy = True
    root.after(0, lambda: show_state('busy'))
    threading.Thread(target=transcribe_and_type, daemon=True).start()

def on_press(key):
    if key == PTT_KEY:
        start_recording()

def on_release(key):
    if key == PTT_KEY:
        stop_recording()

def quit_app():
    print("[scribe] Quit hotkey (Ctrl+Alt+Q). Exiting.")
    try:
        root.after(0, root.destroy)
    except Exception:
        pass

listener = keyboard.Listener(on_press=on_press, on_release=on_release)
listener.start()
# 窓が無いので終了用のグローバルホットキー（Ctrl+Alt+Q）を用意
quit_listener = keyboard.GlobalHotKeys({'<ctrl>+<alt>+q': quit_app})
quit_listener.start()

root.mainloop()

# 後始末：確実にプロセスを終了させる
try:
    listener.stop(); quit_listener.stop()
    _stream.stop_stream(); _stream.close(); _pa.terminate()
except Exception:
    pass
os._exit(0)
