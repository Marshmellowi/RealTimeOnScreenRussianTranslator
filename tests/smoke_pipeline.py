"""End-to-end smoke test: render Russian text -> OCR (GPU) -> translate.

Run:  python tests/smoke_pipeline.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ocr import OcrEngine
from translate import Translator


def make_image() -> np.ndarray:
    img = Image.new("RGB", (640, 200), (15, 15, 15))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 40)
    except OSError:
        font = ImageFont.load_default()
    draw.text((20, 30), "Привет, как дела?", fill=(255, 255, 255), font=font)
    draw.text((20, 110), "Нажмите кнопку", fill=(255, 255, 255), font=font)
    return np.asarray(img)


def main():
    rgb = make_image()
    print("[test] loading OCR (GPU)...")
    ocr = OcrEngine(gpu=True)
    print("[test] loading translator...")
    tr = Translator("ru", "en")

    print("[test] running OCR...")
    found = ocr.read(rgb, min_confidence=0.2)
    if not found:
        print("[test] FAIL: no text detected")
        return 1
    for x, y, w, h, text in found:
        en = tr.translate(text)
        print(f"  ({x:4d},{y:4d}) RU={text!r}  ->  EN={en!r}")
    print("[test] PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
