import sys, os, io, wave, math, threading, ctypes, re, time, faulthandler
from pathlib import Path
from datetime import datetime, timedelta

faulthandler.enable(open(
    Path(os.environ.get("TEMP","C:/temp")) / "voice-input-crash.txt", "w"
))

# ── Single instance — kill any previous GoVoice process ───────────────────
_PID_FILE = Path(os.environ.get("TEMP", "C:/temp")) / "voice-input-gui.pid"

def _enforce_single_instance():
    if _PID_FILE.exists():
        try:
            old_pid = int(_PID_FILE.read_text().strip())
            h = ctypes.windll.kernel32.OpenProcess(1, False, old_pid)  # PROCESS_TERMINATE=1
            if h:
                ctypes.windll.kernel32.TerminateProcess(h, 0)
                ctypes.windll.kernel32.CloseHandle(h)
        except Exception:
            pass
    _PID_FILE.write_text(str(os.getpid()))

_enforce_single_instance()

try:
    import numpy as np
    import sounddevice as sd
    import httpx
except ImportError as _e:
    import tkinter as _tk, tkinter.messagebox as _mb
    _tk.Tk().withdraw()
    _mb.showerror("GoVoice", f"Missing dependency:\n{_e}\n\nRun: pip install sounddevice numpy httpx")
    sys.exit(1)

from PyQt6.QtWidgets import (
    QApplication, QSystemTrayIcon, QMenu, QDialog, QVBoxLayout,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget,
    QTableWidget, QTableWidgetItem, QComboBox,
    QHeaderView, QAbstractItemView, QScrollArea, QFrame
)
from PyQt6.QtCore import Qt, QTimer, QPoint, QRect, pyqtSignal, QAbstractNativeEventFilter
from PyQt6.QtGui import QPainter, QPixmap, QColor, QPen, QAction, QFont, QIcon

# ── RegisterHotKey — works in terminals and all windows ───────────────────
_u32          = ctypes.windll.user32
_MOD_CTRL     = 0x0002
_MOD_ALT      = 0x0001
_MOD_NOREPEAT = 0x4000
_VK_R         = 0x52
_VK_ESC       = 0x1B
_WM_HOTKEY    = 0x0312
_HK_RECORD    = 1
_HK_CANCEL    = 2

# Fix 64-bit pointer handling for hotkey functions
_u32.RegisterHotKey.argtypes   = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
_u32.RegisterHotKey.restype    = ctypes.c_bool
_u32.UnregisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int]
_u32.UnregisterHotKey.restype  = ctypes.c_bool

import ctypes.wintypes as _wt

class _MSG(ctypes.Structure):
    _fields_ = [("hwnd",   _wt.HWND),  ("message", _wt.UINT),
                ("wParam", _wt.WPARAM),("lParam",  _wt.LPARAM),
                ("time",   _wt.DWORD), ("ptx",     _wt.LONG),
                                       ("pty",     _wt.LONG)]

class HotkeyFilter(QAbstractNativeEventFilter):
    """Native event filter — no window needed, works in terminals."""
    def __init__(self, on_record, on_cancel):
        super().__init__()
        self._on_record = on_record
        self._on_cancel = on_cancel
        _u32.UnregisterHotKey(None, _HK_RECORD)
        _u32.UnregisterHotKey(None, _HK_CANCEL)
        ok1 = _u32.RegisterHotKey(None, _HK_RECORD, _MOD_CTRL | _MOD_ALT | _MOD_NOREPEAT, _VK_R)
        ok2 = _u32.RegisterHotKey(None, _HK_CANCEL, _MOD_CTRL | _MOD_ALT | _MOD_NOREPEAT, _VK_ESC)
        log(f"RegisterHotKey Ctrl+Alt+R={ok1}  Ctrl+Alt+Esc={ok2}")

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            try:
                msg = _MSG.from_address(int(message))
                if msg.message == _WM_HOTKEY:
                    if msg.wParam == _HK_RECORD:
                        self._on_record()
                    elif msg.wParam == _HK_CANCEL:
                        self._on_cancel()
                    return True, 0
            except Exception:
                pass
        return False, 0

    def unregister(self):
        _u32.UnregisterHotKey(None, _HK_RECORD)
        _u32.UnregisterHotKey(None, _HK_CANCEL)

# ── Paths ──────────────────────────────────────────────────────────────────
APP_DIR = Path(os.environ.get("APPDATA", "")) / "voice-input"
APP_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE      = APP_DIR / "config"
CORRECTIONS_FILE = APP_DIR / "corrections.txt"
TEMPLATES_FILE   = APP_DIR / "templates.txt"
NOTES_DIR        = Path.home() / "Documents" / "voice-notes"

LOG_FILE = Path(os.environ.get("TEMP", "C:/temp")) / "voice-input.log"

def log(msg):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass

SAMPLERATE        = 48000   # WASAPI universally supports 48kHz; Whisper accepts any rate
SILENCE_PEAK      = 150
MIN_WORDS_CLEANUP = 5
LONG_MAX_SECONDS  = 600

# ── Config I/O ─────────────────────────────────────────────────────────────
DEFAULT_TEMPLATES = [
    ("General dictation",  ""),
    ("Software developer", "Australian software developer. Go, Python, TypeScript. VS Code. Precise technical language."),
    ("Meeting notes",      "Business meeting. Capture action items, decisions, and key points. Bullet format."),
    ("Email",              "Professional email. Proper salutation, body, sign-off. Polite and concise."),
    ("Code docs",          "Technical documentation. Precise language. Wrap code terms in backticks."),
    ("Bullet points",      "Format as clean bullet points starting with -. Be concise."),
    ("Summarise",          "Summarise key points. Remove filler words and false starts. Essential info only."),
    ("AI prompt",          "AI prompt or instruction. Capture exactly as spoken including technical terms."),
]

def load_config():
    cfg = {"TRANSCRIPTION_PROVIDER": "openai", "OPENAI_API_KEY": "",
           "DEEPGRAM_API_KEY": "", "DEEPSEEK_API_KEY": "", "CONTEXT": "",
           "LANGUAGE": "", "AUDIO_DEVICE": "", "PASTE_MODE": "auto"}
    if CONFIG_FILE.exists():
        for line in CONFIG_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    return cfg

def save_config(cfg):
    CONFIG_FILE.write_text("\r\n".join(f"{k}={v}" for k, v in cfg.items()), encoding="utf-8")

