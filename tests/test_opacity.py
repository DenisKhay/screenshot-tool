"""Opacity scrubber: relative accumulation, screen-edge wrap, 0..100 range,
and the wiring into _opacity. Regressions: scrub stopped at screen edges,
was 1px=1% twitchy, and the 20% floor was unrequested."""
from PySide6.QtCore import QPoint


def test_scrub_relative_accumulation(win):
    s = win.opacity_spin
    s.setValue(20)
    s._scrubbing = True
    s._scrub_moved = False
    s._scrub_ref = QPoint(400, 400)
    s._scrub_acc = 0.0
    cur = QPoint(400, 400)
    for _ in range(36):                      # 36px = 1 unit per step
        s._scrub_step(QPoint(cur.x() + 36, cur.y()))
        cur = QPoint(*s._scrub_ref.toTuple())
    assert s.value() == 56
    for _ in range(10):                      # vertical up also increases
        s._scrub_step(QPoint(cur.x(), cur.y() - 36))
        cur = QPoint(*s._scrub_ref.toTuple())
    assert s.value() == 66


def test_scrub_wraps_at_screen_edge(win):
    s = win.opacity_spin
    g = s.screen().geometry()
    s.setValue(50)
    s._scrubbing = True
    s._scrub_moved = False
    s._scrub_ref = QPoint(g.x() + g.width() - 2, 300)
    s._scrub_acc = 0.0
    s._scrub_step(QPoint(g.x() + g.width() + 50, 300))  # pull past the edge
    assert s._scrub_ref.x() < g.center().x(), "cursor did not wrap"
    assert s.value() > 50, "wrapped drag must keep changing the value"
    cur = QPoint(*s._scrub_ref.toTuple())
    s._scrub_step(QPoint(cur.x() + 36, cur.y()))        # and keeps scrubbing
    assert s.value() > 51


def test_scrub_clamps_at_bounds(win):
    s = win.opacity_spin
    g = s.screen().geometry()
    s.setValue(100)
    s._scrubbing = True
    s._scrub_moved = False
    s._scrub_ref = QPoint(400, 400)
    s._scrub_acc = 0.0
    cur = QPoint(400, 400)
    for _ in range(200):                     # pull left far past the floor
        s._scrub_step(QPoint(cur.x() - 36, cur.y()))
        cur = QPoint(*s._scrub_ref.toTuple())
    assert s.value() == 0


def test_scrub_wires_opacity(win):
    s = win.opacity_spin
    s.setValue(0)
    assert win._opacity == 0.0
    s.setValue(37)
    assert win._opacity == 0.37
    s.setValue(100)
    assert win._opacity == 1.0
    s.setValue(150)
    assert s.value() == 100
