# Real-Time On-Screen Translator

Reads text from any region of your screen (game chat, subtitles, dialogue),
translates it to English **offline**, and shows the translation in a
transparent, click-through overlay on top of your game. Works with Russian
out of the box and supports many other languages.

It also works the other way: press a hotkey, type a message in English, and
get it translated and copied to your clipboard, ready to paste into team/all
chat in Russian (or German, Japanese, …).

Pipeline: `mss` capture → RapidOCR (CPU) → NLLB-200 (offline) → PySide6 overlay.
No accounts or API keys needed for the default setup — everything runs locally.

## Requirements

- **Python 3.10 – 3.13**
- Windows 10/11, macOS, or Linux (X11) — see per-OS notes below
- ~3 GB of free disk space (translation model, downloaded once on first use)
- No GPU required — the default OCR and translation both run on CPU
- Games in **borderless / windowed** mode (exclusive fullscreen hides overlays)

## Install

Clone (or download + unzip) the repo, then in its folder:

### Windows (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python src\main.py
```

After the first successful run you can double-click
**`Launch Russian Translator.vbs`** to start it without a console window
(or **`Launch (debug).bat`** to see errors).

### macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python src/main.py
```

macOS will prompt for two permissions the first time (System Settings →
Privacy & Security):

- **Screen Recording** — required for capturing the game region
- **Accessibility / Input Monitoring** — required for the global hotkeys

Grant them to your terminal app (or whatever launches Python), then restart
the app.

### Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python src/main.py
```

Notes:

- **X11 works; Wayland mostly doesn't.** Screen capture (`mss`) and global
  hotkeys (`pynput`) need X11. On a Wayland desktop, log into an "Xorg" /
  "X11" session instead.
- If Qt complains about missing libraries on a fresh system:
  `sudo apt install libxcb-cursor0 libgl1` (Debian/Ubuntu).

## First run

1. Start the app — the control panel appears instantly; OCR/translation
   models load in the background (status shows progress). **The first run
   downloads the NLLB translation model (~2.5 GB, one time)** plus small
   RapidOCR models (~20 MB); after that it's fully offline.
2. Click **Select Region** (or `Ctrl+Alt+R`) and drag a box over the area
   where foreign text appears (chat box / subtitle area — keep it tight).
3. Click **Start** (or `Ctrl+Alt+T`). Translations appear in the overlay.
   English lines are automatically skipped; only foreign text is translated.

### Replying in their language (Compose)

1. Press `Ctrl+Alt+M` anywhere — a small input box pops up.
2. Type your message in English, pick a target language, press **Enter**.
3. The translation is **copied to your clipboard** and the box hides itself.
4. Open team/all chat in the game and press `Ctrl+V`, then Enter.

`Esc` closes the box; drag it if it covers something important.

### Hotkeys

| Default | Action |
|---------|--------|
| `Ctrl+Alt+T` | Start/stop translating |
| `Ctrl+Alt+R` | Select capture region |
| `Ctrl+Alt+M` | Compose a message (English → chosen language) |

All three can be remapped in **Settings…** — click the field and press the
new combination.

## Translation backends

Pick in **Settings…** (or set `translator_backend` in `config.json`):

| Backend | Quality | Needs | Notes |
|---------|---------|-------|-------|
| `nllb` (default) | good | nothing (offline) | NLLB-200 600M, runs on CPU. Set **Source lang** (e.g. `ru`) — no auto-detect. |
| `deepl` | best | free API key + internet | 500k chars/month free at [deepl.com/pro-api](https://www.deepl.com/pro-api). Auto-detects the source language. |
| `ollama` | good–best | [Ollama](https://ollama.com) running locally | `ollama pull translategemma:4b`. Auto-detects. Uses GPU if available. |
| `argos` | okay | `pip install argostranslate` + `python setup_models.py` | Lightweight offline fallback. |

For DeepL, put the key in Settings or set the `DEEPL_API_KEY` environment
variable.

## Other languages than Russian

- **OCR languages** (Settings): e.g. `ja,en` for Japanese, `ko,en` for
  Korean, `de,en` for German. The default RapidOCR engine picks a matching
  recognition model automatically.
- **Source lang** (Settings): leave `auto` for DeepL/Ollama; set the code
  (`ja`, `de`, …) for NLLB/Argos.
- The overlay only translates lines containing non-English characters by
  default (`translate_filter: "non_ascii"`); set it to `"none"` in
  `config.json` to translate everything.

## Optional: EasyOCR (GPU) engine

The default RapidOCR engine is fast, accurate, and uses no GPU. If you want
to try EasyOCR instead (Settings → OCR engine):

```bash
pip install easyocr
```

For NVIDIA GPU acceleration install PyTorch with CUDA **first** — pick the
right command for your system at
[pytorch.org/get-started](https://pytorch.org/get-started/locally/).

## Configuration — `config.json`

`config.json` is git-ignored (it holds your capture region and any API key).
It's created automatically as you use the UI; `config.example.json` shows the
format. Highlights not covered by the Settings dialog:

| Key | Meaning |
|-----|---------|
| `interval_seconds` | Snapshot/translate cadence (default 5s). |
| `overlay_mode` | `"panel"` (chat feed), `"subtitle"` (block), `"inline"` (per-box). |
| `min_confidence` | Drop OCR results below this score (0–1). |
| `ocr_upscale` | Enlarge the capture before OCR — helps small fonts. |
| `change_threshold` | Pixel-change needed to re-OCR. Raise if it rescans static scenes; lower if it misses new chat lines. |
| `translate_filter` | `"non_ascii"` (skip English), `"cyrillic"`, or `"none"`. |
| `feed_max_lines`, `feed_ttl_seconds` | Panel feed length / fade-out. |
| `font_size`, `overlay_*`, `click_through` | Overlay appearance/behavior. |

## Performance tips

- Keep the capture region **small** — just the chat/subtitle box.
- The app skips OCR entirely while the region's pixels haven't changed, so a
  quiet chat costs almost nothing.
- Lower `interval_seconds` for fast chat; raise it for slow dialogue.
- Everything heavy loads once and is cached — Start/Stop is instant after the
  first launch warm-up.

## Caveats

- **Exclusive fullscreen** hides the overlay — use borderless/windowed mode.
- **Anti-cheat:** the overlay is a normal transparent window and the app only
  *reads* the screen, but some competitive games frown on overlays — use
  your judgment.
- **DPI scaling:** if overlay text is offset from the game text, re-select
  the region, or set display scaling to 100%.
- Offline translation quality is "good, not perfect" — for best results get
  a free DeepL key.

## Project layout

```
requirements.txt      core dependencies (RapidOCR + NLLB defaults)
setup_models.py       one-time Argos model download (optional backend)
config.example.json   example settings
src/
  main.py             control panel, settings, hotkeys (entry point)
  config.py           settings load/save + defaults
  region_selector.py  drag-a-box region selector
  capture.py          mss screen grab + change detection
  ocr.py              RapidOCR / EasyOCR engines
  translate.py        NLLB / DeepL / Ollama / Argos backends (both directions)
  compose.py          type-English → translated-to-clipboard overlay
  overlay.py          transparent click-through overlay
  pipeline.py         capture → OCR → translate worker thread
models/               NLLB model (auto-downloaded, git-ignored)
```
