"""Worker thread: capture -> OCR -> translate loop.

Heavy engines (EasyOCR, Argos) are constructed inside run() so their slow first
load happens off the GUI thread, with status updates emitted along the way.
Emits `results` with boxes in absolute PHYSICAL pixels for the overlay.
"""
from __future__ import annotations

import re
import threading
import time

from PySide6 import QtCore

# Filters for which OCR lines get translated (config "translate_filter"):
#   "cyrillic"  - only lines with Cyrillic characters (the old russian_only)
#   "non_ascii" - skip lines that are plain ASCII (i.e. already English)
#   "none"      - translate everything
_CYRILLIC = re.compile(r"[А-Яа-яЁё]")
_NON_ASCII = re.compile(r"[^\x00-\x7F]")

# Loaded engines are cached at module level so stop/start (and settings
# changes that rebuild the pipeline) don't pay the multi-second EasyOCR /
# Argos load again. The lock also serializes the launch-time warm_up()
# against a user clicking Start mid-load.
_engine_lock = threading.Lock()
_ocr_engines: dict = {}
_translators: dict = {}


def _ocr_cfg_key(cfg: dict) -> tuple:
    return (
        cfg.get("ocr_engine", "easyocr").lower(),
        tuple(cfg.get("ocr_languages", ["ru", "en"])),
        bool(cfg.get("ocr_gpu", True)),
    )


def _get_ocr(cfg: dict):
    key = _ocr_cfg_key(cfg)
    engine_name, langs, gpu = key
    with _engine_lock:
        engine = _ocr_engines.get(key)
        if engine is None:
            if engine_name == "rapidocr":
                from ocr import RapidOcrEngine

                engine = RapidOcrEngine(langs)
            else:
                from ocr import OcrEngine

                engine = OcrEngine(langs, gpu=gpu)
            _ocr_engines[key] = engine
        return engine


def _translator_cfg_key(cfg: dict) -> tuple:
    return (
        cfg.get("translator_backend", "argos").lower(),
        cfg.get("source_lang", "auto"),
        cfg.get("deepl_api_key", ""),
        cfg.get("deepl_target", "EN-US"),
        cfg.get("ollama_host", ""),
        cfg.get("ollama_model", ""),
    )


def _get_translator(cfg: dict):
    key = _translator_cfg_key(cfg)
    with _engine_lock:
        translator = _translators.get(key)
        if translator is None:
            from translate import make_translator

            translator = make_translator(cfg)
            _translators[key] = translator
        return translator


def warm_up(cfg: dict, status=None) -> None:
    """Preload the OCR engine and translator so the first Start is instant.

    Meant to run on a background thread at launch; errors are reported via
    the status callback instead of raised (Start will retry and surface them).
    """
    def say(msg: str):
        if status:
            status(msg)

    try:
        say("Loading OCR model in background...")
        _get_ocr(cfg)
        say("Loading translator in background...")
        _get_translator(cfg)
        say("Ready.")
    except Exception as e:
        say(f"Preload failed: {e}")


class TranslatePipeline(QtCore.QThread):
    results = QtCore.Signal(list)     # [(x, y, w, h, text), ...] absolute physical px
    status = QtCore.Signal(str)
    ready = QtCore.Signal()

    def __init__(self, cfg: dict):
        super().__init__()
        self._cfg = cfg
        self._region: dict | None = cfg.get("region")
        self._running = True

    def set_region(self, region: dict):
        self._region = region
        # Force the next frame to be treated as changed.
        if hasattr(self, "_capture"):
            self._capture.reset()

    def stop(self):
        self._running = False

    def run(self):
        cfg = self._cfg
        from capture import RegionCapture

        self._capture = RegionCapture(cfg.get("change_threshold", 6.0))

        self.status.emit("Loading OCR model (first run downloads ~100MB)...")
        try:
            ocr = _get_ocr(cfg)
        except Exception as e:
            self.status.emit(f"OCR init failed: {e}")
            return

        self.status.emit("Initializing translator...")
        try:
            translator = _get_translator(cfg)
        except Exception as e:
            self.status.emit(f"Translator init failed: {e}")
            return

        self.status.emit("Ready.")
        self.ready.emit()

        interval = float(cfg.get("interval_seconds", 5.0))
        min_conf = cfg.get("min_confidence", 0.35)
        upscale = float(cfg.get("ocr_upscale", 2.0))
        filt = cfg.get("translate_filter")
        if filt is None:
            # Legacy config: russian_only=False meant "translate everything".
            filt = "non_ascii" if cfg.get("russian_only", True) else "none"
        last_sig = None

        while self._running:
            start = time.perf_counter()
            region = self._region
            if not region:
                time.sleep(0.2)
                continue
            try:
                # Take a single snapshot, OCR + translate the whole thing, then
                # replace the overlay in one shot. The previous translation stays
                # on screen until this new one is ready.
                rgb = self._capture.grab(region)
                # Skip the (expensive) OCR pass entirely while the region's
                # pixels haven't meaningfully changed since the last accepted
                # grab — static scenes cost nothing.
                if self._capture.has_changed(rgb):
                    found = ocr.read(rgb, min_conf, upscale)
                    if filt == "cyrillic":
                        found = [f for f in found if _CYRILLIC.search(f[4])]
                    elif filt == "non_ascii":
                        found = [f for f in found if _NON_ASCII.search(f[4])]
                    # Translate the whole snapshot in one batch (one DeepL request).
                    translated = translator.translate_many([f[4] for f in found])
                    boxes = [
                        (region["left"] + x, region["top"] + y, w, h, en)
                        for (x, y, w, h, _), en in zip(found, translated)
                    ]
                    # Only repaint when the TEXT actually changed. Ignoring the box
                    # coordinates keeps the overlay frozen in place while the same
                    # text is on screen (EasyOCR's boxes jitter a few px each grab,
                    # which otherwise makes the overlay hop around).
                    sig = tuple(sorted(b[4] for b in boxes))
                    if sig != last_sig:
                        last_sig = sig
                        self.results.emit(boxes)
            except Exception as e:
                self.status.emit(f"loop error: {e}")

            # Wait out the rest of the interval, but stay responsive to Stop.
            remaining = interval - (time.perf_counter() - start)
            while remaining > 0 and self._running:
                step = min(0.2, remaining)
                time.sleep(step)
                remaining -= step

        self._capture.close()
