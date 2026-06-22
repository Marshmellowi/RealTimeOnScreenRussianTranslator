# Real-Time On-Screen Russian Translator

Captures a region of your screen, OCRs Russian text, translates it to English
**offline**, and draws the translation as a transparent, click-through overlay
on top of your game.

Pipeline: `mss` capture → `EasyOCR` (GPU) → `Argos Translate` (offline) →
`PySide6` overlay.

## Requirements

- Windows 10/11, Python 3.10+
- NVIDIA GPU recommended (this machine: RTX 3070 Ti) for real-time OCR
- Games in **borderless / windowed** mode (true exclusive-fullscreen hides
  overlays — see Caveats)

## Install

```powershell
# 1. (Recommended) virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Install GPU PyTorch FIRST so EasyOCR uses CUDA (not the CPU wheel).
#    CUDA 12.1 build:
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121

# 3. Install the rest
pip install -r requirements.txt

# 4. One-time: download the offline ru->en model (needs internet once)
python setup_models.py
```

## Run

```powershell
python src/main.py
```

1. Click **Select Region** (or press `Ctrl+Alt+R`) and drag a box over the area
   with Russian text (subtitle/dialogue area works best).
2. Click **Start** (or `Ctrl+Alt+T`). First start downloads EasyOCR's Russian
   models (~100 MB) — subsequent starts are fast.
3. Translations appear over the text. Press **Stop** / `Ctrl+Alt+T` to pause.

## Translation backends

Set `translator_backend` in `config.json`:

- `"argos"` — fully offline, free, no key. Run `python setup_models.py` once.
- `"deepl"` — DeepL API (higher quality). Get a free key at
  deepl.com/pro-api (free keys end in `:fx`), then either set the
  `DEEPL_API_KEY` environment variable or put it in `deepl_api_key`. The free
  vs pro endpoint is auto-selected from the key. Each snapshot is sent as one
  batched request to conserve the 500k chars/month free quota.

## Configuration — `config.json`

`config.json` is git-ignored (it holds your personal capture region and any API
key). Copy `config.example.json` to `config.json` to start, or just run the app —
it falls back to sensible defaults and writes `config.json` as you use the UI.


| Key | Meaning |
|-----|---------|
| `region` | Saved capture box (physical px). Set via the UI. |
| `interval_seconds` | Snapshot/translate cadence in seconds (default 5). |
| `translator_backend` | `"argos"` (offline) or `"deepl"` (API). |
| `deepl_api_key`, `deepl_target` | DeepL key and target lang (e.g. `EN-US`). |
| `overlay_mode` | `"subtitle"` (fixed block) or `"inline"` (per-OCR-box). |
| `subtitle_anchor` | `"bottom"` or `"top"` of the region. |
| `capture_fps` | (unused in snapshot mode; kept for reference). |
| `ocr_gpu` | Use CUDA for OCR. Set `false` to force CPU. |
| `min_confidence` | Drop OCR results below this score. |
| `font_size`, `overlay_*` | Overlay appearance. |
| `change_threshold` | Higher = less re-translation of near-static text. |
| `hotkey_toggle`, `hotkey_region` | Global hotkeys. |

## Tuning for performance

- Keep the region as **small** as possible (just the dialogue box).
- Lower `capture_fps` to 1–2 for slow dialogue; raise for fast subtitles.
- Raise `change_threshold` if it re-translates flickering/static text too often.

## Caveats

- **Exclusive fullscreen** games will hide the overlay or cause flicker. Use
  *borderless windowed* mode. (For exclusive fullscreen you'd need a second
  monitor / capture-card setup.)
- **DPI scaling:** built for standard setups. If overlay text is offset, set the
  game/display to 100% scaling, or capture a fresh region.
- Translation quality is "good, not perfect" (offline model). The code is
  structured so a cloud backend (DeepL/Google) can be swapped into
  `src/translate.py` later.

## Project layout

```
setup_models.py      one-time Argos model download
config.json          settings
src/
  main.py            control panel + hotkeys (entry point)
  config.py          settings load/save
  region_selector.py drag-a-box selector
  capture.py         mss grab + change detection
  ocr.py             EasyOCR wrapper
  translate.py       Argos offline wrapper
  overlay.py         transparent click-through overlay
  pipeline.py        capture->OCR->translate worker thread
```
