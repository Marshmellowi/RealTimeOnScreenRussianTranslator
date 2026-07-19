"""Entry point: small control panel that wires together the region selector,
overlay, and translation pipeline, plus global hotkeys.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6 import QtCore, QtGui, QtWidgets

import config
import pipeline
from compose import ComposeWindow
from overlay import Overlay
from pipeline import TranslatePipeline
from region_selector import RegionSelector
from translate import backend_ready, backend_hint


class HotkeyBridge(QtCore.QObject):
    """Marshals pynput callbacks (a non-Qt thread) onto the Qt event loop."""
    toggle = QtCore.Signal()
    region = QtCore.Signal()
    compose = QtCore.Signal()


class InitBridge(QtCore.QObject):
    """Marshals launch-time background init results onto the Qt event loop."""
    status = QtCore.Signal(str)
    ready_state = QtCore.Signal(bool)


def _pynput_to_qt(hotkey: str) -> str:
    """'<ctrl>+<alt>+t' -> 'Ctrl+Alt+T' (for showing in QKeySequenceEdit)."""
    parts = []
    for p in hotkey.split("+"):
        p = p.strip()
        if p.startswith("<") and p.endswith(">"):
            name = p[1:-1]
            parts.append({"cmd": "Meta", "enter": "Return"}.get(name, name.capitalize()))
        elif p:
            parts.append(p.upper())
    return "+".join(parts)


def _qt_to_pynput(seq: str) -> str:
    """'Ctrl+Alt+T' -> '<ctrl>+<alt>+t' (pynput GlobalHotKeys format)."""
    parts = []
    for p in seq.split("+"):
        p = p.strip()
        if not p:
            continue
        low = p.lower()
        if low in ("ctrl", "alt", "shift"):
            parts.append(f"<{low}>")
        elif low == "meta":
            parts.append("<cmd>")
        elif len(p) == 1:
            parts.append(low)
        else:
            # Named keys: F1..F24, Space, Return, Esc, ...
            parts.append(f"<{'enter' if low == 'return' else low}>")
    return "+".join(parts)


class SettingsDialog(QtWidgets.QDialog):
    """Choose the translation provider and its settings."""

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        form = QtWidgets.QFormLayout(self)

        self.provider = QtWidgets.QComboBox()
        self.provider.addItem("DeepL (API)", "deepl")
        self.provider.addItem("Ollama (local, free)", "ollama")
        self.provider.addItem("NLLB (offline)", "nllb")
        self.provider.addItem("Argos (offline)", "argos")
        idx = self.provider.findData(cfg.get("translator_backend", "deepl"))
        if idx >= 0:
            self.provider.setCurrentIndex(idx)
        form.addRow("Provider:", self.provider)

        self.source_lang = QtWidgets.QLineEdit(cfg.get("source_lang", "auto"))
        self.source_lang.setPlaceholderText(
            "auto (DeepL/Ollama detect; NLLB/Argos need a code like ru, ja, de)"
        )
        form.addRow("Source lang:", self.source_lang)

        self.ocr_engine = QtWidgets.QComboBox()
        self.ocr_engine.addItem("RapidOCR (CPU, fast, no VRAM)", "rapidocr")
        self.ocr_engine.addItem("EasyOCR (GPU)", "easyocr")
        idx = self.ocr_engine.findData(cfg.get("ocr_engine", "easyocr"))
        if idx >= 0:
            self.ocr_engine.setCurrentIndex(idx)
        form.addRow("OCR engine:", self.ocr_engine)

        self.ocr_langs = QtWidgets.QLineEdit(
            ",".join(cfg.get("ocr_languages", ["ru", "en"]))
        )
        self.ocr_langs.setPlaceholderText(
            "e.g. ru,en or ja,en (EasyOCR: CJK pairs only with en)"
        )
        form.addRow("OCR languages:", self.ocr_langs)

        self.deepl_key = QtWidgets.QLineEdit(cfg.get("deepl_api_key", ""))
        self.deepl_key.setPlaceholderText("blank = use DEEPL_API_KEY env var")
        form.addRow("DeepL key:", self.deepl_key)

        self.ollama_host = QtWidgets.QLineEdit(
            cfg.get("ollama_host", "http://localhost:11434")
        )
        form.addRow("Ollama host:", self.ollama_host)
        self.ollama_model = QtWidgets.QLineEdit(
            cfg.get("ollama_model", "translategemma:4b")
        )
        form.addRow("Ollama model:", self.ollama_model)

        # Hotkey remapping: click a field and press the new combination.
        self._hotkey_edits: dict[str, QtWidgets.QKeySequenceEdit] = {}
        for cfg_key, label, default in (
            ("hotkey_toggle", "Hotkey: translate on/off", "<ctrl>+<alt>+t"),
            ("hotkey_region", "Hotkey: select region", "<ctrl>+<alt>+r"),
            ("hotkey_compose", "Hotkey: compose message", "<ctrl>+<alt>+m"),
        ):
            edit = QtWidgets.QKeySequenceEdit(
                QtGui.QKeySequence(_pynput_to_qt(cfg.get(cfg_key, default)))
            )
            edit.setMaximumSequenceLength(1)
            edit.setClearButtonEnabled(True)
            self._hotkey_edits[cfg_key] = edit
            form.addRow(label, edit)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def apply_to(self, cfg: dict):
        cfg["translator_backend"] = self.provider.currentData()
        cfg["deepl_api_key"] = self.deepl_key.text().strip()
        cfg["ollama_host"] = self.ollama_host.text().strip()
        cfg["ollama_model"] = self.ollama_model.text().strip()
        cfg["source_lang"] = self.source_lang.text().strip().lower() or "auto"
        cfg["ocr_engine"] = self.ocr_engine.currentData()
        langs = [l.strip().lower() for l in self.ocr_langs.text().split(",")]
        cfg["ocr_languages"] = [l for l in langs if l] or ["ru", "en"]
        for cfg_key, edit in self._hotkey_edits.items():
            seq = edit.keySequence().toString()
            if not seq:
                continue  # cleared/empty: keep the current binding
            hotkey = _qt_to_pynput(seq)
            try:
                from pynput import keyboard

                keyboard.HotKey.parse(hotkey)
            except Exception:
                continue  # combination pynput can't register: keep the old one
            cfg[cfg_key] = hotkey


class ControlPanel(QtWidgets.QWidget):
    def __init__(self, cfg: dict):
        super().__init__()
        self._cfg = cfg
        self._pipeline: TranslatePipeline | None = None
        self._overlay = Overlay(cfg)
        self._selector: RegionSelector | None = None
        self._compose: ComposeWindow | None = None
        self._active = False

        self.setWindowTitle("Screen Translator")
        self.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint, True)

        self._status = QtWidgets.QLabel("Starting…")
        self._status.setWordWrap(True)
        self._region_lbl = QtWidgets.QLabel(self._region_text())
        self._btn_region = QtWidgets.QPushButton("Select Region")
        self._btn_toggle = QtWidgets.QPushButton("Start")
        self._btn_compose = QtWidgets.QPushButton("Compose…")
        self._btn_settings = QtWidgets.QPushButton("Settings…")
        self._btn_region.clicked.connect(self.choose_region)
        self._btn_toggle.clicked.connect(self.toggle)
        self._btn_compose.clicked.connect(self.open_compose)
        self._btn_settings.clicked.connect(self.open_settings)

        # Opacity slider (live).
        init_op = int(round(self._cfg.get("overlay_opacity", 1.0) * 100))
        self._opacity_lbl = QtWidgets.QLabel(f"Opacity: {init_op}%")
        self._opacity = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._opacity.setRange(15, 100)
        self._opacity.setValue(init_op)
        self._opacity.valueChanged.connect(self._on_opacity)
        self._opacity.sliderReleased.connect(lambda: config.save(self._cfg))

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self._region_lbl)
        layout.addWidget(self._btn_region)
        layout.addWidget(self._btn_toggle)
        layout.addWidget(self._btn_compose)
        layout.addWidget(self._btn_settings)
        layout.addWidget(self._opacity_lbl)
        layout.addWidget(self._opacity)
        layout.addWidget(self._status)
        self.resize(280, 200)

        self._setup_hotkeys()

        # Backend readiness + engine preload run off the GUI thread: the argos
        # import alone takes seconds (tens, cold), and the Ollama probe can
        # block up to 3s. The window shows immediately; Start becomes instant
        # once the warm-up finishes.
        self._init_bridge = InitBridge()
        self._init_bridge.status.connect(self._on_init_status)
        self._init_bridge.ready_state.connect(self._on_backend_checked)
        threading.Thread(target=self._background_init, daemon=True).start()

    # --- launch-time background init ------------------------------------
    def _background_init(self):
        ready = backend_ready(self._cfg)
        self._init_bridge.ready_state.emit(ready)
        if not ready:
            self._init_bridge.status.emit(backend_hint(self._cfg))
            return
        pipeline.warm_up(self._cfg, status=self._init_bridge.status.emit)

    def _on_init_status(self, msg: str):
        # Don't stomp live pipeline status if the user already hit Start.
        if not self._active:
            self._status.setText(msg)

    def _on_backend_checked(self, ready: bool):
        if not ready and not self._active:
            self._btn_toggle.setEnabled(False)

    # --- settings -------------------------------------------------------
    def open_settings(self):
        dlg = SettingsDialog(self._cfg, self)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        dlg.apply_to(self._cfg)
        config.save(self._cfg)
        self._setup_hotkeys()  # re-register in case hotkeys were remapped
        provider = self._cfg.get("translator_backend")
        ready = backend_ready(self._cfg)
        self._btn_toggle.setEnabled(ready or self._active)
        if not ready:
            self._status.setText(backend_hint(self._cfg))
        else:
            self._status.setText(f"Provider set to: {provider}")
        # Rebuild the running pipeline so the new provider takes effect now.
        if self._active:
            self.stop()
            self.start()

    # --- opacity --------------------------------------------------------
    def _on_opacity(self, value: int):
        val = value / 100.0
        self._cfg["overlay_opacity"] = val
        self._opacity_lbl.setText(f"Opacity: {value}%")
        self._overlay.setWindowOpacity(val)

    # --- compose --------------------------------------------------------
    def open_compose(self):
        if self._compose is None:
            self._compose = ComposeWindow(self._cfg)
        self._compose.open()

    # --- region ---------------------------------------------------------
    def _region_text(self) -> str:
        r = self._cfg.get("region")
        if not r:
            return "Region: not set"
        return f"Region: {r['width']}x{r['height']} @ ({r['left']},{r['top']})"

    def choose_region(self):
        self._selector = RegionSelector()
        self._selector.region_selected.connect(self._on_region)
        self._selector.start()

    def _on_region(self, region: dict):
        self._cfg["region"] = region
        config.save(self._cfg)
        self._region_lbl.setText(self._region_text())
        self._overlay.set_region(region)
        if self._pipeline:
            self._pipeline.set_region(region)

    # --- start/stop -----------------------------------------------------
    def toggle(self):
        if self._active:
            self.stop()
        else:
            self.start()

    def start(self):
        if not self._cfg.get("region"):
            self._status.setText("Pick a region first.")
            return
        if not backend_ready(self._cfg):
            self._status.setText(backend_hint(self._cfg))
            return
        self._active = True
        self._btn_toggle.setText("Stop")
        self._overlay.set_region(self._cfg.get("region"))
        self._overlay.show()
        self._pipeline = TranslatePipeline(self._cfg)
        self._pipeline.results.connect(self._overlay.set_boxes)
        self._pipeline.status.connect(self._status.setText)
        self._pipeline.start()

    def stop(self):
        self._active = False
        self._btn_toggle.setText("Start")
        if self._pipeline:
            self._pipeline.stop()
            self._pipeline.wait(3000)
            self._pipeline = None
        self._overlay.clear()
        self._overlay.hide()
        self._status.setText("Stopped.")

    # --- hotkeys --------------------------------------------------------
    def _setup_hotkeys(self):
        # Re-entrant: called again after Settings to apply remapped hotkeys.
        if getattr(self, "_hotkeys", None):
            try:
                self._hotkeys.stop()
            except Exception:
                pass
            self._hotkeys = None
        if not hasattr(self, "_bridge"):
            self._bridge = HotkeyBridge()
            self._bridge.toggle.connect(self.toggle)
            self._bridge.region.connect(self.choose_region)
            self._bridge.compose.connect(self.open_compose)
        try:
            from pynput import keyboard

            self._hotkeys = keyboard.GlobalHotKeys({
                self._cfg.get("hotkey_toggle", "<ctrl>+<alt>+t"):
                    self._bridge.toggle.emit,
                self._cfg.get("hotkey_region", "<ctrl>+<alt>+r"):
                    self._bridge.region.emit,
                self._cfg.get("hotkey_compose", "<ctrl>+<alt>+m"):
                    self._bridge.compose.emit,
            })
            self._hotkeys.daemon = True
            self._hotkeys.start()
        except Exception as e:
            print(f"[hotkeys] disabled: {e}")

    def closeEvent(self, event):
        self.stop()
        self._overlay.close()
        if self._compose:
            self._compose.close()
        super().closeEvent(event)


def main():
    QtWidgets.QApplication.setHighDpiScaleFactorRoundingPolicy(
        QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QtWidgets.QApplication(sys.argv)
    cfg = config.load()
    panel = ControlPanel(cfg)
    panel.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
