"""Diagnostic: grab the configured region several times and print OCR results,
so we can see whether the text/positions actually change between snapshots.

    python tests/diag_region.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import config
from capture import RegionCapture
from ocr import OcrEngine


def main():
    cfg = config.load()
    region = cfg.get("region")
    if not region:
        print("No region set.")
        return 1
    print("region:", region)
    cap = RegionCapture()
    ocr = OcrEngine(gpu=cfg.get("ocr_gpu", True))
    min_conf = cfg.get("min_confidence", 0.35)
    upscale = float(cfg.get("ocr_upscale", 2.0))

    for i in range(2):
        rgb = cap.grab(region)
        found = ocr.read(rgb, min_conf, upscale)
        print(f"\n--- snapshot {i} ---")
        for x, y, w, h, text in found:
            print(f"  ({x:4d},{y:4d}) {w}x{h}  {text!r}")
        time.sleep(1.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
