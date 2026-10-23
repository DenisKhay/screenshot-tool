"""Pin / window-mode state machine. Regression: mode switches used to lose
the frame position, and pin did not change anything at all."""
from PySide6.QtCore import QPoint
from conftest import set_image


def test_fresh_capture_starts_unpinned(win):
    set_image(win)
    win._apply_pin()
    win.show()
    assert win._bypass is True, "fresh shot must live in the OR overlay"
    assert win._pinned is False


def test_mode_switch_preserves_frame_position(win):
    set_image(win)
    win._apply_pin()
    g0 = win.content_global_rect().topLeft()
    win.toggle_pin()  # pin -> managed sticker
    assert win._bypass is False
    assert win.content_global_rect().topLeft() == g0, "frame jumped on pin"
    win.toggle_pin()  # unpin -> overlay
    assert win._bypass is True
    assert win.content_global_rect().topLeft() == g0, "frame jumped on unpin"


def test_unpinned_shot_can_go_offscreen(win):
    set_image(win)
    win._apply_pin()
    win.show()
    win.nudge(-2000, 0)
    assert win.content_global_rect().x() < 0, "unpinned shot must cross edges"
    win.toggle_pin()  # pin: managed window, mutter keeps it on-screen
    assert win._bypass is False
    win.toggle_pin()  # unpin again: the off-screen placement is restored
    assert win._bypass is True


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
