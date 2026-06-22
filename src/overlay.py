"""Transparent, always-on-top, click-through overlay.

Render modes (config `overlay_mode`):
  - "panel" (default): a STATIONARY box over the capture region showing the
    latest translated lines as a de-duplicated feed (newest at the bottom).
    Best for scrolling chat — never strays because it doesn't track word
    positions.
  - "subtitle": consolidate current lines into one block anchored to the
    region edge.
  - "inline": draw each translated line over the spot OCR found it. Only stable
    for a single static line; jitters/strays on dynamic text.
"""
from __future__ import annotations

import difflib
import re
import sys
import time

from PySide6 import QtCore, QtGui, QtWidgets

_NORM = re.compile(r"[^a-z0-9а-яё]+")


class Overlay(QtWidgets.QWidget):
    def __init__(self, cfg: dict):
        super().__init__()
        self._cfg = cfg
        self._mode = cfg.get("overlay_mode", "panel")
        self._anchor = cfg.get("subtitle_anchor", "bottom")
        self._boxes: list[tuple[int, int, int, int, str]] = []  # logical px + text
        self._region_logical: QtCore.QRect | None = None
        # Feed state for panel mode (parallel lists: text, normalized, timestamp).
        self._feed: list[str] = []
        self._feed_norm: list[str] = []
        self._feed_time: list[float] = []
        self._feed_max = int(cfg.get("feed_max_lines", 18))
        self._feed_ttl = float(cfg.get("feed_ttl_seconds", 20.0))

        flags = (
            QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.Tool
        )
        if cfg.get("click_through", True):
            flags |= QtCore.Qt.WindowTransparentForInput
        self.setWindowFlags(flags)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating)
        self.setWindowOpacity(cfg.get("overlay_opacity", 1.0))

        vgeo = QtWidgets.QApplication.primaryScreen().virtualGeometry()
        self.setGeometry(vgeo)

        # Periodic tick so the feed can expire (and the panel disappear) even
        # when no new snapshot arrives.
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

    def _tick(self):
        if self._mode == "panel" and self._prune():
            self.update()

    def _prune(self) -> bool:
        """Drop feed lines older than the TTL. Returns True if anything changed."""
        if not self._feed_time:
            return False
        now = time.monotonic()
        keep = [i for i, ts in enumerate(self._feed_time) if now - ts <= self._feed_ttl]
        if len(keep) == len(self._feed):
            return False
        self._feed = [self._feed[i] for i in keep]
        self._feed_norm = [self._feed_norm[i] for i in keep]
        self._feed_time = [self._feed_time[i] for i in keep]
        return True

    def showEvent(self, event):
        super().showEvent(event)
        # Exclude the overlay from screen capture so our own translation isn't
        # screenshotted, re-OCR'd and re-translated (a feedback loop).
        self._exclude_from_capture()

    def _exclude_from_capture(self):
        if sys.platform != "win32":
            return
        try:
            import ctypes

            WDA_EXCLUDEFROMCAPTURE = 0x00000011
            hwnd = int(self.winId())
            ok = ctypes.windll.user32.SetWindowDisplayAffinity(
                hwnd, WDA_EXCLUDEFROMCAPTURE
            )
            if not ok:
                print("[overlay] SetWindowDisplayAffinity failed "
                      "(needs Windows 10 2004+); overlay may be self-captured.")
        except Exception as e:
            print(f"[overlay] exclude-from-capture error: {e}")

    def set_region(self, region: dict | None):
        """Store the capture region (physical px) as a widget-local logical rect."""
        if not region:
            self._region_logical = None
            return
        dpr = self.devicePixelRatioF() or 1.0
        vgeo = self.geometry()
        self._region_logical = QtCore.QRect(
            int(round(region["left"] / dpr)) - vgeo.left(),
            int(round(region["top"] / dpr)) - vgeo.top(),
            int(round(region["width"] / dpr)),
            int(round(region["height"] / dpr)),
        )

    @QtCore.Slot(list)
    def set_boxes(self, boxes):
        """boxes: [(x, y, w, h, text), ...] in absolute PHYSICAL pixels."""
        dpr = self.devicePixelRatioF() or 1.0
        vgeo = self.geometry()
        conv = []
        for x, y, w, h, text in boxes:
            lx = int(round(x / dpr)) - vgeo.left()
            ly = int(round(y / dpr)) - vgeo.top()
            conv.append((lx, ly, int(round(w / dpr)), int(round(h / dpr)), text))
        self._boxes = conv
        if self._mode == "panel":
            self._update_feed(boxes)
        self.update()

    def clear(self):
        self._boxes = []
        self._feed = []
        self._feed_norm = []
        self._feed_time = []
        self.update()

    # --- feed management (panel mode) ----------------------------------
    @staticmethod
    def _norm(s: str) -> str:
        return _NORM.sub(" ", s.lower()).strip()

    def _is_dup(self, n: str) -> bool:
        for e in self._feed_norm:
            if n == e or difflib.SequenceMatcher(None, n, e).ratio() > 0.85:
                return True
        return False

    def _update_feed(self, boxes):
        now = time.monotonic()
        # Lines in reading order (top -> bottom by original y).
        for _x, _y, _w, _h, text in sorted(boxes, key=lambda b: b[1]):
            text = text.strip()
            n = self._norm(text)
            if len(n) < 2 or self._is_dup(n):
                continue
            self._feed.append(text)
            self._feed_norm.append(n)
            self._feed_time.append(now)
        if len(self._feed) > self._feed_max:
            self._feed = self._feed[-self._feed_max:]
            self._feed_norm = self._feed_norm[-self._feed_max:]
            self._feed_time = self._feed_time[-self._feed_max:]
        self._prune()

    # --- painting ------------------------------------------------------
    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        # Erase the previous frame; a translucent overlay won't auto-clear.
        p.setCompositionMode(QtGui.QPainter.CompositionMode_Source)
        p.fillRect(self.rect(), QtCore.Qt.transparent)
        p.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
        p.setRenderHint(QtGui.QPainter.Antialiasing)

        bg = QtGui.QColor(*self._cfg.get("overlay_bg", [0, 0, 0, 235]))
        fg = QtGui.QColor(*self._cfg.get("overlay_fg", [255, 255, 255, 255]))
        font = QtGui.QFont()
        font.setPointSize(self._cfg.get("font_size", 13))
        font.setBold(True)
        p.setFont(font)

        if self._mode == "panel":
            self._paint_panel(p, bg, fg)
        elif self._mode == "subtitle" and self._region_logical is not None:
            if self._boxes:
                self._paint_subtitle(p, bg, fg)
        else:
            if self._boxes:
                self._paint_inline(p, bg, fg)

    def _paint_panel(self, p, bg, fg):
        if not self._feed or self._region_logical is None:
            return
        region = self._region_logical
        pad = 8
        p.setBrush(bg)
        p.setPen(QtCore.Qt.NoPen)
        p.drawRoundedRect(region, 8, 8)
        text = "\n".join(self._feed)
        inner = region.adjusted(pad, pad, -pad, -pad)
        flags = QtCore.Qt.TextWordWrap | QtCore.Qt.AlignLeft | QtCore.Qt.AlignBottom
        # Shadow first, then text, so it stays legible over a translucent panel.
        p.setPen(QtGui.QColor(0, 0, 0, 230))
        p.drawText(inner.translated(1, 1), flags, text)
        p.setPen(fg)
        p.drawText(inner, flags, text)

    def _paint_subtitle(self, p, bg, fg):
        fm = p.fontMetrics()
        region = self._region_logical
        pad = 10
        lines = [t for (_x, _y, _w, _h, t) in sorted(self._boxes, key=lambda b: b[1])]
        text = "\n".join(lines).strip()
        if not text:
            return
        max_w = max(region.width() - 2 * pad, 60)
        flags = QtCore.Qt.TextWordWrap | QtCore.Qt.AlignHCenter
        text_rect = fm.boundingRect(QtCore.QRect(0, 0, max_w, 10_000), flags, text)
        box_w = text_rect.width() + 2 * pad
        box_h = text_rect.height() + 2 * pad
        x = region.left() + (region.width() - box_w) // 2
        y = region.top() + pad if self._anchor == "top" else region.bottom() - box_h - pad
        box = QtCore.QRect(x, y, box_w, box_h)
        p.setBrush(bg)
        p.setPen(QtCore.Qt.NoPen)
        p.drawRoundedRect(box, 8, 8)
        p.setPen(fg)
        p.drawText(box.adjusted(pad, pad, -pad, -pad), flags, text)

    def _paint_inline(self, p, bg, fg):
        base_size = self._cfg.get("font_size", 13)
        flags = QtCore.Qt.TextWordWrap | QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter
        font = QtGui.QFont(p.font())
        for x, y, w, h, text in self._boxes:
            box_w = max(w, 24)
            size = base_size
            while True:
                font.setPointSize(size)
                fm = QtGui.QFontMetrics(font)
                tr = fm.boundingRect(
                    QtCore.QRect(0, 0, box_w, 10_000),
                    QtCore.Qt.TextWordWrap | QtCore.Qt.AlignHCenter,
                    text,
                )
                if tr.height() <= h + 2 or size <= 9:
                    break
                size -= 1
            p.setFont(font)
            rect = QtCore.QRect(x, y, box_w, max(h, tr.height() + 4))
            p.setBrush(bg)
            p.setPen(QtCore.Qt.NoPen)
            p.drawRoundedRect(rect, 4, 4)
            p.setPen(fg)
            p.drawText(rect, flags, text)