def load_corrections():
    if not CORRECTIONS_FILE.exists():
        return []
    result = []
    for line in CORRECTIONS_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if "->" in line and not line.startswith("#"):
            parts = line.split("->", 1)
            result.append((parts[0].strip(), parts[1].strip()))
    return result

def save_corrections(corr):
    lines = ["# Corrections — wrong -> correct  (case-insensitive)"]
    lines += [f"{w} -> {r}" for w, r in corr]
    CORRECTIONS_FILE.write_text("\r\n".join(lines), encoding="utf-8")

def load_templates():
    if TEMPLATES_FILE.exists():
        result = []
        for line in TEMPLATES_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "->" in line:
                parts = line.split("->", 1)
                result.append((parts[0].strip(), parts[1].strip()))
        return result if result else list(DEFAULT_TEMPLATES)
    return list(DEFAULT_TEMPLATES)

def save_templates(templates):
    lines = ["# Context templates — name -> context text"]
    lines += [f"{name} -> {ctx}" for name, ctx in templates]
    TEMPLATES_FILE.write_text("\r\n".join(lines), encoding="utf-8")

def apply_corrections(text, corrections):
    for wrong, right in corrections:
        text = re.sub(re.escape(wrong), right, text, flags=re.IGNORECASE)
    return text

def build_whisper_prompt(corrections):
    # Only send corrections to Whisper — context goes to DeepSeek only.
    # Sending full context to Whisper causes it to echo the prompt text.
    if not corrections:
        return ""
    return ", ".join(r for _, r in corrections[:20])

# ── Recorder ───────────────────────────────────────────────────────────────
class Recorder:
    """Records via WASAPI in a background thread using blocking stream.read()."""
    _CHUNK = 512   # frames per read (~32 ms at 16 kHz)

    def __init__(self):
        self._chunks  = []
        self._running = False
        self._thread  = None
        self._lock    = threading.Lock()
        self.level    = 0.0   # current RMS level 0.0–1.0, updated in real-time
        self.sample_rate = SAMPLERATE   # actual rate of the last-opened stream
        self._wasapi  = self._find_wasapi_api()

    @staticmethod
    def _find_wasapi_api():
        for i, api in enumerate(sd.query_hostapis()):
            if "WASAPI" in api["name"]:
                return i
        return None

    def list_input_devices(self):
        """Return input device names available on WASAPI (falls back to all)."""
        seen, result = set(), []
        devices = sd.query_devices()
        # WASAPI devices first
        for d in devices:
            if d["max_input_channels"] > 0 and d["hostapi"] == self._wasapi and d["name"] not in seen:
                seen.add(d["name"])
                result.append(d["name"])
        # then anything else
        for d in devices:
            if d["max_input_channels"] > 0 and d["name"] not in seen:
                seen.add(d["name"])
                result.append(d["name"])
        return result

    def _resolve_device(self, name=None):
        """Return device index for a named device, or None to use the OS default."""
        if not name:
            return None  # let sounddevice use the OS default — avoids stale fixed indices
        devices = sd.query_devices()
        for i, d in enumerate(devices):
            if d["name"] == name and d["max_input_channels"] > 0 and d["hostapi"] == self._wasapi:
                return i
        for i, d in enumerate(devices):
            if d["name"] == name and d["max_input_channels"] > 0:
                return i
        return None

    def start(self, device_name=None):
        if self._thread and self._thread.is_alive():
            self._running = False
            self._thread.join(timeout=1.0)
        self._chunks  = []
        self._running = True
        device = self._resolve_device(device_name)
        api_name = sd.query_devices(device)["hostapi"] if device is not None else "?"
        log(f"opening audio stream  device={device}  wasapi_api={self._wasapi}")
        self._thread = threading.Thread(target=self._run, args=(device,), daemon=True)
        self._thread.start()

    def _wasapi_default_input(self):
        """Return the WASAPI default input device index, or None."""
        try:
            if self._wasapi is not None:
                idx = sd.query_hostapis(self._wasapi)["default_input_device"]
                if idx >= 0:
                    return idx
        except Exception:
            pass
        return None

    def _run(self, device):
        # Try specified device, then WASAPI default, then OS default
        wasapi_dev = self._wasapi_default_input()
        candidates = [device, wasapi_dev, None]
        # deduplicate while preserving order
        seen, devs = set(), []
        for d in candidates:
            k = str(d)
            if k not in seen:
                seen.add(k)
                devs.append(d)

        for attempt, dev in enumerate(devs):
            if attempt > 0:
                log(f"retrying  dev={dev}  attempt={attempt+1}")
                time.sleep(0.25)   # let Windows Audio Session settle between attempts
            latency = 0.1 if attempt == 0 else 0.3   # higher latency on retry
            # Open at the device's native rate and channel count — asking WASAPI
            # to convert (48kHz mono) gives some devices stereo-interleaved-as-mono
            # data (half-speed audio) or AUDCLNT_E_DEVICE_INVALIDATED.
            try:
                info = sd.query_devices(dev) if dev is not None else sd.query_devices(kind="input")
                rate     = int(info.get("default_samplerate") or SAMPLERATE)
                channels = max(1, min(2, int(info.get("max_input_channels") or 1)))
            except Exception:
                rate, channels = SAMPLERATE, 1
            try:
                with sd.InputStream(device=dev, samplerate=rate,
                                     channels=channels, dtype="int16",
                                     blocksize=self._CHUNK, latency=latency) as stream:
                    self.sample_rate = rate
                    log(f"audio stream open  device={dev}  rate={rate}  channels={channels}")
                    while self._running:
                        data, _ = stream.read(self._CHUNK)
                        # First channel only — on echo-cancelling devices the
                        # second channel can be a far-end reference signal.
                        flat = np.ascontiguousarray(data[:, 0])
                        with self._lock:
                            self._chunks.append(flat.copy())
                        # Update live level (exponential smoothing, no lock needed for float)
                        rms = float(np.sqrt(np.mean(flat.astype(np.float32) ** 2)))
                        self.level = 0.3 * min(1.0, rms / 2500.0) + 0.7 * self.level
                    log("audio stream closed")
                    return  # success
            except Exception as e:
                log(f"recorder error (attempt {attempt+1}): {e}")
                if not self._running:
                    return  # cancelled, don't retry

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        with self._lock:
            chunks, self._chunks = self._chunks[:], []
        if not chunks:
            return None
        return np.concatenate(chunks)

    def cancel(self):
        self._running = False
        self.level = 0.0
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        with self._lock:
            self._chunks = []

