"""Compose overlay: press the hotkey, type a message in English, Enter.

The translation is copied to the clipboard and the overlay hides itself —
focus returns to the game, so you just open team/all chat and Ctrl+V, Enter.
Esc closes without translating. The box can be dragged to a better spot and
remembers where you put it for the session.
"""
from __future__ import annotations

import threading

from PySide6 import QtCore, QtGui, QtWidgets

import config
from translate import make_reverse_translator

_LANGS = [
    ("Russian", "ru"), ("Ukrainian", "uk"), ("German", "de"),
    ("French", "fr"), ("Spanish", "es"), ("Italian", "it"),
    ("Portuguese", "pt"), ("Polish", "pl"), ("Turkish", "tr"),
    ("Japanese", "ja"), ("Chinese", "zh"), ("Korean", "ko"),
]

# How long the "Copied" confirmation stays on screen before the overlay
# hides itself and hands focus back to the game.
_HIDE_DELAY_MS = 900


class _Bridge(QtCore.QObject):
    """Marshals worker-thread results onto the Qt event loop."""
    done = QtCore.Signal(str)
    failed = QtCore.Signal(str)


class ComposeWindow(QtWidgets.QWidget):
    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self._cfg = cfg
        self._translators: dict = {}  # (backend, target) -> translator
        self._drag_offset: QtCore.QPoint | None = None
        self._moved = False
        self._bridge = _Bridge()
        self._bridge.done.connect(self._on_done)
        self._bridge.failed.connect(self._on_failed)

        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint
            | QtCore.Qt.Tool
            | QtCore.Qt.WindowStaysOnTopHint
        )
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)

        card = QtWidgets.QFrame(self)
        card.setObjectName("card")
        card.setStyleSheet("""
            QFrame#card {
                background: rgba(18, 22, 30, 235);
                border: 1px solid rgba(255, 255, 255, 40);
                border-radius: 10px;
            }
            QLabel { color: #d8dce4; }
            QComboBox {
                background: rgba(255, 255, 255, 18); color: #e8ecf4;
                border: 1px solid rgba(255, 255, 255, 40);
                border-radius: 4px; padding: 2px 6px;
            }
            QComboBox QAbstractItemView {
                background: #14161c; color: #e8ecf4;
                selection-background-color: #2a3242;
            }
            QLineEdit {
                background: rgba(255, 255, 255, 22); color: white;
                border: 1px solid rgba(255, 255, 255, 60);
                border-radius: 5px; padding: 6px 8px; font-size: 13px;
            }
        """)

        self._target = QtWidgets.QComboBox()
        for name, code in _LANGS:
            self._target.addItem(name, code)
        idx = self._target.findData(cfg.get("compose_target", "ru"))
        if idx >= 0:
            self._target.setCurrentIndex(idx)
        self._target.currentIndexChanged.connect(self._save_prefs)

        hint = QtWidgets.QLabel("Enter = translate + copy    Esc = close")
        hint.setStyleSheet("color: rgba(216, 220, 228, 130); font-size: 10px;")

        self._input = QtWidgets.QLineEdit()
        self._input.setPlaceholderText("Type in English…")
        self._input.returnPressed.connect(self.send)

        self._status = QtWidgets.QLabel("")
        self._status.setWordWrap(True)

        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel("To:"))
        top.addWidget(self._target)
        top.addStretch(1)
        top.addWidget(hint)

        inner = QtWidgets.QVBoxLayout(card)
        inner.setContentsMargins(12, 10, 12, 10)
        inner.addLayout(top)
        inner.addWidget(self._input)
        inner.addWidget(self._status)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        self.resize(420, 110)

    # --- public ---------------------------------------------------------
    def open(self):
        """Show and focus the input (used by the hotkey and panel button)."""
        if not self._moved:
            self._place_default()
        self._status.setText("")
        self.show()
        self.raise_()
        self.activateWindow()
        self._input.setFocus()
        self._input.selectAll()

    # --- placement / drag ----------------------------------------------
    def _place_default(self):
        """Center horizontally, lower third of the screen with the region."""
        screen = None
        r = self._cfg.get("region")
        if r:
            screen = QtGui.QGuiApplication.screenAt(
                QtCore.QPoint(r["left"] + r["width"] // 2,
                              r["top"] + r["height"] // 2)
            )
        screen = screen or QtGui.QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        self.move(
            geo.x() + (geo.width() - self.width()) // 2,
            geo.y() + int(geo.height() * 0.62),
        )

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            self._moved = True

    def mouseReleaseEvent(self, event):
        self._drag_offset = None

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)

    # --- prefs ----------------------------------------------------------
    def _save_prefs(self, *_):
        self._cfg["compose_target"] = self._target.currentData()
        config.save(self._cfg)

    # --- translate ------------------------------------------------------
    def send(self):
        text = self._input.text().strip()
        if not text:
            return
        target = self._target.currentData()
        self._status.setText("Translating…")
        threading.Thread(
            target=self._work, args=(text, target), daemon=True
        ).start()

    def _work(self, text: str, target: str):
        try:
            key = (self._cfg.get("translator_backend", "argos"), target)
            translator = self._translators.get(key)
            if translator is None:
                translator = make_reverse_translator(self._cfg, target)
                self._translators[key] = translator
            self._bridge.done.emit(translator.translate(text))
        except Exception as e:
            self._bridge.failed.emit(str(e))

    def _on_done(self, translated: str):
        QtWidgets.QApplication.clipboard().setText(translated)
        self._status.setText(f"✓ Copied:  {translated}")
        # Give the user a moment to see the result, then get out of the way
        # so the game regains focus and Ctrl+V can be pressed right away.
        QtCore.QTimer.singleShot(_HIDE_DELAY_MS, self.hide)

    def _on_failed(self, err: str):
        self._status.setText(f"Error: {err}")
