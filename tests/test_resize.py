"""Grip resize. Regression: the math ran in window-relative coordinates while
the sticker window re-hugs (moves) under the cursor on left/top grips — the
corner chased its own tail and right-bottom was the only working grip."""
from PySide6.QtCore import QPoint, QRect

from screenshot_tool.window import MIN_H, MIN_W
from conftest import set_image


def _start_resize(win, corner):
    set_image(win, 800, 200)  # 4:1
    win._apply_pin()
    win.show()
    win._resize_corner = corner
    win._resize_ref = QRect(win._content)
    win._resize_ref_global = win.content_global_rect()
    g = win.content_global_rect()
    return g.right(), g.bottom()  # anchor (inclusive-edge convention)


def test_sticker_tl_resize_is_anchored_and_idempotent(win):
    ax, ay = _start_resize(win, "tl")
    w0 = win.content_global_rect().width()

    win._do_resize(QPoint(ax - 100, ay - 100))
    cg = win.content_global_rect()
    assert (cg.right(), cg.bottom()) == (ax - 1, ay - 1), "anchor moved"

    # same cursor again (the window moved underneath): must be idempotent
    win._do_resize(QPoint(ax - 100, ay - 100))
    assert win.content_global_rect() == cg, "resize drifted"

    # further drag: still anchored, no accumulation
    win._do_resize(QPoint(ax - 200, ay - 200))
    cg2 = win.content_global_rect()
    assert (cg2.right(), cg2.bottom()) == (ax - 1, ay - 1)


def test_sticker_br_resize_grows(win):
    ax, ay = _start_resize(win, "br")
    left, top = win.content_global_rect().left(), win.content_global_rect().top()
    win._do_resize(QPoint(ax + 200, ay + 100))
    cg = win.content_global_rect()
    assert (cg.left(), cg.top()) == (left, top), "anchor corner moved on br grip"
    assert cg.width() == 200 or cg.height() >= MIN_H


def test_overlay_tl_resize_anchored(win):
    set_image(win, 400, 400)  # 1:1 — no MIN-floor coupling in expectations
    win._apply_pin()
    win.show()
    win._resize_corner = "tl"
    win._resize_ref = QRect(win._content)
    win._resize_ref_global = win.content_global_rect()
    ax, ay = win._resize_ref_global.right(), win._resize_ref_global.bottom()
    win._do_resize(QPoint(ax - 300, ay - 300))
    cg = win.content_global_rect()
    assert (cg.right(), cg.bottom()) == (ax - 1, ay - 1)
    assert (cg.left(), cg.top()) == (ax - 300, ay - 300)
    assert (cg.width(), cg.height()) == (300, 300)


def test_resize_respects_minimums(win):
    ax, ay = _start_resize(win, "br")
    win._do_resize(QPoint(ax - 5000, ay - 5000))  # dragging inward past min
    cg = win.content_global_rect()
    assert cg.width() == MIN_W or cg.height() == MIN_H
