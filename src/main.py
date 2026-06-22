"""Entry point: small control panel that wires together the region selector,
overlay, and translation pipeline, plus global hotkeys.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6 import QtCore, QtWidgets

import config
from overlay import Overlay
from pipeline import TranslatePipeline
from region_selector import RegionSelector
from translate import backend_ready, backend_hint


class HotkeyBridge(QtCore.QObject):
    """Marshals pynput callbacks (a non-Qt thread) onto the Qt event loop."""
    toggle = QtCore.Signal()
    region = QtCore.Signal()


class SettingsDialog(QtWidgets.QDialog):
    """Choose the translation provider and its settings."""

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        form = QtWidgets.QFormLayout(self)

        self.provider = QtWidgets.QComboBox()
        self.provider.addItem("DeepL (API)", "deepl")
        self.provider.addItem("Ollama (local, free)", "ollama")
        self.provider.addItem("Argos (offline)", "argos")
        idx = self.provider.findData(cfg.get("translator_backend", "deepl"))
        if idx >= 0:
            self.provider.setCurrentIndex(idx)
        form.addRow("Provider:", self.provider)

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


class ControlPanel(QtWidgets.QWidget):
    def __init__(self, cfg: dict):
        super().__init__()
        self._cfg = cfg
        self._pipeline: TranslatePipeline | None = None
        self._overlay = Overlay(cfg)
        self._selector: RegionSelector | None = None
        self._active = False

        self.setWindowTitle("RU Translator")
        self.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint, True)

        self._status = QtWidgets.QLabel("Idle.")
        self._status.setWordWrap(True)
        self._region_lbl = QtWidgets.QLabel(self._region_text())
        self._btn_region = QtWidgets.QPushButton("Select Region")
        self._btn_toggle = QtWidgets.QPushButton("Start")
        self._btn_settings = QtWidgets.QPushButton("Settings…")
        self._btn_region.clicked.connect(self.choose_region)
        self._btn_toggle.clicked.connect(self.toggle)
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
        layout.addWidget(self._btn_settings)
        layout.addWidget(self._opacity_lbl)
        layout.addWidget(self._opacity)
        layout.addWidget(self._status)
        self.resize(280, 200)

        if not backend_ready(self._cfg):
            self._status.setText(backend_hint(self._cfg))
            self._btn_toggle.setEnabled(False)

        self._setup_hotkeys()

    # --- settings -------------------------------------------------------
    def open_settings(self):
        dlg = SettingsDialog(self._cfg, self)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        dlg.apply_to(self._cfg)
        config.save(self._cfg)
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
        self._bridge = HotkeyBridge()
        self._bridge.toggle.connect(self.toggle)
        self._bridge.region.connect(self.choose_region)
        try:
            from pynput import keyboard

            self._hotkeys = keyboard.GlobalHotKeys({
                self._cfg.get("hotkey_toggle", "<ctrl>+<alt>+t"):
                    self._bridge.toggle.emit,
                self._cfg.get("hotkey_region", "<ctrl>+<alt>+r"):
                    self._bridge.region.emit,
            })
            self._hotkeys.daemon = True
            self._hotkeys.start()
        except Exception as e:
            print(f"[hotkeys] disabled: {e}")

    def closeEvent(self, event):
        self.stop()
        self._overlay.close()
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
