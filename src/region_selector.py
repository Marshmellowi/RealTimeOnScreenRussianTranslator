"""Fullscreen drag-a-box region selector.

Shows a *lightly* dimmed overlay across the whole virtual desktop so the screen
stays visible, and punches a fully-clear hole in the box being dragged so you
can see exactly what you're selecting. Emits the chosen region in PHYSICAL
pixels (what mss expects) as {'left','top','width','height'}.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets


class RegionSelector(QtWidgets.QWidget):
    region_selected = QtCore.Signal(dict)

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.WindowStaysOnTopHint
            | QtCore.Qt.Tool
        )
        self.setCursor(QtCore.Qt.CrossCursor)
        # NOTE: do NOT use showFullScreen()/WindowFullScreen here — on Windows
        # that disables per-pixel alpha and forces an opaque black background.
        # A frameless window sized to the virtual desktop keeps translucency.
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self._origin: QtCore.QPoint | None = None
        self._cur = QtCore.QPoint()
        self._selecting = False

    def start(self):
        # Cover the entire virtual desktop (all monitors).
        vgeo = QtWidgets.QApplication.primaryScreen().virtualGeometry()
        self.setGeometry(vgeo)
        self.show()
        self.raise_()
        self.activateWindow()

    def _current_rect(self) -> QtCore.QRect:
        if self._origin is None:
            return QtCore.QRect()
        return QtCore.QRect(self._origin, self._cur).normalized()

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        # Light dim so the screen underneath stays clearly visible.
        p.fillRect(self.rect(), QtGui.QColor(0, 0, 0, 70))

        rect = self._current_rect()
        if rect.isValid() and rect.width() > 0 and rect.height() > 0:
            # Punch a fully transparent hole over the selection.
            p.setCompositionMode(QtGui.QPainter.CompositionMode_Clear)
            p.fillRect(rect, QtCore.Qt.transparent)
            p.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
            # Bright border + live size readout.
            p.setPen(QtGui.QPen(QtGui.QColor(0, 200, 255), 2))
            p.drawRect(rect)
            label = f"{rect.width()} x {rect.height()}"
            p.setPen(QtGui.QColor(0, 200, 255))
            f = QtGui.QFont()
            f.setPointSize(11)
            f.setBold(True)
            p.setFont(f)
            ty = rect.top() - 8 if rect.top() > 24 else rect.bottom() + 20
            p.drawText(rect.left(), ty, label)

        # Instructions, drawn at the top of the primary screen area.
        p.setPen(QtGui.QColor(255, 255, 255))
        f = QtGui.QFont()
        f.setPointSize(12)
        f.setBold(True)
        p.setFont(f)
        p.drawText(
            self.rect().adjusted(0, 24, 0, 0),
            QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop,
            "Drag a box over the text to translate.   Esc to cancel.",
        )

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key_Escape:
            self.close()

    def mousePressEvent(self, event):
        self._origin = event.position().toPoint()
        self._cur = self._origin
        self._selecting = True
        self.update()

    def mouseMoveEvent(self, event):
        if self._selecting:
            self._cur = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        self._selecting = False
        self._cur = event.position().toPoint()
        rect = self._current_rect()
        if rect.width() < 8 or rect.height() < 8:
            self.close()
            return
        # Map local logical coords -> global logical -> physical pixels.
        top_left_global = self.mapToGlobal(rect.topLeft())
        dpr = self.devicePixelRatioF()
        region = {
            "left": int(round(top_left_global.x() * dpr)),
            "top": int(round(top_left_global.y() * dpr)),
            "width": int(round(rect.width() * dpr)),
            "height": int(round(rect.height() * dpr)),
        }
        self.region_selected.emit(region)
        self.close()
