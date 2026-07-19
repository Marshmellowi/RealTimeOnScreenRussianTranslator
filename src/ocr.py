"""OCR engines (EasyOCR / RapidOCR) for on-screen text detection.

Both engines expose read(rgb, min_confidence, upscale) returning
[(x, y, w, h, text), ...] in pixel coordinates relative to the input image
(i.e. relative to the captured region). The pipeline offsets these by the
region origin to place overlay text.
"""
from __future__ import annotations

import re

# cv2/numpy/easyocr are imported lazily inside methods: importing this module
# must stay cheap so the GUI window can appear before the heavy libs load.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

# A row must contain at least one letter (any script: Latin, Cyrillic, CJK...)
# to be kept; this drops standalone numbers, icons and punctuation
# (UI counters/timers). [^\W\d_] matches any Unicode letter.
_HAS_LETTER = re.compile(r"[^\W\d_]")


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
    def __init__(self, langs: tuple[str, ...] = ("ru", "en"), gpu: bool = True):
        # Imported lazily so importing this module is cheap and so the (slow)
        # model load happens when the engine is actually constructed.
        import easyocr

        self._reader = easyocr.Reader(list(langs), gpu=gpu)

    def read(self, rgb: np.ndarray, min_confidence: float = 0.35, upscale: float = 1.0):
        """Return [(x, y, w, h, text), ...] for boxes above min_confidence.

        Small stylized fonts (game chat/subtitles) OCR much better enlarged, so
        we optionally upscale before reading and map coordinates back down.
        """
        import cv2

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


# Map app language codes to RapidOCR recognition model families. RapidOCR
# loads ONE recognition model, so the first non-English language in the list
# decides which one; all of them read English/Latin fine alongside.
_RAPID_LANG = {
    "ru": "eslav", "uk": "eslav", "be": "eslav", "bg": "cyrillic",
    "sr": "cyrillic", "ja": "japan", "ko": "korean", "zh": "ch",
    "ch_sim": "ch", "ch_tra": "chinese_cht", "ar": "arabic", "el": "el",
    "th": "th", "hi": "devanagari", "ta": "ta", "te": "te", "ka": "ka",
    "en": "en",
}


class RapidOcrEngine:
    """RapidOCR (PP-OCRv5 ONNX models). CPU-only, ~2s load, no VRAM use, and
    in testing more accurate than EasyOCR on Cyrillic UI text."""

    def __init__(self, langs: tuple[str, ...] = ("ru", "en")):
        import logging

        from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR

        logging.getLogger("RapidOCR").setLevel(logging.WARNING)
        lang = next(
            (_RAPID_LANG[l] for l in langs
             if l in _RAPID_LANG and _RAPID_LANG[l] != "en"),
            "latin",
        )
        rec_lang = LangRec(lang)
        # Not every (version, size) combo exists per language; fall back from
        # the known-good v5 mobile models to RapidOCR's own defaults.
        last_err: Exception | None = None
        for params in (
            {"Rec.lang_type": rec_lang, "Rec.ocr_version": OCRVersion.PPOCRV5,
             "Rec.model_type": ModelType.MOBILE},
            {"Rec.lang_type": rec_lang},
            {},
        ):
            try:
                self._ocr = RapidOCR(params=params)
                break
            except ValueError as e:
                last_err = e
        else:
            raise RuntimeError(f"RapidOCR init failed: {last_err}")

    def read(self, rgb: np.ndarray, min_confidence: float = 0.35, upscale: float = 1.0):
        import cv2

        img = rgb
        if upscale and upscale != 1.0:
            img = cv2.resize(
                rgb, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC
            )
        res = self._ocr(img)
        inv = 1.0 / upscale if upscale else 1.0
        raw = []
        for box, text, conf in zip(
            res.boxes if res.boxes is not None else [],
            res.txts or [],
            res.scores or [],
        ):
            if conf is not None and conf < min_confidence:
                continue
            text = (text or "").strip()
            if not text:
                continue
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            x = int(min(xs) * inv)
            y = int(min(ys) * inv)
            w = int((max(xs) - min(xs)) * inv)
            h = int((max(ys) - min(ys)) * inv)
            raw.append((x, y, w, h, text))
        rows = _group_rows(raw)
        return [r for r in rows if len(r[4]) >= 2 and _HAS_LETTER.search(r[4])]
