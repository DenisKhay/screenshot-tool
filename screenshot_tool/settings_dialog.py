import subprocess

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

HOTKEY_PATH = ("org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:"
               "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/screenshot-tool0/")


def _read_hotkey() -> str:
    try:
        out = subprocess.run(["gsettings", "get", HOTKEY_PATH + "binding"],
                             capture_output=True, text=True, timeout=3).stdout.strip()
        return out.strip("'\"")
    except Exception:
        return "unknown"


def _sound_enabled() -> bool:
    try:
        out = subprocess.run(["gsettings", "get", "org.gnome.desktop.sound", "event-sounds"],
                             capture_output=True, text=True, timeout=3).stdout.strip()
        return out == "true"
    except Exception:
        return False


def _set_sound(on: bool):
    subprocess.run(["gsettings", "set", "org.gnome.desktop.sound", "event-sounds",
                    "true" if on else "false"], timeout=3)


class SettingsDialog(QDialog):
    def __init__(self, settings, window, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.window = window
        self.setWindowTitle("Screenshot Tool — settings")
        self.setModal(True)
        self.setMinimumWidth(430)

        form = QFormLayout(self)
        form.setSpacing(8)

        row = QHBoxLayout()
        self.ed_dir = QLineEdit(str(settings["save_dir"]))
        b_browse = QPushButton("…")
        b_browse.setFixedWidth(34)
        b_browse.clicked.connect(self._pick_dir)
        row.addWidget(self.ed_dir)
        row.addWidget(b_browse)
        form.addRow("Save folder", row)

        self.ed_pattern = QLineEdit(str(settings["filename_pattern"]))
        form.addRow("Filename pattern (strftime)", self.ed_pattern)

        self.cmb_format = QComboBox()
        self.cmb_format.addItems(["png", "jpg"])
        self.cmb_format.setCurrentText(str(settings["format"]))
        form.addRow("Format", self.cmb_format)

        row = QHBoxLayout()
        self.sp_w = QSpinBox(); self.sp_w.setRange(200, 4000); self.sp_w.setValue(int(settings["default_width"]))
        self.sp_h = QSpinBox(); self.sp_h.setRange(120, 4000); self.sp_h.setValue(int(settings["default_height"]))
        row.addWidget(QLabel("w")); row.addWidget(self.sp_w)
        row.addWidget(QLabel("h")); row.addWidget(self.sp_h)
        form.addRow("Default frame size", row)

        self.sp_opacity = QSpinBox()
        self.sp_opacity.setRange(20, 100)
        self.sp_opacity.setValue(int(settings["default_opacity"]))
        self.sp_opacity.setSuffix("%")
        form.addRow("Default image opacity", self.sp_opacity)

        self.btn_color = QPushButton()
        self._pick_color = QColor(str(settings["color"]))
        self._sync_color_btn()
        self.btn_color.clicked.connect(self._choose_color)
        form.addRow("Stroke color", self.btn_color)

        self.sp_width = QSpinBox()
        self.sp_width.setRange(1, 30)
        self.sp_width.setValue(int(settings["stroke_width"]))
        self.sp_width.setSuffix("px")
        form.addRow("Stroke width", self.sp_width)

        self.cmb_side = QComboBox()
        self.cmb_side.addItems(["end of the toolbar (right)", "start of the toolbar (left)"])
        self.cmb_side.setCurrentIndex(0 if "right" in str(settings["capture_button_corner"]) else 1)
        form.addRow("Camera button", self.cmb_side)

        self.sp_hist = QSpinBox()
        self.sp_hist.setRange(1, 100)
        self.sp_hist.setValue(int(settings["history_limit"]))
        form.addRow("History limit (shots)", self.sp_hist)

        self.chk_sound = QCheckBox("Play the capture shutter sound")
        self.chk_sound.setChecked(_sound_enabled())
        form.addRow("", self.chk_sound)

        ed_hot = QLineEdit(_read_hotkey())
        ed_hot.setReadOnly(True)
        form.addRow("Hotkey (GNOME Settings → Keyboard)", ed_hot)

        btns = QHBoxLayout()
        b_cancel = QPushButton("Cancel")
        b_cancel.clicked.connect(self.reject)
        b_save = QPushButton("Save")
        b_save.setDefault(True)
        b_save.clicked.connect(self._save)
        btns.addStretch(1)
        btns.addWidget(b_cancel)
        btns.addWidget(b_save)
        form.addRow(btns)

    def _pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Choose save folder", self.ed_dir.text())
        if d:
            self.ed_dir.setText(d)

    def _choose_color(self):
        c = QColorDialog.getColor(self._pick_color, self, "Stroke color")
        if c.isValid():
            self._pick_color = c
            self._sync_color_btn()

    def _sync_color_btn(self):
        self.btn_color.setText(self._pick_color.name())
        self.btn_color.setStyleSheet(
            f"background: {self._pick_color.name()}; color: {'#102027' if self._pick_color.lightness() > 128 else '#eceff1'};"
            f" border: 1px solid rgba(255,255,255,60); border-radius: 4px; padding: 4px 10px;"
        )

    def _save(self):
        s = self.settings
        s["save_dir"] = self.ed_dir.text().strip() or "~/Pictures/Screenshots"
        s["filename_pattern"] = self.ed_pattern.text().strip() or "shot_%Y%m%d_%H%M%S"
        s["format"] = self.cmb_format.currentText()
        s["default_width"] = self.sp_w.value()
        s["default_height"] = self.sp_h.value()
        s["default_opacity"] = self.sp_opacity.value()
        s["color"] = self._pick_color.name()
        s["stroke_width"] = self.sp_width.value()
        s["capture_button_corner"] = "bottom-left" if self.cmb_side.currentIndex() == 1 else "bottom-right"
        s["history_limit"] = self.sp_hist.value()
        s.save()
        _set_sound(self.chk_sound.isChecked())
        if self.window is not None:
            self.window.apply_settings(s)
        self.accept()
