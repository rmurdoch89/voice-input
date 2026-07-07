# voice-input

A Wispr-like voice-to-text tool. Speak, and text is transcribed via OpenAI Whisper, cleaned up by DeepSeek, then typed into whatever window is focused.

Windows and Linux are two independent implementations that happen to share a repo — not shared code:

- **Linux** (this directory) — a set of Python scripts (`voice-input`, `voice-history`), push-to-talk via i3 keybindings.
- **Windows** (`windows/`) — a self-contained PyQt6 tray app (`voice-input-gui.pyw`), packaged as a one-click installer.

Jump to: [Linux setup](#setup--linux) · [Windows setup](#setup--windows)

## Setup — Linux

### Features

- **Push-to-talk** — hold `Mod4+grave` to record, release to transcribe
- **Long recording mode** — up to 10 minutes, no key holding required
- **AI cleanup** — DeepSeek fixes grammar and punctuation without changing meaning
- **Vocabulary hints** — your corrections list is sent to Whisper to improve proper noun accuracy
- **rofi history UI** — browse, copy, or retype past transcriptions; manage settings
- **Daily notes** — every transcription saved to `~/Documents/voice-notes/YYYY-MM-DD.md`

### Dependencies

- `arecord` (alsa-utils)
- `xdotool`
- `xclip`
- `rofi`
- `dunst`
- `notify-send` (libnotify)
- Python 3 with `openai` and `requests` packages

```bash
sudo pacman -S alsa-utils xdotool xclip rofi dunst libnotify
pip install --user --break-system-packages openai requests
```

### Install

1. Copy scripts to `~/.local/bin/` and make executable:
   ```bash
   cp voice-input voice-history ~/.local/bin/
   chmod +x ~/.local/bin/voice-input ~/.local/bin/voice-history
   ```

2. Copy config and fill in your API keys:
   ```bash
   mkdir -p ~/.config/voice-input
   cp config.example ~/.config/voice-input/config
   chmod 600 ~/.config/voice-input/config
   ```

3. Copy corrections file:
   ```bash
   cp corrections.example.txt ~/.config/voice-input/corrections.txt
   ```

4. Add to your i3 config:
   ```
   bindsym Mod4+grave exec --no-startup-id voice-input start
   bindsym --release Mod4+grave exec --no-startup-id voice-input stop
   bindsym Mod4+Escape exec --no-startup-id voice-input cancel
   bindsym Mod4+Shift+grave exec --no-startup-id voice-history
   exec --no-startup-id dunst
   ```

5. Reload i3: `Mod4+Shift+c`

### Hotkeys

| Key | Action |
|-----|--------|
| `Mod4+grave` (hold) | Record |
| `Mod4+grave` (release) | Stop and transcribe |
| `Mod4+Escape` | Cancel (or finish long recording) |
| `Mod4+Shift+grave` | Open history + settings menu |

### Config

`~/.config/voice-input/config`:
```
OPENAI_API_KEY=sk-...
DEEPSEEK_API_KEY=sk-...
CONTEXT=Australian software developer. Works on a project called "rumbo"...
```

`~/.config/voice-input/corrections.txt` — one correction per line:
```
wrong phrase -> Correct Phrase
```

## Setup — Windows

Everything Windows-specific lives under [`windows/`](windows/).

### Option A: Installer (recommended)

1. Build or download `GoVoice-Setup.exe` (see "Building the installer" below).
2. Run it — no admin rights needed, installs per-user to `%LocalAppData%\Programs\GoVoice`. The installer isn't code-signed, so Windows SmartScreen will show an "Unknown publisher" warning on first run — click "More info" → "Run anyway."
3. Enter your OpenAI API key (required) and DeepSeek key (optional, for grammar cleanup) when prompted. These get written to `%APPDATA%\voice-input\config`.
4. Optionally tick "Start GoVoice automatically when Windows starts."

A teal mic icon appears in the system tray. Right-click for the menu, or use the hotkeys below. To change API keys, context, or corrections later, use the tray icon's Settings dialog — no need to reinstall.

### Option B: Run from source

**Prerequisites**
- Windows 10/11
- Python 3.11+ — `winget install Python.Python.3.13`
- `pip install PyQt6 sounddevice numpy httpx`

**Steps**
1. Copy `windows/voice-input-gui.pyw` and the two `.vbs` launchers somewhere, e.g. `C:\voice-input\`.
2. Create `%APPDATA%\voice-input\config`:
   ```
   OPENAI_API_KEY=sk-your-key-here
   DEEPSEEK_API_KEY=sk-your-key-here
   CONTEXT=Describe yourself — role, tools, project names
   LANGUAGE=en
   ```
3. (Optional) Create `%APPDATA%\voice-input\corrections.txt` (same format as Linux, above).
4. Double-click `voice-input-gui.vbs` (or run `pythonw.exe voice-input-gui.pyw` directly).

To auto-start on login, copy `voice-input-gui.vbs` to `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\` — it launches whatever `pythonw.exe` is first on your `PATH`, so edit the `.vbs` if you have a non-standard Python install.

### Building the installer yourself

```cmd
cd windows\dist_build
python -m PyInstaller --noconfirm --clean --onefile --windowed --name GoVoice --icon assets\icon.ico ^
  --exclude-module matplotlib --exclude-module tkinter --exclude-module PySide2 --exclude-module PySide6 --exclude-module PyQt5 ^
  --exclude-module scipy --exclude-module pandas --exclude-module IPython --exclude-module notebook ^
  ..\voice-input-gui.pyw
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" GoVoice.iss
```
Output: `windows\dist_build\installer_output\GoVoice-Setup.exe`

### Hotkeys (Windows)

| Key | Action |
|---|---|
| `Ctrl+Alt+R` | Press once to start recording, press again to stop, transcribe, and type |
| `Ctrl+Alt+Esc` | Cancel the current recording |

Long recording (up to 10 min) is tray-menu only — right-click the tray icon → "Long Recording (10 min)." Tray icon left-click also toggles recording.

### Features (Windows)

- **System tray** — teal mic icon, red when recording
- **Recording overlay** — floating glass pill with pulsing mic near cursor
- **Settings dialog** — API keys, context with 8 templates, language, corrections table
- **History browser** — scrollable cards per transcript, individual Copy buttons
- **Auto paste** — Ctrl+V after a normal (non-long) recording, unless `PASTE_MODE=off`
- **Daily notes** — saved to `%USERPROFILE%\Documents\voice-notes\YYYY-MM-DD.md`
- **Native toast notifications** via PowerShell

### Windows troubleshooting

**Recording won't stop:**
1. Check `%TEMP%\voice-input-gui.pid` exists (delete if stale)
2. Kill stuck process: `taskkill /f /im GoVoice.exe` (installer) or `taskkill /f /im pythonw.exe` (running from source)
3. Relaunch the GUI

**No transcriptions:**
- Verify `OPENAI_API_KEY` in config is valid
- Check `%TEMP%\voice-input.log` for API errors
- Make sure you're not near-silent (peak < 150 threshold)

**Tray icon doesn't respond:**
- Hover the icon — if tooltip says "GoVoice" it's the live one
- Kill all `GoVoice.exe`/`pythonw.exe` and relaunch — the app self-terminates any previous instance on startup via the PID file, so this is normally automatic

## License

MIT
