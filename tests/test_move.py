"""Movement: drag/nudge clamps. The frame may cross desktop edges but a
grabbable strip (EDGE_MARGIN) must stay reachable. Regression: hard clamps
kept the frame inside the desktop; arrows dead in sticker mode."""
from PySide6.QtCore import QPoint

from screenshot_tool.window import EDGE_MARGIN
from conftest import set_image


def test_nudge_crosses_left_top_edge_overlay(win):
    for _ in range(2000):
        win.nudge(-1, -1)
    c = win._content
    assert c.x() == EDGE_MARGIN - c.width(), c.x()
    assert c.y() == EDGE_MARGIN - c.height(), c.y()


def test_nudge_crosses_right_bottom_edge_overlay(win):
    for _ in range(6000):
        win.nudge(1, 1)
    c = win._content
    assert c.x() == win._desktop.width() - EDGE_MARGIN
    assert c.y() == win._desktop.height() - EDGE_MARGIN


def test_nudge_crosses_edges_sticker_mode(win):
    set_image(win)
    win._apply_pin()
    win.show()
    g0 = win.content_global_rect().topLeft()
    for _ in range(100):
        win.nudge(-1, 0)
    g1 = win.content_global_rect().topLeft()
    assert g1.x() == g0.x() - 100, (g0, g1)
    assert g1.y() == g0.y()


def test_nudge_ignores_mid_drag_and_resize(win):
    win._drag_offset = QPoint(1, 1)
    c0 = QPoint(win._content.topLeft())
    win.nudge(-5, -5)
    assert win._content.topLeft() == c0
    win._drag_offset = None
    win._resize_corner = "br"
    win.nudge(-5, -5)
    assert win._content.topLeft() == c0
    win._resize_corner = None
