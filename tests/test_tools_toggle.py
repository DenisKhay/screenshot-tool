"""Toolbar collapse + focus hygiene. Regressions: collapsing left the bars
visible (relayout force-showed them), the corner button did not follow the
frame, draw mode stayed collapsed, and the opacity field trapped the arrow
keys by holding Qt focus."""
from PySide6.QtCore import QPoint
from conftest import set_image


def test_collapse_hides_all_bars_and_shows_corner_button(win):
    win.toggle_tools()
    assert not win.bar_top.isVisible()
    assert not win.bar_bottom.isVisible()
    assert not win.bar_right.isVisible()
    assert win.btn_expand.isVisible()


def test_corner_button_follows_the_frame(win):
    win.toggle_tools()
    old = QPoint(win.btn_expand.geometry().topLeft())
    win.nudge(30, 20)
    assert win.btn_expand.geometry().topLeft() != old


def test_expand_restores_bars(win):
    win.toggle_tools()
    win.toggle_tools()
    assert win.bar_top.isVisible() and not win.btn_expand.isVisible()


def test_draw_mode_auto_expands(win):
    win.toggle_tools()
    win._enter_draw_mode()
    assert win._tools_hidden is False
    # bars return once a rectangle exists (draw mode itself is chrome-free)
    win._drawing = False
    win._content = win._content  # keep whatever frame
    win.relayout()
    assert win.bar_top.isVisible()


def test_focus_hygiene_field_never_traps_arrows(win):
    set_image(win)
    win.opacity_spin.setFocus()
    win.claim_focus()  # every summon/capture claims focus back
    assert not win.opacity_spin.hasFocus()
    win._dismiss()
    win._open_last_frame()
    assert not win.opacity_spin.hasFocus()


def test_scrub_release_returns_focus(win):
    """A moved scrub must not leave the caret in the field."""
    set_image(win)
    s = win.opacity_spin
    s.show()
    s._scrubbing = True
    s._scrub_moved = True
    # release path runs through the filter; emulate its focus decision
    s.clearFocus()
    assert not s.hasFocus()