# ── Audio / API / Output ───────────────────────────────────────────────────
_LAST_WAV = Path(os.environ.get("TEMP", "C:/temp")) / "voice-input-last.wav"

_WHISPER_RATE = 16000

def audio_to_wav(audio_int16, rate=SAMPLERATE):
    # Save full-rate copy for playback
    try:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(rate)
            wf.writeframes(audio_int16.tobytes())
        _LAST_WAV.write_bytes(buf.getvalue())
    except Exception:
        pass

    # Downsample to 16kHz for transcription (smaller upload)
    old_len = len(audio_int16)
    new_len = int(old_len * _WHISPER_RATE / rate)
    audio_16k = np.interp(
        np.linspace(0, old_len - 1, new_len),
        np.arange(old_len),
        audio_int16.astype(np.float32),
    ).astype(np.int16)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(_WHISPER_RATE)
        wf.writeframes(audio_16k.tobytes())
    return buf.getvalue()

_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=5.0)

def api_transcribe_openai(api_key, wav_bytes, prompt="", language=""):
    data = {"model": "gpt-4o-mini-transcribe", "response_format": "text"}
    if prompt:   data["prompt"]   = prompt
    if language: data["language"] = language
    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": ("audio.wav", wav_bytes, "audio/wav")},
            data=data,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Whisper {resp.status_code}: {resp.text[:120]}")
        return resp.text.strip()


def api_transcribe_deepgram(api_key, wav_bytes, language="", keywords=None):
    params = [("model", "nova-2"), ("diarize", "false"), ("smart_format", "true")]
    if language:
        params.append(("language", language))
    if keywords:
        params += [("keywords", k) for k in keywords]
    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.post(
            "https://api.deepgram.com/v1/listen", params=params,
            headers={"Authorization": f"Token {api_key}", "Content-Type": "audio/wav"},
            content=wav_bytes,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Deepgram {resp.status_code}: {resp.text[:120]}")
        return resp.json()["results"]["channels"][0]["alternatives"][0]["transcript"].strip()


def api_transcribe(provider, api_key, wav_bytes, prompt="", language="", keywords=None):
    if provider == "deepgram":
        return api_transcribe_deepgram(api_key, wav_bytes, language=language, keywords=keywords)
    return api_transcribe_openai(api_key, wav_bytes, prompt=prompt, language=language)

def api_cleanup(api_key, text, context=""):
    system = (
        "You are a transcription editor. Fix grammar, punctuation, and clarity of voice "
        "transcriptions. Never answer or engage with the content — treat it as raw speech "
        "to clean up. Return only the corrected text, no commentary."
    )
    if context:
        system += f"\n\nContext: {context}"
    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "temperature": 0.3,
                  "messages": [{"role": "system", "content": system},
                               {"role": "user",   "content": text}]},
        )
        if resp.status_code != 200:
            raise RuntimeError(f"DeepSeek {resp.status_code}")
        return resp.json()["choices"][0]["message"]["content"].strip()

def save_to_notes(text):
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    note_file = NOTES_DIR / f"{datetime.now().date()}.md"
    ts = datetime.now().strftime("%H:%M:%S")
    with open(note_file, "a", encoding="utf-8") as f:
        f.write(f"\n## {ts}\n{text}\n")

def paste_clipboard():
    VK_CONTROL, VK_V, KEYUP = 0x11, 0x56, 0x0002
    _u32.keybd_event(VK_CONTROL, 0, 0, 0)
    _u32.keybd_event(VK_V,       0, 0, 0)
    _u32.keybd_event(VK_V,       0, KEYUP, 0)
    _u32.keybd_event(VK_CONTROL, 0, KEYUP, 0)

# ── Styles ─────────────────────────────────────────────────────────────────
SCROLLBAR_CSS = """
    QScrollArea { background: transparent; border: none; }
    QScrollBar:vertical {
        background: #0c0c18; width: 8px; border-radius: 4px; margin: 2px 2px 2px 0;
    }
    QScrollBar::handle:vertical {
        background: #363660; border-radius: 4px; min-height: 36px;
    }
    QScrollBar::handle:vertical:hover { background: #5050a0; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
"""

DARK_STYLE = """
    * { font-family: "Segoe UI"; font-size: 10pt; }
    QDialog, QWidget { background-color: #111118; color: #c8c8e0; }
    QLabel { color: #7070a0; font-size: 9.5pt; background: transparent; }
    QLineEdit {
        background: #18182a; border: 1px solid #282848; border-radius: 6px;
        padding: 7px 11px; color: #d8d8f0; selection-background-color: #03DAC6;
    }
    QLineEdit:focus { border-color: #03DAC6; background: #1c1c30; }
    QTableWidget {
        background: #14141e; border: 1px solid #202038; border-radius: 6px;
        gridline-color: #1c1c2e; color: #c0c0de; font-size: 9.5pt;
    }
    QTableWidget::item { padding: 4px 10px; }
    QTableWidget::item:selected { background: #1a3838; color: #03DAC6; }
    QTableWidget::item:focus { border: none; outline: none; }
    QTableCornerButton::section { background: #14141e; border: none; }
    QHeaderView::section {
        background: #14141e; color: #48487a; border: none;
        border-bottom: 1px solid #202038; padding: 5px 10px;
        font-size: 8pt; font-weight: 600; letter-spacing: 0.5px;
    }
    QPushButton {
        background: #03DAC6; color: #06060e; border: none; border-radius: 6px;
        padding: 8px 20px; font-weight: 600; min-width: 64px;
    }
    QPushButton:hover  { background: #04EED8; }
    QPushButton:pressed { background: #02BBA9; }
    QPushButton#secondary {
        background: #1c1c2e; color: #7070a8; font-weight: normal; min-width: 0;
    }
    QPushButton#secondary:hover { background: #242438; color: #9090c8; }
    QPushButton#ghost {
        background: transparent; color: #42426e;
        border: 1px solid #20203a; border-radius: 5px;
        padding: 4px 12px; min-width: 0; font-size: 9pt; font-weight: normal;
    }
    QPushButton#ghost:hover { background: #16162a; color: #8080b8; border-color: #343460; }
    QComboBox {
        background: #18182a; border: 1px solid #282848; border-radius: 6px;
        padding: 7px 11px; color: #d8d8f0;
    }
    QComboBox::drop-down { border: none; width: 20px; }
    QComboBox QAbstractItemView {
        background: #18182a; border: 1px solid #282848;
        color: #d8d8f0; selection-background-color: #03DAC6; selection-color: #06060e;
    }
    QMenu {
        background: #14141e; border: 1px solid #222238; border-radius: 8px;
        padding: 4px; color: #c0c0de;
    }
    QMenu::item { padding: 8px 28px 8px 14px; border-radius: 5px; }
    QMenu::item:selected { background: #1c1c30; }
    QMenu::item:disabled { color: #30304a; }
    QMenu::separator { height: 1px; background: #1c1c2c; margin: 3px 8px; }
"""

