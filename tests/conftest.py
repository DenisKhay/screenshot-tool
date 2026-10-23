import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def tmp_config(tmp_path, monkeypatch):
    """Isolate config + save dir in a tmp dir (never touch real settings)."""
    cfg = tmp_path / "config"
    shots = tmp_path / "shots"
    shots.mkdir()
    cfg.mkdir()
    monkeypatch.setenv("SCREENSHOT_TOOL_CONFIG_DIR", str(cfg))
    monkeypatch.setattr("screenshot_tool.settings.CONFIG_DIR", cfg)
    monkeypatch.setattr("screenshot_tool.settings.SETTINGS_PATH", cfg / "settings.json")
    return {"config": cfg, "shots": shots}


@pytest.fixture()
def app(tmp_config):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    yield app


@pytest.fixture()
def win(app, tmp_config):
    """A fresh SnipperWindow with a ready frame."""
    from screenshot_tool.settings import Settings
    from screenshot_tool.window import SnipperWindow

    settings = Settings()
    settings["save_dir"] = str(tmp_config["shots"])
    w = SnipperWindow(settings)
    w.open_at(500, 400)
    app.processEvents()
    yield w


def set_image(win, w=400, h=200):
    """Attach a fake captured image (aspect w:h) to the window."""
    from PySide6.QtGui import QImage
    win._image = QImage(w, h, QImage.Format.Format_RGB32)
    win._image_px = (w, h)
