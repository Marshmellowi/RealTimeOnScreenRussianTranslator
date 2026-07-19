"""Load/save persistent settings from config.json in the project root."""
from __future__ import annotations

import json
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.json"

DEFAULTS = {
    "region": None,                 # [left, top, width, height] in physical px, or null
    "interval_seconds": 5.0,        # snapshot/translate every N seconds
    "capture_fps": 3.0,             # (unused in snapshot mode; kept for reference)
    "translator_backend": "nllb",   # "nllb"/"argos" (offline), "deepl" (API), "ollama" (local LLM)
    "deepl_api_key": "",            # DeepL auth key (free keys end in ":fx")
    "deepl_target": "EN-US",        # DeepL target language
    "ollama_host": "http://localhost:11434",
    "ollama_model": "translategemma:4b",
    "ocr_gpu": True,                # use CUDA for EasyOCR
    "ocr_engine": "rapidocr",       # "rapidocr" (CPU, fast, no VRAM) or "easyocr" (GPU)
    "ocr_languages": ["ru", "en"],  # EasyOCR langs; CJK ("ja","ch_sim","ko") must pair with "en" only
    "source_lang": "auto",          # translation source: "auto" (DeepL/Ollama detect) or code like "ru"
    "translate_filter": "non_ascii",# which OCR lines to translate: "non_ascii" (skip plain English), "cyrillic", "none"
    "min_confidence": 0.5,          # drop OCR results below this confidence
    "ocr_upscale": 2.0,             # enlarge capture before OCR (helps small fonts)
    "font_size": 13,                # overlay text size (point size)
    "overlay_mode": "panel",        # "panel" (feed), "subtitle" (block), "inline" (per-box)
    "subtitle_anchor": "bottom",    # "bottom" or "top" of the capture region
    "feed_max_lines": 18,           # max lines kept in the panel feed
    "feed_ttl_seconds": 20.0,       # panel lines fade out this long after appearing
    "overlay_opacity": 0.85,        # whole-overlay opacity
    "overlay_bg": [0, 0, 0, 180],   # text background RGBA
    "overlay_fg": [255, 255, 255, 255],
    "click_through": True,          # overlay ignores mouse so the game stays usable
    "change_threshold": 1.5,        # mean per-pixel diff to consider region "changed"
                                    # (low: a single new chat line in a big region is subtle)
    "hotkey_toggle": "<ctrl>+<alt>+t",
    "hotkey_region": "<ctrl>+<alt>+r",
    "hotkey_compose": "<ctrl>+<alt>+m", # open the compose (type-to-translate) overlay
    "compose_target": "ru",             # language your typed message is translated into
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except (json.JSONDecodeError, OSError) as e:
            print(f"[config] could not read {CONFIG_PATH}: {e}; using defaults")
    return cfg


def save(cfg: dict) -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except OSError as e:
        print(f"[config] could not write {CONFIG_PATH}: {e}")
