# voice-input

A Wispr-like voice-to-text tool for Linux (i3/X11). Hold a hotkey, speak, release — text is transcribed via OpenAI Whisper, cleaned up by DeepSeek, then typed into whatever window is focused.

## Features

- **Push-to-talk** — hold `Mod4+grave` to record, release to transcribe
- **Long recording mode** — up to 10 minutes, no key holding required
- **AI cleanup** — DeepSeek fixes grammar and punctuation without changing meaning
- **Vocabulary hints** — your corrections list is sent to Whisper to improve proper noun accuracy
- **rofi history UI** — browse, copy, or retype past transcriptions; manage settings
- **Daily notes** — every transcription saved to `~/Documents/voice-notes/YYYY-MM-DD.md`

## Dependencies

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

## Setup

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

## Hotkeys

| Key | Action |
|-----|--------|
| `Mod4+grave` (hold) | Record |
| `Mod4+grave` (release) | Stop and transcribe |
| `Mod4+Escape` | Cancel (or finish long recording) |
| `Mod4+Shift+grave` | Open history + settings menu |

## Config

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
