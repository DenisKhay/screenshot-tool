"""Async capture guard + persistence of runtime settings."""
from pathlib import Path


def test_settings_persisted_on_change(win, tmp_config):
    win._set_width(11)
    win._set_text_size(31)
    win._set_color("#00ff00")
    win._on_opacity(55)
    import json
    data = json.loads((tmp_config["config"] / "settings.json").read_text())
    assert data["stroke_width"] == 11
    assert data["text_size"] == 31
    assert data["color"] == "#00ff00"
    assert data["default_opacity"] == 55


def test_settings_reloaded_on_restart(tmp_config, app):
    """A new window (what a restart creates) must see the persisted values."""
    from screenshot_tool.settings import Settings
    from screenshot_tool.window import SnipperWindow

    w1 = SnipperWindow(Settings())
    w1._set_width(13)
    w1._set_text_size(33)
    w1._set_color("#123456")
    w1._on_opacity(60)

    w2 = SnipperWindow(Settings())
    assert w2._width == 13
    assert w2._text_size == 33
    assert w2._color.name() == "#123456"
    assert w2._opacity == 0.6


def test_capture_stale_generation_drops(tmp_config, app, monkeypatch):
    """A capture result arriving after a new session opened must be dropped,
    not stomp the session."""
    from screenshot_tool.settings import Settings
    from screenshot_tool.window import SnipperWindow
    from PySide6.QtGui import QImage

    w = SnipperWindow(Settings())
    w.open_at(300, 300)
    w.do_capture()                     # gen -> 1, worker would be in flight
    gen_at_start = w._session_gen
    w.open_draw()                      # session changed while capture pending
    assert w._session_gen > gen_at_start
    fake = QImage(50, 40, QImage.Format.Format_RGB32)
    w._capture_continuation(fake, "/nonexistent/src.png", gen_at_start)
    assert w._image is None, "stale capture stomped the new session"
    assert w._busy is False


def test_capture_error_path_restores_window(tmp_config, app):
    from screenshot_tool.settings import Settings
    from screenshot_tool.window import SnipperWindow

    w = SnipperWindow(Settings())
    w.open_at(300, 300)
    w.do_capture()                     # offscreen portal fails -> error path
    w._capture_continuation(None, "portal timeout", w._session_gen)
    assert w._busy is False
    assert w.isVisible(), "failed capture left the window hidden"
    assert "capture failed" in w.size_label.text()