# ── Icons ──────────────────────────────────────────────────────────────────
def _wave_px(size, color, heights=None):
    px = QPixmap(size, size)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    heights = heights or [0.30, 0.55, 0.85, 0.55, 0.30]
    n = len(heights)
    bw  = max(1, size // 10)
    gap = max(1, size // 14)
    sx  = (size - (n * bw + (n - 1) * gap)) // 2
    for i, frac in enumerate(heights):
        h = max(2, int(frac * size))
        p.drawRoundedRect(sx + i * (bw + gap), (size - h) // 2, bw, h, bw // 2, bw // 2)
    p.end()
    return px

def make_logo_icon():
    return _wave_px(16, QColor(3, 218, 198))

def make_tray_icon(recording=False):
    px = QPixmap(32, 32)
    px.fill(Qt.GlobalColor.transparent)
    p  = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(10, 10, 20))
    p.drawRoundedRect(0, 0, 32, 32, 6, 6)
    if recording:
        p.setBrush(QColor(160, 20, 20, 60))
        p.drawRoundedRect(0, 0, 32, 32, 6, 6)
    bar = QColor(235, 65, 65) if recording else QColor(3, 218, 198)
    p.setBrush(bar)
    fracs = [0.30, 0.55, 0.85, 0.55, 0.30]
    bw, gap = 3, 2
    sx = (32 - (len(fracs) * bw + (len(fracs) - 1) * gap)) // 2
    for i, frac in enumerate(fracs):
        h = max(3, int(frac * 24))
        p.drawRoundedRect(sx + i * (bw + gap), (32 - h) // 2, bw, h, 1, 1)
    p.end()
    return QIcon(px)

# ── FloatingIcon ───────────────────────────────────────────────────────────
class FloatingIcon(QWidget):
    IDLE, RECORDING, TRANSCRIBING, DONE, ERROR = "idle", "recording", "transcribing", "done", "error"
    W, H = 52, 52

    def __init__(self, tray_app=None):
        super().__init__(None)
        self._app = tray_app
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool |
            Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedSize(self.W, self.H)
        self.state  = self.IDLE
        self.frame  = 0
        self._drag  = None
        timer = QTimer(self)
        timer.timeout.connect(self._tick)
        timer.start(50)
        self._snap_to_corner()
        self.show()

    def _snap_to_corner(self):
        geo = QApplication.primaryScreen().availableGeometry()
        self.move(geo.right() - self.W - 14, geo.bottom() - self.H - 14)

    def set_state(self, state):
        self.state = state
        self.update()

    def _tick(self):
        self.frame += 1
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _):
        self._drag = None

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._app:
            if self._app.recording: self._app._cmd_stop()
            else:                   self._app._cmd_start()

    def contextMenuEvent(self, e):
        if self._app:
            self._app.menu.exec(e.globalPos())

    def paintEvent(self, _):
        try:    self._paint()
        except: pass

    def _paint(self):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy, r = self.W // 2, self.H // 2, (min(self.W, self.H) - 4) // 2

        if self.state == self.IDLE:
            a = 0.5 + 0.5 * math.sin(self.frame * 0.025)
            p.setPen(QPen(QColor(3, 218, 198, int(20 + 10 * a)), 1))
            p.setBrush(QColor(8, 8, 20, 185))
            p.drawEllipse(2, 2, self.W - 4, self.H - 4)
            self._draw_bars(p, QColor(3, 218, 198, int(55 + 30 * a)),
                            [0.20, 0.38, 0.58, 0.38, 0.20], animated=False)

        elif self.state == self.RECORDING:
            a = 0.5 + 0.5 * math.sin(self.frame * 0.13)
            p.setPen(QPen(QColor(235, 65, 65, int(70 + 70 * a)), 1.5))
            p.setBrush(QColor(20, 5, 5, 225))
            p.drawEllipse(2, 2, self.W - 4, self.H - 4)
            self._draw_bars(p, QColor(235, 65, 65), None, animated=True)

        elif self.state == self.TRANSCRIBING:
            p.setPen(QPen(QColor(3, 218, 198, 35), 1))
            p.setBrush(QColor(8, 8, 20, 210))
            p.drawEllipse(2, 2, self.W - 4, self.H - 4)
            self._draw_dots(p)

        elif self.state == self.DONE:
            p.setPen(QPen(QColor(3, 218, 198, 55), 1))
            p.setBrush(QColor(4, 18, 16, 215))
            p.drawEllipse(2, 2, self.W - 4, self.H - 4)
            # Drawn checkmark
            pen = QPen(QColor(3, 218, 198), 2.5, Qt.PenStyle.SolidLine,
                       Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            cx, cy = self.W // 2, self.H // 2
            p.drawLine(cx - 9, cy, cx - 2, cy + 7)
            p.drawLine(cx - 2, cy + 7, cx + 9, cy - 7)

        elif self.state == self.ERROR:
            pulse = 0.5 + 0.5 * math.sin(self.frame * 0.10)
            # Outer glow ring
            p.setPen(QPen(QColor(210, 50, 50, int(30 + 50 * pulse)), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(1, 1, self.W - 2, self.H - 2)
            # Fill
            p.setPen(QPen(QColor(210, 50, 50, int(50 + 30 * pulse)), 1))
            p.setBrush(QColor(24, 4, 4, 220))
            p.drawEllipse(3, 3, self.W - 6, self.H - 6)
            # Drawn X (no emoji)
            m = 16
            pen = QPen(QColor(220, 75, 75), 2.2, Qt.PenStyle.SolidLine,
                       Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.drawLine(m, m, self.W - m, self.H - m)
            p.drawLine(self.W - m, m, m, self.H - m)

    def _draw_bars(self, p, color, heights, animated):
        n, bw, gap = 5, 3, 2
        sx = self.W // 2 - (n * bw + (n - 1) * gap) // 2
        cy = self.H // 2
        p.setPen(Qt.PenStyle.NoPen)
        # Pull live mic level from recorder when recording
        mic = 0.0
        if animated and self._app:
            rec = getattr(self._app, '_recorder', None)
            if rec: mic = rec.level
        for i in range(n):
            if animated:
                ph = self.frame * 0.08 + i * 0.7
                # Each bar: mic level drives height, gentle wave adds texture
                wave  = 0.5 + 0.5 * math.sin(ph)
                frac  = max(0.08, mic * (0.6 + 0.4 * wave) + 0.08 * wave)
                al    = 140 + int(100 * min(1.0, mic + 0.2 * wave))
                c     = QColor(color.red(), color.green(), color.blue(), al)
            else:
                frac = heights[i]
                c    = color
            h = max(3, int(frac * 22))
            p.setBrush(c)
            p.drawRoundedRect(sx + i * (bw + gap), cy - h // 2, bw, h, 1, 1)

    def _draw_dots(self, p):
        cx, cy = self.W // 2, self.H // 2
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(3):
            ph = self.frame * 0.12 + i * 1.1
            al = 70 + int(170 * (0.5 + 0.5 * math.sin(ph)))
            r  = 3 + int(round(1.5 * (0.5 + 0.5 * math.sin(ph))))
            p.setBrush(QColor(3, 218, 198, al))
            p.drawEllipse(cx - 16 + i * 16 - r, cy - r, r * 2, r * 2)

# ── DarkDialog ─────────────────────────────────────────────────────────────
class DarkDialog(QDialog):
    def __init__(self, title="GoVoice", parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self._dragging = False
        self._drag_pos = QPoint()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Title bar
        tbar = QWidget()
        tbar.setFixedHeight(44)
        tbar.setStyleSheet("background:#0a0a16; border-bottom:1px solid #1a1a2c;")
        tbar.mousePressEvent   = self._tp
        tbar.mouseMoveEvent    = self._tm
        tbar.mouseReleaseEvent = lambda _: setattr(self, "_dragging", False)

        tb = QHBoxLayout(tbar)
        tb.setContentsMargins(14, 0, 8, 0)
        tb.setSpacing(6)

        logo = QLabel()
        logo.setPixmap(make_logo_icon())
        tb.addWidget(logo)

        brand = QLabel("GoVoice")
        brand.setStyleSheet("color:#03DAC6; font-size:10.5pt; font-weight:700; background:transparent;")
        tb.addWidget(brand)

        sep_dot = QLabel("·")
        sep_dot.setStyleSheet("color:#242440; font-size:13pt; background:transparent;")
        tb.addWidget(sep_dot)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("color:#484870; font-size:9pt; background:transparent;")
        tb.addWidget(title_lbl)
        tb.addStretch()

        x = QPushButton("✕")
        x.setFixedSize(28, 28)
        x.setStyleSheet("""
            QPushButton { background:transparent; color:#383860; border:none;
                          font-size:11pt; padding:0; min-width:0; }
            QPushButton:hover { background:#a82020; color:#fff; border-radius:5px; }
        """)
        x.clicked.connect(self.reject)
        tb.addWidget(x)

        outer.addWidget(tbar)

        self._content = QWidget()
        self._content.setStyleSheet("background:#111118;")
        outer.addWidget(self._content, 1)

    def _tp(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def _tm(self, e):
        if self._dragging and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)

# ── SettingsDialog ─────────────────────────────────────────────────────────
class SettingsDialog(DarkDialog):
    def __init__(self, parent=None):
        super().__init__("Settings", parent)
        self.resize(540, 660)
        self.setMinimumSize(480, 480)

        outer = QVBoxLayout(self._content)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(SCROLLBAR_CSS)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        body = QWidget()
        body.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(5)

        cfg = load_config()

        # API Keys
        lay.addWidget(self._section("API Keys"))

        lay.addWidget(self._label("Transcription provider"))
        self.provider = QComboBox()
        self.provider.addItem("OpenAI Whisper", "openai")
        self.provider.addItem("Deepgram", "deepgram")
        current_provider = cfg.get("TRANSCRIPTION_PROVIDER", "openai").lower().strip()
        for i in range(self.provider.count()):
            if self.provider.itemData(i) == current_provider:
                self.provider.setCurrentIndex(i)
                break
        lay.addWidget(self.provider)
        lay.addSpacing(6)

        lay.addWidget(self._label("OpenAI API Key"))
        self.openai = self._field(cfg.get("OPENAI_API_KEY", ""), "sk-…", password=True)
        lay.addWidget(self.openai)
        lay.addSpacing(6)

        lay.addWidget(self._label("Deepgram API Key"))
        lay.addWidget(self._hint("Required when provider is Deepgram"))
        self.deepgram = self._field(cfg.get("DEEPGRAM_API_KEY", ""), "", password=True)
        lay.addWidget(self.deepgram)
        lay.addSpacing(6)

        lay.addWidget(self._label("DeepSeek API Key"))
        lay.addWidget(self._hint("Optional — enables AI grammar and punctuation cleanup"))
        self.deepseek = self._field(cfg.get("DEEPSEEK_API_KEY", ""), "sk-…", password=True)
        lay.addWidget(self.deepseek)

        # Transcription
        lay.addSpacing(12)
        lay.addWidget(self._section("Transcription"))
        lay.addWidget(self._label("Language hint"))
        self.language = self._field(cfg.get("LANGUAGE", ""), "en · fr · de · ja  (blank = auto-detect)")
        lay.addWidget(self.language)
        lay.addSpacing(6)

        lay.addWidget(self._label("Microphone"))
        self.mic_combo = QComboBox()
        self.mic_combo.addItem("Default (Windows setting)", "")
        current_dev = cfg.get("AUDIO_DEVICE", "")
        try:
            for name in Recorder().list_input_devices():
                self.mic_combo.addItem(name, name)
        except Exception:
            pass
        for i in range(self.mic_combo.count()):
            if self.mic_combo.itemData(i) == current_dev:
                self.mic_combo.setCurrentIndex(i)
                break
        lay.addWidget(self.mic_combo)

        # AI Context
        lay.addSpacing(12)
        lay.addWidget(self._section("AI Context"))
        lay.addWidget(self._hint("Guides DeepSeek — describe your role, domain, or output style"))
        self.context = self._field(cfg.get("CONTEXT", ""), "e.g. Australian software developer using Go and Python…")
        lay.addWidget(self.context)

        # Context Templates
        lay.addSpacing(12)
        lay.addWidget(self._section("Context Templates"))
        lay.addWidget(self._hint("Double-click a row to load its context above"))
        self.tmpl_table = self._table(["Name", "Context / AI instruction"], col_widths=[140])
        self.tmpl_table.setMinimumHeight(130)
        self.tmpl_table.setMaximumHeight(200)
        self.tmpl_table.cellDoubleClicked.connect(self._apply_template)
        for name, ctx in load_templates():
            self._add_row(self.tmpl_table, name, ctx)
        lay.addWidget(self.tmpl_table)
        lay.addLayout(self._table_btns(self.tmpl_table))

        # Corrections
        lay.addSpacing(12)
        lay.addWidget(self._section("Corrections"))
        lay.addWidget(self._hint("Fix recurring mishears — applied after transcription"))
        self.corr_table = self._table(["Whisper says", "Replace with"])
        self.corr_table.setMinimumHeight(90)
        self.corr_table.setMaximumHeight(170)
        for w, r in load_corrections():
            self._add_row(self.corr_table, w, r)
        lay.addWidget(self.corr_table)
        lay.addLayout(self._table_btns(self.corr_table))

        lay.addStretch()
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        # Footer
        foot = QWidget()
        foot.setFixedHeight(58)
        foot.setStyleSheet("background:#0a0a16; border-top:1px solid #181828;")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(22, 0, 22, 0)
        fl.addStretch()
        btn = QPushButton("Save")
        btn.setDefault(True)
        btn.setFixedWidth(88)
        btn.clicked.connect(self._save)
        fl.addWidget(btn)
        outer.addWidget(foot)

    def reject(self):
        self._do_save()
        super().reject()

    # helpers
    def _section(self, text):
        l = QLabel(text.upper())
        l.setStyleSheet("color:#03DAC6; font-size:7pt; font-weight:700; letter-spacing:1.5px; padding:8px 0 3px 0; background:transparent;")
        return l

    def _label(self, text):
        l = QLabel(text)
        l.setStyleSheet("color:#8888b0; font-size:9.5pt; padding:1px 0; background:transparent;")
        return l

    def _hint(self, text):
        l = QLabel(text)
        l.setWordWrap(True)
        l.setStyleSheet("color:#38385a; font-size:9pt; padding:1px 0 3px 0; background:transparent;")
        return l

    def _field(self, value, placeholder="", password=False):
        f = QLineEdit(value)
        f.setPlaceholderText(placeholder)
        if password:
            f.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
        return f

    def _table(self, headers, col_widths=None):
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().setVisible(False)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        hh = t.horizontalHeader()
        if col_widths:
            for i, w in enumerate(col_widths):
                hh.setSectionResizeMode(i, QHeaderView.ResizeMode.Fixed)
                t.setColumnWidth(i, w)
            for i in range(len(col_widths), len(headers)):
                hh.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
        else:
            for i in range(len(headers)):
                hh.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
        return t

    def _table_btns(self, table):
        row = QHBoxLayout()
        row.setSpacing(6)
        for label, fn in [("+ Add", lambda t=table: self._add_blank(t)),
                          ("Remove", lambda t=table: self._del_row(t))]:
            b = QPushButton(label)
            b.setObjectName("ghost")
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        return row

    def _add_row(self, table, a, b):
        r = table.rowCount()
        table.insertRow(r)
        table.setItem(r, 0, QTableWidgetItem(a))
        table.setItem(r, 1, QTableWidgetItem(b))
        return r

    def _add_blank(self, table):
        r = self._add_row(table, "", "")
        table.scrollToItem(table.item(r, 0))
        table.setCurrentCell(r, 0)
        table.editItem(table.item(r, 0))

    def _del_row(self, table):
        r = table.currentRow()
        if r >= 0:
            table.removeRow(r)

    def _apply_template(self, row, _col):
        item = self.tmpl_table.item(row, 1)
        if item:
            self.context.setText(item.text())
            self.context.setFocus()

    def _do_save(self):
        cfg = {
            "TRANSCRIPTION_PROVIDER": self.provider.currentData() or "openai",
            "OPENAI_API_KEY":         self.openai.text().strip(),
            "DEEPGRAM_API_KEY":       self.deepgram.text().strip(),
            "DEEPSEEK_API_KEY":       self.deepseek.text().strip(),
            "CONTEXT":                self.context.text().strip(),
            "LANGUAGE":               self.language.text().strip(),
            "AUDIO_DEVICE":           self.mic_combo.currentData() or "",
            "PASTE_MODE":             "auto",
        }
        if CONFIG_FILE.exists():
            for line in CONFIG_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    if k.strip() == "PASTE_MODE":
                        cfg["PASTE_MODE"] = v.strip()
        save_config(cfg)
        corr = []
        for i in range(self.corr_table.rowCount()):
            w = (self.corr_table.item(i, 0) or QTableWidgetItem("")).text().strip()
            r = (self.corr_table.item(i, 1) or QTableWidgetItem("")).text().strip()
            if w or r: corr.append((w, r))
        save_corrections(corr)
        templates = []
        for i in range(self.tmpl_table.rowCount()):
            name = (self.tmpl_table.item(i, 0) or QTableWidgetItem("")).text().strip()
            ctx  = (self.tmpl_table.item(i, 1) or QTableWidgetItem("")).text().strip()
            if name: templates.append((name, ctx))
        save_templates(templates)

    def _save(self):
        self._do_save()
        self.accept()

# ── HistoryDialog ──────────────────────────────────────────────────────────
class HistoryDialog(DarkDialog):
    def __init__(self, parent=None):
        super().__init__("History", parent)
        self.resize(600, 540)
        self.setMinimumSize(480, 400)

        outer = QVBoxLayout(self._content)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        bar = QWidget()
        bar.setStyleSheet("background:#0e0e1a;")
        bar.setFixedHeight(44)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(18, 0, 12, 0)
        lbl = QLabel("Voice transcriptions — last 14 days")
        lbl.setStyleSheet("color:#505080; font-size:9pt; background:transparent;")
        bl.addWidget(lbl)
        bl.addStretch()
        rb = QPushButton("Refresh")
        rb.setObjectName("ghost")
        rb.clicked.connect(self._load)
        bl.addWidget(rb)
        outer.addWidget(bar)

        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setFixedHeight(1)
        div.setStyleSheet("background:#181828; border:none;")
        outer.addWidget(div)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet(SCROLLBAR_CSS)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._body = QWidget()
        self._body.setStyleSheet("background:transparent;")
        self._lay = QVBoxLayout(self._body)
        self._lay.setContentsMargins(14, 10, 14, 10)
        self._lay.setSpacing(0)
        self._lay.addStretch()
        self._scroll.setWidget(self._body)
        outer.addWidget(self._scroll, 1)

        foot = QWidget()
        foot.setFixedHeight(52)
        foot.setStyleSheet("background:#0a0a16; border-top:1px solid #181828;")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(18, 0, 18, 0)
        fl.addStretch()
        cb = QPushButton("Close")
        cb.setObjectName("secondary")
        cb.clicked.connect(self.accept)
        fl.addWidget(cb)
        outer.addWidget(foot)

        self._load()

    def _clear(self):
        while self._lay.count() > 1:
            item = self._lay.takeAt(0)
            if item.widget(): item.widget().deleteLater()

    def _load(self):
        self._clear()
        entries = []
        for i in range(14):
            d  = datetime.now().date() - timedelta(days=i)
            nf = NOTES_DIR / f"{d}.md"
            if not nf.exists(): continue
            ts, body = "", []
            for line in nf.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line.startswith("## "):
                    if body: entries.append((d, ts, "\n".join(body)))
                    ts, body = line[3:], []
                elif line:
                    body.append(line)
            if body: entries.append((d, ts, "\n".join(body)))

        if not entries:
            empty = QLabel('<div style="color:#2a2a48;padding:40px 10px;line-height:2;">No transcriptions yet.<br>Hold Win+V to start recording.</div>')
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._lay.insertWidget(self._lay.count() - 1, empty)
            return

        entries.sort(key=lambda e: (e[0], e[1]), reverse=True)
        cur_date = None
        for date, ts, body in entries:
            if date != cur_date:
                cur_date = date
                dl = QLabel(f'<span style="color:#03DAC6;font-size:9pt;font-weight:700;">{date.strftime("%A, %d %B %Y")}</span>')
                dl.setStyleSheet("background:transparent; padding:16px 4px 4px 4px;")
                self._lay.insertWidget(self._lay.count() - 1, dl)

            card = QWidget()
            card.setObjectName("card")
            card.setStyleSheet("QWidget#card { background:#131320; border-radius:7px; border:1px solid #1c1c2e; margin-bottom:3px; }")
            cl = QHBoxLayout(card)
            cl.setContentsMargins(12, 8, 8, 8)
            cl.setSpacing(8)

            col = QVBoxLayout()
            col.setSpacing(2)
            col.addWidget(QLabel(f'<span style="color:#2e2e50;font-size:8pt;">{ts}</span>'))
            bl = QLabel(body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
            bl.setWordWrap(True)
            bl.setStyleSheet("background:transparent; color:#a8a8cc; font-size:9.5pt;")
            bl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            col.addWidget(bl)
            cl.addLayout(col, 1)

            cp = QPushButton("Copy")
            cp.setObjectName("ghost")
            cp.setFixedSize(46, 24)
            cp.clicked.connect(lambda _c, t=body: QApplication.clipboard().setText(t))
            cl.addWidget(cp, 0, Qt.AlignmentFlag.AlignTop)

            self._lay.insertWidget(self._lay.count() - 1, card)

# ── TrayApp ────────────────────────────────────────────────────────────────
class TrayApp(QApplication):
    _sig_state = pyqtSignal(str)           # state for FloatingIcon (thread-safe)
    _sig_done  = pyqtSignal(str, str, bool) # display, full_text, do_paste
    _sig_error = pyqtSignal()               # show error then return to idle

    def __init__(self):
        log("TrayApp init start")
        super().__init__(sys.argv)
        log("QApplication created")
        self.setQuitOnLastWindowClosed(False)
        self.setStyleSheet(DARK_STYLE)

        self.recording    = False
        self._long_mode   = False
        log("creating Recorder")
        self._recorder    = Recorder()
        log("Recorder created")
        self._long_timer  = None
        self.settings_dlg = None
        self.history_dlg  = None

        self.tray = QSystemTrayIcon()
        self.tray.setIcon(make_tray_icon(False))
        self.tray.setToolTip("GoVoice")
        self.tray.activated.connect(self._on_tray_click)

        self.menu = QMenu()
        self.act_start  = QAction("Start Recording\tCtrl+Alt+R")
        self.act_stop   = QAction("Stop Recording\tCtrl+Alt+R")
        self.act_long   = QAction("Long Recording (10 min)")
        self.act_cancel = QAction("Cancel\tCtrl+Alt+Esc")
        self.act_set    = QAction("Settings…")
        self.act_hist   = QAction("History…")
        self.act_quit   = QAction("Exit")

        self.act_start.triggered.connect(self._cmd_start)
        self.act_stop.triggered.connect(self._cmd_stop)
        self.act_stop.setEnabled(False)
        self.act_long.triggered.connect(lambda: self._cmd_start(long=True))
        # Long recording only available via tray menu — no hotkey (Win+Shift+V was unreliable)
        self.act_cancel.triggered.connect(self._cmd_cancel)
        self.act_cancel.setEnabled(False)
        self.act_set.triggered.connect(self._open_settings)
        self.act_hist.triggered.connect(self._open_history)
        self.act_quit.triggered.connect(self._quit)

        for act in [self.act_start, self.act_stop, self.act_long, self.act_cancel,
                    None, self.act_set, self.act_hist, None, self.act_quit]:
            if act: self.menu.addAction(act)
            else:   self.menu.addSeparator()

        self.tray.setContextMenu(self.menu)
        self.tray.show()

        log("creating FloatingIcon")
        self.float_icon = FloatingIcon(tray_app=self)
        log("FloatingIcon created")

        log("connecting signals")
        self._sig_state.connect(self.float_icon.set_state)
        self._sig_done.connect(self._on_done)
        self._sig_error.connect(self._on_error)
        log("signals connected")

        log("installing hotkey")
        self._install_hook()
        log("startup complete")

    # ── Hotkey ─────────────────────────────────────────────────────────────

    def _install_hook(self):
        self._hk_active = False

        def on_record():
            log(f"Ctrl+Alt+R  hk_active={self._hk_active}")
            if not self._hk_active:
                self._hk_active = True
                self._cmd_start()
            else:
                self._hk_active = False
                self._cmd_stop()

        def on_cancel():
            log("Ctrl+Alt+Esc")
            self._hk_active = False
            self._cmd_cancel()

        self._hotkey_filter = HotkeyFilter(on_record, on_cancel)
        self.installNativeEventFilter(self._hotkey_filter)

    def _remove_hook(self):
        if hasattr(self, "_hotkey_filter"):
            self._hotkey_filter.unregister()
            self.removeNativeEventFilter(self._hotkey_filter)

    # ── Tray ───────────────────────────────────────────────────────────────

    def _on_tray_click(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.recording: self._cmd_stop()
            else:              self._cmd_start()

    # ── Commands ───────────────────────────────────────────────────────────

    def _cmd_start(self, long=False):
        if self.recording:
            return
        cfg = load_config()
        device = cfg.get("AUDIO_DEVICE") or None
        log(f"start recording  device={device!r}  long={long}")
        try:
            self._recorder.start(device_name=device)
        except Exception as e:
            log(f"recorder.start failed: {e}")
            self._on_error()
            return
        self._long_mode = long
        self.recording  = True
        self.float_icon.set_state(FloatingIcon.RECORDING)
        self._update_ui()
        if long:
            self._long_timer = QTimer()
            self._long_timer.setSingleShot(True)
            self._long_timer.timeout.connect(self._cmd_stop)
            self._long_timer.start(LONG_MAX_SECONDS * 1000)

    def _cmd_stop(self):
        if not self.recording:
            return
        if self._long_timer:
            self._long_timer.stop()
            self._long_timer = None
        self.recording = False
        self._update_ui()
        self.float_icon.set_state(FloatingIcon.TRANSCRIBING)
        long_mode = self._long_mode
        self._long_mode = False
        # Stop recorder and process entirely in background — don't block Qt thread
        threading.Thread(target=self._stop_and_process, args=(long_mode,), daemon=True).start()

    def _cmd_cancel(self):
        if self._long_timer:
            self._long_timer.stop()
            self._long_timer = None
        self._long_mode = False
        if not self.recording:
            return
        self.recording = False
        self._recorder.cancel()
        self._update_ui()
        self.float_icon.set_state(FloatingIcon.IDLE)

    # ── Processing (background thread) ────────────────────────────────────

    def _stop_and_process(self, long_mode):
        audio = self._recorder.stop()
        rate  = self._recorder.sample_rate
        n = len(audio) if audio is not None else 0
        min_n = int(rate * 0.1)   # 100ms minimum
        log(f"stop recording  samples={n}  rate={rate}  min_needed={min_n}")
        if audio is None or n < min_n:
            log("too short — aborting")
            self._sig_error.emit()
            return
        self._process(audio, rate, long_mode)

    def _process(self, audio, rate, long_mode):
        try:
            peak = int(np.max(np.abs(audio)))
            rms  = int(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))
            log(f"process  samples={len(audio)}  peak={peak}  rms={rms}")
            if peak < SILENCE_PEAK or rms < 100:
                log("near-silent, aborting")
                self._sig_error.emit()
                return
            wav   = audio_to_wav(audio, rate)
            cfg   = load_config()
            corr  = load_corrections()
            provider = cfg.get("TRANSCRIPTION_PROVIDER", "openai").lower().strip()
            if provider == "deepgram":
                key = cfg.get("DEEPGRAM_API_KEY", "")
                prompt = ""
                keywords = [r for _, r in corr[:20]]
            else:
                key = cfg.get("OPENAI_API_KEY", "")
                prompt = build_whisper_prompt(corr)
                keywords = None
            if not key:
                log(f"missing {provider} key")
                self._sig_error.emit()
                return
            log(f"calling {provider}…")
            raw = api_transcribe(provider, key, wav,
                                 prompt=prompt,
                                 language=cfg.get("LANGUAGE", ""),
                                 keywords=keywords)
            log(f"transcript: {raw[:80]!r}")
            if not raw:
                log("empty transcript — wrong mic, or no speech detected")
                self._sig_error.emit()
                return
            final = raw
            ds = cfg.get("DEEPSEEK_API_KEY", "")
            if ds and len(raw.split()) >= MIN_WORDS_CLEANUP:
                try:
                    log("calling DeepSeek…")
                    final = api_cleanup(ds, raw, cfg.get("CONTEXT", ""))
                    log(f"cleaned: {final[:80]!r}")
                except Exception as e:
                    log(f"cleanup failed: {e}")
            if corr:
                final = apply_corrections(final, corr)
            save_to_notes(final)
            do_paste = not long_mode and cfg.get("PASTE_MODE", "auto") != "off"
            log(f"done  do_paste={do_paste}")
            self._sig_done.emit(final[:80] + ("…" if len(final) > 80 else ""), final, do_paste)
        except Exception as e:
            log(f"process exception: {e}")
            self._sig_error.emit()

    # ── Result handlers (main thread) ─────────────────────────────────────

    def _on_done(self, preview, full_text, do_paste):
        self.float_icon.set_state(FloatingIcon.DONE)
        QApplication.clipboard().setText(full_text)
        if do_paste:
            QTimer.singleShot(80, paste_clipboard)
        QTimer.singleShot(3500, lambda: self.float_icon.set_state(FloatingIcon.IDLE))

    def _on_error(self):
        self.float_icon.set_state(FloatingIcon.ERROR)
        QTimer.singleShot(3500, lambda: self.float_icon.set_state(FloatingIcon.IDLE))

    # ── Helpers ────────────────────────────────────────────────────────────

    def _update_ui(self):
        self.tray.setIcon(make_tray_icon(self.recording))
        self.tray.setToolTip("GoVoice  ·  Recording…" if self.recording else "GoVoice")
        self.act_start.setEnabled(not self.recording)
        self.act_stop.setEnabled(self.recording)
        self.act_long.setEnabled(not self.recording)
        self.act_cancel.setEnabled(self.recording)

    def _open_settings(self):
        try:
            if self.settings_dlg and self.settings_dlg.isVisible():
                self.settings_dlg.raise_()
                self.settings_dlg.activateWindow()
                return
            self.settings_dlg = SettingsDialog()
            self.settings_dlg.finished.connect(lambda: setattr(self, "settings_dlg", None))
            self.settings_dlg.exec()
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(None, "GoVoice", str(e))

    def _open_history(self):
        try:
            if self.history_dlg and self.history_dlg.isVisible():
                self.history_dlg.raise_()
                self.history_dlg.activateWindow()
                return
            self.history_dlg = HistoryDialog()
            self.history_dlg.finished.connect(lambda: setattr(self, "history_dlg", None))
            self.history_dlg.exec()
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(None, "GoVoice", str(e))

    def _quit(self):
        self._remove_hook()
        if self.recording: self._recorder.cancel()
        self.float_icon.close()
        self.tray.hide()
        self.quit()


if __name__ == "__main__":
    app = TrayApp()
    sys.exit(app.exec())
