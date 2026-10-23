"""Pin / window-mode state machine. Regression: mode switches used to lose
the frame position, and pin did not change anything at all."""
from PySide6.QtCore import QPoint, QRect
from conftest import set_image


def test_fresh_capture_starts_unpinned(win):
    set_image(win)
    win._apply_pin()
    win.show()
    assert win._bypass is False, "the shot lives in the managed hug window"
    assert win._pinned is False


def test_mode_switch_preserves_frame_position(win):
    set_image(win)
    win._apply_pin()
    g0 = win.content_global_rect().topLeft()
    win.toggle_pin()  # pin -> above everything
    assert win._pinned is True
    assert win.content_global_rect().topLeft() == g0, "frame jumped on pin"
    win.toggle_pin()  # unpin -> normal stacking
    assert win._pinned is False
    assert win.content_global_rect().topLeft() == g0, "frame jumped on unpin"


def test_pin_toggles_the_above_request(win, monkeypatch):
    import screenshot_tool.window as W
    calls = []
    monkeypatch.setattr(W, "set_above_state", lambda w, on: calls.append(on))
    set_image(win)
    win._apply_pin()
    assert calls[-1] is False, "unpinned must not sit in the above layer"
    win.toggle_pin()
    assert calls[-1] is True, "pinned must request the above layer"


def test_unpinned_shot_keeps_normal_stacking_placement(win):
    """Unpinned = managed window: the app lets it travel (mutter applies its
    own on-screen clamp in production). Arrows still move it step by step."""
    set_image(win)
    win._apply_pin()
    win.show()
    g0 = win.content_global_rect().topLeft()
    for _ in range(50):
        win.nudge(-1, 0)
    assert win.content_global_rect().x() == g0.x() - 50


def test_offscreen_selection_still_possible_pre_capture(win):
    """The draw overlay stays OR: a selection frame may cross the edges."""
    win._drawing = False
    win._content = QRect(-60, -60, 400, 300)
    win.nudge(-10, -10)
    assert (win._content.x(), win._content.y()) == (-70, -70)


def test_pin_toggle_in_draw_mode_is_state_only(win):
    # no captured image yet: pin must not switch the window mode mid-draw
    win.toggle_pin()
    assert win._bypass is True
    win.toggle_pin()
    assert win._bypass is True


def test_bars_replaced_after_mode_switch(win):
    """Regression: mode entries skipped _place_bars — the bars kept their
    overlay coordinates and floated ON the captured shot in sticker mode."""
    set_image(win)
    win._apply_pin()
    win.show()
    assert win.bar_top.geometry().bottom() <= win._content.top()
    assert win.bar_top.geometry().contains(
        win.btn_pin.geometry().translated(win.bar_top.geometry().topLeft()))
    win.toggle_pin()  # pinned sticker
    assert win.bar_top.geometry().bottom() <= win._content.top(), \
        "bars floated on the shot after pin"
    win.toggle_pin()  # unpinned overlay
    assert win.bar_top.geometry().bottom() <= win._content.top(), \
        "bars misplaced after unpin"


def test_clear_history_does_not_resurrect_files(win, tmp_config):
    set_image(win)
    entry = {"image": None, "base": None, "imprints": [], "files": [],
             "path": None, "logical": None}
    f = tmp_config["shots"] / "keepme.png"
    f.write_bytes(b"x")
    entry["files"] = [str(f)]
    win.history = [entry]
    win.clear_history()
    assert win.history == []
    assert not f.exists(), "trash must delete the files or restore resurrects them"


def test_history_restored_from_save_dir(win, tmp_config):
    import os
    from PySide6.QtGui import QImage
    for i in range(3):
        p = tmp_config["shots"] / f"shot_{i}.png"
        img = QImage(60 + i, 40, QImage.Format.Format_RGB32)
        img.save(str(p))
        os.utime(p, (1000 + i, 1000 + i))  # distinct mtimes: shot_2 newest
    win._restore_history()
    assert len(win.history) == 3
    assert win._hist_idx == 2  # newest last
    assert win.history[-1]["path"].endswith("shot_2.png")
