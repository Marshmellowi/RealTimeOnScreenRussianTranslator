"""EasyOCR wrapper for Russian text detection.

EasyOCR returns a list of (box, text, confidence) where box is 4 corner points
in pixel coordinates relative to the input image (i.e. relative to the captured
region). The pipeline offsets these by the region origin to place overlay text.
"""
from __future__ import annotations

import re

import cv2
import numpy as np

# A row must contain at least one letter (Latin or Cyrillic) to be kept;
# this drops standalone numbers, icons and punctuation (UI counters/timers).
_HAS_LETTER = re.compile(r"[A-Za-zА-Яа-яЁё]")


def _group_rows(items):
    """Merge boxes that sit on the same text row into one left-to-right line,
    so 'username:' + 'message' become a single 'username: message' line."""
    if not items:
        return []
    rows = []  # each: {"cy": float, "hmax": int, "items": [box, ...]}
    for b in sorted(items, key=lambda b: b[1]):
        x, y, w, h, _t = b
        cy = y + h / 2.0
        for row in rows:
            if abs(cy - row["cy"]) <= max(h, row["hmax"]) * 0.6:
                row["items"].append(b)
                n = len(row["items"])
                row["cy"] = (row["cy"] * (n - 1) + cy) / n
                row["hmax"] = max(row["hmax"], h)
                break
        else:
            rows.append({"cy": cy, "hmax": h, "items": [b]})

    out = []
    for row in rows:
        its = sorted(row["items"], key=lambda b: b[0])
        x0 = min(i[0] for i in its)
        y0 = min(i[1] for i in its)
        x1 = max(i[0] + i[2] for i in its)
        y1 = max(i[1] + i[3] for i in its)
        text = " ".join(i[4] for i in its)
        out.append((x0, y0, x1 - x0, y1 - y0, text))
    out.sort(key=lambda b: b[1])
    return out


class OcrEngine:
    def __init__(self, gpu: bool = True):
        # Imported lazily so importing this module is cheap and so the (slow)
        # model load happens when the engine is actually constructed.
        import easyocr

        self._reader = easyocr.Reader(["ru", "en"], gpu=gpu)

    def read(self, rgb: np.ndarray, min_confidence: float = 0.35, upscale: float = 1.0):
        """Return [(x, y, w, h, text), ...] for boxes above min_confidence.

        Small stylized fonts (game chat/subtitles) OCR much better enlarged, so
        we optionally upscale before reading and map coordinates back down.
        """
        img = rgb
        if upscale and upscale != 1.0:
            img = cv2.resize(
                rgb, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC
            )
        results = self._reader.readtext(img)
        inv = 1.0 / upscale if upscale else 1.0
        raw = []
        for box, text, conf in results:
            if conf < min_confidence:
                continue
            text = text.strip()
            if not text:
                continue
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            x = int(min(xs) * inv)
            y = int(min(ys) * inv)
            w = int((max(xs) - min(xs)) * inv)
            h = int((max(ys) - min(ys)) * inv)
            raw.append((x, y, w, h, text))
        # Merge same-row boxes (keeps "username: message" together), then drop
        # rows that are just numbers/symbols.
        rows = _group_rows(raw)
        return [r for r in rows if len(r[4]) >= 2 and _HAS_LETTER.search(r[4])]
