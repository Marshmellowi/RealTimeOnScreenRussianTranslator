"""Screen region capture with cheap change-detection.

Uses mss for fast grabs. Returns RGB numpy arrays. The change detector lets the
pipeline skip OCR/translation when the on-screen text hasn't changed (e.g. a
static subtitle), which keeps GPU/CPU load low during gameplay.
"""
from __future__ import annotations

import numpy as np
import mss


class RegionCapture:
    def __init__(self, change_threshold: float = 6.0):
        self._sct = mss.mss()
        self._change_threshold = change_threshold
        self._last_small: np.ndarray | None = None

    def grab(self, region: dict) -> np.ndarray:
        """region: {'left','top','width','height'} in physical pixels. Returns RGB."""
        shot = self._sct.grab(region)
        # mss gives BGRA; drop alpha and flip to RGB.
        arr = np.asarray(shot)[:, :, :3][:, :, ::-1]
        return np.ascontiguousarray(arr)

    def has_changed(self, rgb: np.ndarray) -> bool:
        """True if the region differs meaningfully from the last grab we accepted."""
        # Downscale to a small grayscale fingerprint for a cheap comparison.
        small = rgb[::8, ::8].mean(axis=2).astype(np.float32)
        if self._last_small is None or self._last_small.shape != small.shape:
            self._last_small = small
            return True
        diff = float(np.abs(small - self._last_small).mean())
        if diff >= self._change_threshold:
            self._last_small = small
            return True
        return False

    def reset(self) -> None:
        self._last_small = None

    def close(self) -> None:
        try:
            self._sct.close()
        except Exception:
            pass
