"""Worker thread: capture -> OCR -> translate loop.

Heavy engines (EasyOCR, Argos) are constructed inside run() so their slow first
load happens off the GUI thread, with status updates emitted along the way.
Emits `results` with boxes in absolute PHYSICAL pixels for the overlay.
"""
from __future__ import annotations

import re
import time

from PySide6 import QtCore

# A line is treated as Russian only if it contains Cyrillic characters.
_CYRILLIC = re.compile(r"[А-Яа-яЁё]")

from capture import RegionCapture
from ocr import OcrEngine
from translate import make_translator


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
        self._capture = RegionCapture(cfg.get("change_threshold", 6.0))

        self.status.emit("Loading OCR model (first run downloads ~100MB)...")
        try:
            ocr = OcrEngine(gpu=cfg.get("ocr_gpu", True))
        except Exception as e:
            self.status.emit(f"OCR init failed: {e}")
            return

        self.status.emit("Initializing translator...")
        try:
            translator = make_translator(cfg)
        except Exception as e:
            self.status.emit(f"Translator init failed: {e}")
            return

        self.status.emit("Ready.")
        self.ready.emit()

        interval = float(cfg.get("interval_seconds", 5.0))
        min_conf = cfg.get("min_confidence", 0.35)
        upscale = float(cfg.get("ocr_upscale", 2.0))
        russian_only = cfg.get("russian_only", True)
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
                found = ocr.read(rgb, min_conf, upscale)
                if russian_only:
                    found = [f for f in found if _CYRILLIC.search(f[4])]
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
