import ctypes
import logging
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import (
    QColor,
    QCursor,
    QGuiApplication,
    QImage,
    QKeySequence,
    QPainter,
    QPen,
    QShortcut,
    QRegion,
    QFontMetrics,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QColorDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QSlider,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from . import capture, icons
from .settings import Settings
from .shapes import ArrowShape, FilledRectShape, PenShape, RectShape, TextShape, render_shapes

log = logging.getLogger("screenshot_tool")

ACCENT = "#9fc9f0"        # blueprint light blue
INK = "#cfe4f7"           # hint text
CHROME_FG = INK             # legacy: toolbar text color
PANEL_BG = "rgba(13, 43, 82, 235)"
PANEL_BORDER = "#2f6ea5"
CAPTURE_ACCENT = "#ffd54f"  # blueprint yellow accent
TB_H = 44   # toolbar row height: 34px camera + 2x(1px border + 4px margin)
GAP = 6     # gap between frame and toolbar row
PAD = 6     # window margin around the frame
BTN = 30    # icon button size
GRIP = 10   # corner grip hit radius
EDGE_ZONE = 8  # edge grab zone (pre-capture only)
MIN_W, MIN_H = 240, 160

TOOL_MOVE, TOOL_PEN, TOOL_ARROW, TOOL_RECT, TOOL_FILL, TOOL_TEXT = range(6)
TOOL_NAMES = ["move", "pen", "arrow", "rect", "fillrect", "text"]
PALETTE = ["#e53935", "#fb8c00", "#fdd835", "#43a047", "#1e88e5", "#8e24aa", "#ffffff", "#000000"]

BTN_CSS = f"""
QFrame {{ background: {PANEL_BG}; border: 1px solid {PANEL_BORDER}; }}
QToolButton {{ background: transparent; border: 1px solid transparent; padding: 0; }}
QToolButton:hover {{ background: rgba(47, 110, 165, 170); border-color: {PANEL_BORDER}; }}
QToolButton:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QToolButton:disabled {{ background: rgba(13, 43, 82, 120); }}
QSlider {{ background: transparent; }}
QSlider::groove:horizontal {{ height: 3px; background: rgba(159,201,240,90); }}
QSlider::handle:horizontal {{ width: 10px; height: 10px; margin: -4px 0; background: {ACCENT}; }}
"""

CAPTURE_CSS = f"""
QToolButton {{ background: {PANEL_BG}; border: 2px solid {CAPTURE_ACCENT}; }}
QToolButton:hover {{ background: rgba(47, 110, 165, 200); }}
QToolButton:pressed {{ background: rgba(6, 24, 43, 235); }}
"""

LABEL_CSS = f"""
QLabel {{
  background: {PANEL_BG}; color: #ffffff; border: 1px solid {PANEL_BORDER};
  font-size: 11px; font-weight: bold; padding: 1px 6px;
}}
"""

# ---------- X11 input-only shaping (click-through outside the frame) ----------

_xlib = None
_xext = None
_shape_dpy = None
_atom_net_wm_state = None
_atom_above = None
_ignore_xerr = None


class _XRectangle(ctypes.Structure):
    _fields_ = [("x", ctypes.c_short), ("y", ctypes.c_short),
                ("w", ctypes.c_ushort), ("h", ctypes.c_ushort)]


def _ensure_xlibs():
    global _xlib, _xext
    if _xlib is None:
        _xlib = ctypes.cdll.LoadLibrary("libX11.so.6")
        _xext = ctypes.cdll.LoadLibrary("libXext.so.6")
    return _xlib, _xext


_shape_dpy = None
_atom_net_wm_state = None
_atom_above = None
_ignore_xerr = None
_xlib = None
_xext = None


class _XRectangle(ctypes.Structure):
    _fields_ = [("x", ctypes.c_short), ("y", ctypes.c_short),
                ("w", ctypes.c_ushort), ("h", ctypes.c_ushort)]


def _xsetup():
    """Cached private X connection with properly typed shape/EWMH functions."""
    global _xlib, _xext, _shape_dpy, _ignore_xerr
    if _xlib is None:
        _xlib = ctypes.cdll.LoadLibrary("libX11.so.6")
        _xext = ctypes.cdll.LoadLibrary("libXext.so.6")

        def fn(lib, name, restype, argtypes):
            f = getattr(lib, name)
            f.restype = restype
            f.argtypes = argtypes
            return f

        fn(_xlib, "XOpenDisplay", ctypes.c_void_p, [ctypes.c_char_p])
        fn(_xlib, "XCloseDisplay", None, [ctypes.c_void_p])
        fn(_xlib, "XFlush", None, [ctypes.c_void_p])
        fn(_xlib, "XSync", None, [ctypes.c_void_p, ctypes.c_int])
        fn(_xlib, "XSetErrorHandler", ctypes.c_void_p, [ctypes.c_void_p])
        fn(_xlib, "XGetWindowAttributes", ctypes.c_int,
           [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p])
        fn(_xlib, "XDefaultRootWindow", ctypes.c_ulong, [ctypes.c_void_p])
        fn(_xlib, "XInternAtom", ctypes.c_ulong,
           [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int])
        fn(_xlib, "XSendEvent", ctypes.c_int,
           [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
            ctypes.c_ulong, ctypes.c_void_p])
        fn(_xext, "XCreateRegion", ctypes.c_void_p, [])
        fn(_xext, "XDestroyRegion", None, [ctypes.c_void_p])
        fn(_xext, "XUnionRectWithRegion", ctypes.c_int,
           [ctypes.POINTER(_XRectangle), ctypes.c_void_p, ctypes.c_void_p])
        fn(_xext, "XShapeCombineRegion", None,
           [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int])
        fn(_xext, "XShapeCombineMask", None,
           [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_ulong, ctypes.c_int])
        _shape_dpy = _xlib.XOpenDisplay(None)
        global _ignore_xerr
        _ignore_xerr = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)(
            lambda *_: 0)
        _xlib.XSetErrorHandler(ctypes.cast(_ignore_xerr, ctypes.c_void_p))
        _xlib.XFlush(_shape_dpy)
    if not _shape_dpy:
        raise OSError("no X display")
    return _xlib, _xext, _shape_dpy


def set_input_region(widget, region):
    """Input-only X shape: region receives events, everything else is
    click-through for the desktop. A None region = full input (shape removed).
    The bounding shape is never touched, so painting/compositing are
    unaffected."""
    if not widget.isVisible():
        return  # native window not real yet; re-applied on the next relayout
    try:
        xlib, xext, dpy = _xsetup()
        wid = int(widget.winId())
        SHAPE_INPUT, SHAPE_SET = 2, 0
        if region is None:
            xext.XShapeCombineMask(dpy, wid, SHAPE_INPUT, 0, 0, 0, SHAPE_SET)
        else:
            rg = xext.XCreateRegion()
            for r in region:
                rect = _XRectangle(r.x(), r.y(), r.width(), r.height())
                xext.XUnionRectWithRegion(ctypes.byref(rect), rg, rg)
            xext.XShapeCombineRegion(dpy, wid, SHAPE_INPUT, 0, 0, rg, SHAPE_SET)
            xext.XDestroyRegion(rg)
        xlib.XFlush(dpy)
    except Exception:
        log.exception("input shape failed")


def set_always_on_top(widget, on: bool):
    """Flip _NET_WM_STATE_ABOVE on the managed window via an EWMH client
    message — mutter changes the stacking in place: no window recreation,
    no geometry jump."""
    global _atom_net_wm_state, _atom_above
    try:
        xlib, xext, dpy = _xsetup()
        win = int(widget.winId())
        if _atom_net_wm_state is None:
            _atom_net_wm_state = xlib.XInternAtom(dpy, b"_NET_WM_STATE", False)
            _atom_above = xlib.XInternAtom(dpy, b"_NET_WM_STATE_ABOVE", False)

        class _CME(ctypes.Structure):
            _fields_ = [("type", ctypes.c_int), ("serial", ctypes.c_ulong),
                        ("send_event", ctypes.c_int), ("display", ctypes.c_void_p),
                        ("window", ctypes.c_ulong), ("message_type", ctypes.c_ulong),
                        ("format", ctypes.c_int),
                        ("data", ctypes.c_ulong * 5)]

        ev = _CME()
        ev.type = 33  # ClientMessage
        ev.window = win
        ev.message_type = _atom_net_wm_state
        ev.format = 32
        ev.data[0] = 1 if on else 0  # 1 = add, 0 = remove
        ev.data[1] = _atom_above
        ev.data[2] = 0

        root = xlib.XDefaultRootWindow(dpy)
        mask = (1 << 20) | (1 << 19)  # SubstructureRedirect | SubstructureNotify
        xlib.XSendEvent(dpy, root, False, mask, ctypes.byref(ev))
        xlib.XFlush(dpy)
        log.info("pin: _NET_WM_STATE_ABOVE %s", "set" if on else "removed")
    except Exception:
        log.exception("above-state toggle failed")


class _WidthPreview(QWidget):
    """Live stroke-thickness display for the width popup."""

    def __init__(self, color: QColor, width: int):
        super().__init__()
        self.setFixedSize(190, 44)
        self._color = QColor(color)
        self._width = int(width)

    def set_state(self, color: QColor, width: int):
        self._color = QColor(color)
        self._width = int(width)
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        y = self.height() / 2 - 5
        pen = QPen(self._color, self._width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawLine(8, y, self.width() - 56, y)
        f = p.font()
        f.setPixelSize(13)
        p.setFont(f)
        p.setPen(QColor(CHROME_FG))
        p.drawText(QRect(self.width() - 48, 0, 44, self.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                   f"{self._width}px")


class _TextSizePreview(QWidget):
    """Live text-size display: 'Aa' rendered at the chosen pixel size."""

    def __init__(self, color: QColor, size: int):
        super().__init__()
        self.setFixedSize(110, 44)
        self._color = QColor(color)
        self._size = int(size)

    def set_state(self, color: QColor, size: int):
        self._color = QColor(color)
        self._size = int(size)
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        f = p.font()
        f.setPixelSize(min(self._size, 34))
        p.setFont(f)
        p.setPen(self._color)
        p.drawText(QRect(4, 0, 56, self.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "Aa")
        f2 = p.font()
        f2.setPixelSize(13)
        p.setFont(f2)
        p.setPen(QColor(CHROME_FG))
        p.drawText(QRect(self.width() - 48, 0, 44, self.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                   f"{self._size}px")


class JumpSlider(QSlider):
    """Groove click jumps to the clicked value instead of page-stepping."""

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.setValue(QStyle.sliderValueFromPosition(
                self.minimum(), self.maximum(),
                int(e.position().x()), max(1, self.width() - 1)))
        super().mousePressEvent(e)


class TextEditor(QLineEdit):
    """Text tool editor: plain click edits the caret, drag moves the box."""

    def __init__(self, parent):
        super().__init__(parent)
        self._press = None
        self._dragging = False

    def event(self, e):
        # Ctrl+C must stay the capture shortcut even while editing
        # (QLineEdit otherwise claims clipboard keys via ShortcutOverride)
        if e.type() == QEvent.Type.ShortcutOverride and e.matches(QKeySequence.StandardKey.Copy):
            e.ignore()
            return True
        return super().event(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press = e.globalPosition().toPoint()
            self._dragging = False
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._press is not None and (e.buttons() & Qt.LeftButton):
            d = e.globalPosition().toPoint() - self._press
            if d.manhattanLength() > 4:
                self._dragging = True
                self.move(self.pos() + d)
                self._press = e.globalPosition().toPoint()
                return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        dragged = self._dragging
        self._press = None
        self._dragging = False
        if dragged:
            e.accept()
            return
        super().mouseReleaseEvent(e)


class SnipperWindow(QWidget):
    ipc_command = Signal(str)

    def __init__(self, settings: Settings):
        super().__init__(None)
        self.settings = settings
        # Bypass the WM: mutter clamps managed toplevels into the primary
        # monitor's workarea (uneven dual-monitor layout), which used to cut
        # the overlay down to 4440x1048+40+392 and left the top of the screen
        # unreachable. Override-redirect honors setGeometry 1:1; stacking and
        # input shaping are done manually via X calls below.
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool |
                            Qt.X11BypassWindowManagerHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        # the override-redirect window is never "active" in Qt's eyes and
        # tooltips are suppressed for inactive windows without this
        self.setAttribute(Qt.WA_AlwaysShowToolTips, True)
        self.setMouseTracking(True)

        self._desktop = QRect(QPoint(0, 0), QSize(2560, 1440))
        self._drawing = False
        self._draw_start = None
        self._last_rect = None
        self._pre_draw_content = None
        self._content = QRect(0, 0, settings["default_width"], settings["default_height"])
        self._fullscreen = False
        self._pre_fs = None
        self._pinned = True
        self._busy = False

        self._image = None
        self._image_px = None
        self._is_latest = False
        self._opacity = max(0.2, min(1.0, settings["default_opacity"] / 100))

        self._tool = TOOL_MOVE
        self._color = QColor(settings["color"])
        self._width = int(settings["stroke_width"])
        self._text_size = int(settings["text_size"])
        self._shapes = []

        self.history = []
        self._hist_idx = -1

        self._pending = None
        self._drag_offset = None
        self._drag_offset_global = None
        self._resize_corner = None
        self._resize_ref = None
        self._text_editing = False
        self._border_alpha = 255
        self._drag_timer = None
        self._ant_phase = 0.0
        self._ant_timer = QTimer(self)
        self._ant_timer.setInterval(90)
        self._ant_timer.timeout.connect(self._ant_step)

        self._build_ui()

        QShortcut(QKeySequence(Qt.Key_Escape), self, self._on_escape)
        self._sc_undo = QShortcut(QKeySequence("Ctrl+Z"), self, self.undo)
        self._sc_prev = QShortcut(QKeySequence(Qt.Key_Left), self, lambda: self.hist_step(-1))
        self._sc_next = QShortcut(QKeySequence(Qt.Key_Right), self, lambda: self.hist_step(1))
        self._sc_capture = QShortcut(QKeySequence("Ctrl+C"), self, self._capture_shortcut)
        self._typing_shortcuts = [self._sc_undo, self._sc_prev, self._sc_next]
        for key, t in (("m", TOOL_MOVE), ("p", TOOL_PEN), ("a", TOOL_ARROW),
                       ("r", TOOL_RECT), ("f", TOOL_FILL), ("t", TOOL_TEXT)):
            sc = QShortcut(QKeySequence(key), self, lambda t=t: self.set_tool(t))
            self._typing_shortcuts.append(sc)

        self._ant_timer.start()

        self.ipc_command.connect(self._on_ipc)

    # ---------- UI construction ----------

    def _build_ui(self):
        self.bar_bottom = QFrame(self)
        self.bar_top = QFrame(self)
        self.bar_right = QFrame(self)
        for bar in (self.bar_bottom, self.bar_top, self.bar_right):
            bar.setStyleSheet(BTN_CSS)
            bar.setCursor(Qt.ArrowCursor)
        lay_b = QHBoxLayout(self.bar_bottom)
        lay_t = QHBoxLayout(self.bar_top)
        lay_r = QVBoxLayout(self.bar_right)
        for lay in (lay_b, lay_t, lay_r):
            lay.setContentsMargins(4, 4, 4, 4)
            lay.setSpacing(4)

        def mkbtn(parent, name, tip, cb, checkable=False):
            b = QToolButton(parent)
            b.setIcon(icons.svg_icon(name))
            b.setIconSize(QSize(18, 18))
            b.setFixedSize(BTN, BTN)
            b.setToolTip(tip)
            b.setCheckable(checkable)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(cb)
            return b

        def mksep(parent):
            s = QFrame(parent)
            s.setStyleSheet("QFrame { background: rgba(159,201,240,80); border: none; }")
            s.setFixedSize(1, 20)
            s.setCursor(Qt.ArrowCursor)
            return s

        # tools (bottom edge): drawing tools | text size + stroke settings | undo
        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)
        self.btn_pen = mkbtn(self.bar_bottom, "pen", "Pen (P)", lambda: self.set_tool(TOOL_PEN), True)
        self.btn_arrow = mkbtn(self.bar_bottom, "arrow", "Arrow (A)", lambda: self.set_tool(TOOL_ARROW), True)
        self.btn_rect = mkbtn(self.bar_bottom, "rect", "Rectangle (R)", lambda: self.set_tool(TOOL_RECT), True)
        self.btn_fill = mkbtn(self.bar_bottom, "fillrect", "Filled rectangle — uses the current color (F)", lambda: self.set_tool(TOOL_FILL), True)
        self.btn_text = mkbtn(self.bar_bottom, "text", "Text (T)", lambda: self.set_tool(TOOL_TEXT), True)
        self.btn_move = mkbtn(self.bar_right, "move", "Move / transform the frame (M)", lambda: self.set_tool(TOOL_MOVE), True)
        for tid, b in ((TOOL_PEN, self.btn_pen), (TOOL_ARROW, self.btn_arrow),
                       (TOOL_RECT, self.btn_rect), (TOOL_FILL, self.btn_fill),
                       (TOOL_TEXT, self.btn_text)):
            self.tool_group.addButton(b, tid)
            lay_b.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)
        self.tool_group.addButton(self.btn_move, TOOL_MOVE)
        self.btn_move.setChecked(True)
        self.btn_textsize = mkbtn(self.bar_bottom, "textsize", "Text size", self._toggle_textsize_panel)
        lay_b.addWidget(self.btn_textsize, 0, Qt.AlignmentFlag.AlignVCenter)
        lay_b.addWidget(mksep(self.bar_bottom))

        self.btn_color = mkbtn(self.bar_bottom, "pen", "Stroke color", self._open_color_picker)
        self.btn_color.setIcon(icons.color_dot_icon(self._color.name()))
        lay_b.addWidget(self.btn_color, 0, Qt.AlignmentFlag.AlignVCenter)
        self.btn_width = mkbtn(self.bar_bottom, "width", "Stroke width", self._toggle_width_panel)
        lay_b.addWidget(self.btn_width, 0, Qt.AlignmentFlag.AlignVCenter)
        lay_b.addWidget(mksep(self.bar_bottom))

        # drawing panel (bottom edge): tools + stroke settings + undo
        self.btn_undo = mkbtn(self.bar_bottom, "undo", "Undo (Ctrl+Z)", self.undo)
        lay_b.addWidget(self.btn_undo, 0, Qt.AlignmentFlag.AlignVCenter)

        # adjust (right edge): move, 1:1, bake, redraw, reset, fullscreen
        self.btn_11 = QToolButton(self.bar_right)
        self.btn_11.setText("1:1")
        self.btn_11.setFixedSize(BTN, BTN)
        self.btn_11.setToolTip("Reset scale: frame shows the image pixel-per-pixel")
        self.btn_11.setStyleSheet(
            f"QToolButton {{ background: rgba(38,50,56,215); border: none;"
            f" color: {CHROME_FG}; font-size: 10px; font-weight: bold; }}"
            f"QToolButton:hover {{ background: rgba(55,71,79,240); }}")
        self.btn_11.clicked.connect(self.reset_scale)
        self.btn_bake = mkbtn(self.bar_right, "bake", "Bake: commit the resized scale into the image", self.bake_scale)
        self.btn_redraw = mkbtn(self.bar_right, "redraw", "Close this rectangle and draw a new one", self.start_redraw)
        self.btn_reset = mkbtn(self.bar_right, "reset", "Clear the frame: hide image and drawings, same size (history kept)", self.reset)
        self.btn_full = mkbtn(self.bar_right, "full", "Fullscreen on the current monitor", self.toggle_fullscreen)
        for b in (self.btn_move, self.btn_11, self.btn_bake, self.btn_redraw, self.btn_reset, self.btn_full):
            lay_r.addWidget(b, 0, Qt.AlignmentFlag.AlignHCenter)

        # actions (top edge): camera, copy, pin, opacity, history, trash, delete, settings, close
        self.capture_btn = QToolButton(self.bar_top)
        self.capture_btn.setIcon(icons.svg_icon("camera", CAPTURE_ACCENT))
        self.capture_btn.setIconSize(QSize(20, 20))
        self.capture_btn.setFixedSize(34, 34)
        self.capture_btn.setStyleSheet(CAPTURE_CSS)
        self.capture_btn.setToolTip("Capture: save to folder + clipboard (hold to drag the shot)")
        self.capture_btn.setCursor(Qt.PointingHandCursor)
        self.capture_btn.pressed.connect(self._on_capture_pressed)
        lay_t.addWidget(self.capture_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self.btn_copy = mkbtn(self.bar_top, "copy", "Copy image to clipboard", self.copy_image)
        lay_t.addWidget(self.btn_copy, 0, Qt.AlignmentFlag.AlignVCenter)
        self.btn_pin = mkbtn(self.bar_top, "pin", "Always on top", self.toggle_pin, True)
        self.btn_pin.setChecked(True)
        lay_t.addWidget(self.btn_pin, 0, Qt.AlignmentFlag.AlignVCenter)

        self.opacity_slider = QSlider(Qt.Horizontal, self.bar_top)
        self.opacity_slider.setRange(20, 100)
        self.opacity_slider.setValue(int(self._opacity * 100))
        self.opacity_slider.setFixedWidth(64)
        self.opacity_slider.setToolTip("Image opacity")
        self.opacity_slider.setCursor(Qt.PointingHandCursor)
        self.opacity_slider.valueChanged.connect(self._on_opacity)
        lay_t.addWidget(self.opacity_slider, 0, Qt.AlignmentFlag.AlignVCenter)
        lay_t.addWidget(mksep(self.bar_top))

        self.btn_prev = mkbtn(self.bar_top, "prev", "Previous capture (←)", lambda: self.hist_step(-1))
        self.hist_label = QLabel("0/0", self.bar_top)
        self.hist_label.setCursor(Qt.ArrowCursor)
        self.hist_label.setStyleSheet(f"QLabel {{ color: {CHROME_FG}; font-size: 11px; background: transparent; }}")
        self.btn_next = mkbtn(self.bar_top, "next", "Next capture (→)", lambda: self.hist_step(1))
        lay_t.addWidget(self.btn_prev, 0, Qt.AlignmentFlag.AlignVCenter)
        lay_t.addWidget(self.hist_label, 0, Qt.AlignmentFlag.AlignVCenter)
        lay_t.addWidget(self.btn_next, 0, Qt.AlignmentFlag.AlignVCenter)
        self.btn_trash = mkbtn(self.bar_top, "trash",
                               "Clear history — delete ALL shots of this session", self.clear_history)
        lay_t.addWidget(self.btn_trash, 0, Qt.AlignmentFlag.AlignVCenter)
        lay_t.addWidget(mksep(self.bar_top))
        self.btn_delone = mkbtn(self.bar_top, "delone",
                                "Delete the current shot (file + history entry)", self.delete_current)
        lay_t.addWidget(self.btn_delone, 0, Qt.AlignmentFlag.AlignVCenter)
        lay_t.addWidget(mksep(self.bar_top))
        self.btn_settings = mkbtn(self.bar_top, "gear", "Settings", self._open_settings)
        lay_t.addWidget(self.btn_settings, 0, Qt.AlignmentFlag.AlignVCenter)
        self.btn_close = mkbtn(self.bar_top, "close", "Hide (Esc)", self._dismiss)
        lay_t.addWidget(self.btn_close, 0, Qt.AlignmentFlag.AlignVCenter)

        self.size_label = QLabel(self)
        self.size_label.setStyleSheet(LABEL_CSS)
        self.size_label.setCursor(Qt.ArrowCursor)
        self.size_label.hide()

        self.text_edit = TextEditor(self)
        self.text_edit.hide()
        self._refresh_text_style()
        self.text_edit.returnPressed.connect(self._commit_text)
        self.text_edit.editingFinished.connect(self._commit_text)

        self._width_panel = QFrame(self)
        self._width_panel.setStyleSheet(BTN_CSS)
        wp = QHBoxLayout(self._width_panel)
        wp.setContentsMargins(10, 6, 10, 8)
        wp.setSpacing(8)
        self._width_preview = _WidthPreview(self._color, self._width)
        self._width_slider = JumpSlider(Qt.Horizontal, self._width_panel)
        self._width_slider.setRange(1, 30)
        self._width_slider.setValue(self._width)
        self._width_slider.setFixedWidth(170)
        self._width_slider.setFixedHeight(24)
        self._width_slider.setCursor(Qt.PointingHandCursor)
        self._width_slider.valueChanged.connect(self._set_width)
        wp.addWidget(self._width_preview, 0, Qt.AlignmentFlag.AlignVCenter)
        wp.addWidget(self._width_slider, 0, Qt.AlignmentFlag.AlignVCenter)
        self._width_panel.hide()

        self._textsize_panel = QFrame(self)
        self._textsize_panel.setStyleSheet(BTN_CSS)
        tp = QHBoxLayout(self._textsize_panel)
        tp.setContentsMargins(10, 6, 10, 8)
        tp.setSpacing(8)
        self._textsize_preview = _TextSizePreview(self._color, self._text_size)
        self._textsize_slider = JumpSlider(Qt.Horizontal, self._textsize_panel)
        self._textsize_slider.setRange(8, 96)
        self._textsize_slider.setValue(self._text_size)
        self._textsize_slider.setFixedWidth(170)
        self._textsize_slider.setFixedHeight(24)
        self._textsize_slider.setCursor(Qt.PointingHandCursor)
        self._textsize_slider.valueChanged.connect(self._set_text_size)
        tp.addWidget(self._textsize_preview, 0, Qt.AlignmentFlag.AlignVCenter)
        tp.addWidget(self._textsize_slider, 0, Qt.AlignmentFlag.AlignVCenter)
        self._textsize_panel.hide()
        self.btn_textsize.setEnabled(False)

        self._update_ui_state()

    # ---------- geometry ----------

    def content_global_rect(self) -> QRect:
        # from REQUESTED geometry: mapToGlobal can lag behind the WM-acked
        # position right after moves/resizes and must not drive placement
        return QRect(QPoint(self.x() + self._content.x(), self.y() + self._content.y()),
                     self._content.size())

    def relayout(self):
        """Position the edge bars around the frame inside the fixed overlay
        window. Bottom = tools, top = actions, right = adjust; each flips
        inside the frame when there is no room outside."""
        self._width_panel.hide()
        self._textsize_panel.hide()
        if self._drawing:
            self.bar_bottom.hide()
            self.bar_top.hide()
            self.bar_right.hide()
            self._update_mask()
            self.update()
            return

        for bar in (self.bar_bottom, self.bar_top, self.bar_right):
            bar.show()

        self._place_bars()
        self._place_size_label()
        self._update_mask()
        self.update()

    def _place_bars(self):
        cw, ch = self._content.width(), self._content.height()
        d_w, d_h = self._desktop.width(), self._desktop.height()

        def fresh_hint(bar):
            bar.layout().invalidate()
            bar.layout().activate()
            return bar.sizeHint()

        bb = fresh_hint(self.bar_bottom); bb.setWidth(max(bb.width(), 40)); bb.setHeight(TB_H)
        tb = fresh_hint(self.bar_top); tb.setWidth(max(tb.width(), 40)); tb.setHeight(TB_H)
        rb = fresh_hint(self.bar_right); rb.setWidth(TB_H); rb.setHeight(max(rb.height(), 40))

        # bottom bar: outside below, else inside the bottom edge
        bx = max(0, min(self._content.center().x() - bb.width() // 2, d_w - bb.width()))
        by = self._content.bottom() + GAP
        if by + bb.height() > d_h:
            by = self._content.bottom() - bb.height() - 4
        self.bar_bottom.setGeometry(bx, by, bb.width(), bb.height())

        # top bar: outside above, else inside the top edge
        tx = max(0, min(self._content.center().x() - tb.width() // 2, d_w - tb.width()))
        ty = self._content.top() - GAP - tb.height()
        if ty < 0:
            ty = self._content.top() + 4
        self.bar_top.setGeometry(tx, ty, tb.width(), tb.height())

        # right bar: outside right, vertically centered, else inside the edge
        ry = max(0, min(self._content.center().y() - rb.height() // 2, d_h - rb.height()))
        rx = self._content.right() + GAP
        if rx + rb.width() > d_w:
            rx = self._content.right() - rb.width() - 4
        self.bar_right.setGeometry(rx, ry, rb.width(), rb.height())

    def _window_covers_desktop(self):
        return (self.width() >= self._desktop.width() and
                self.height() >= self._desktop.height())

    def _move_content_to(self, tl: QPoint):
        """Drag primitive (full-desktop overlay): move only the content rect,
        repaint only the band it left/entered. Moving the window too doubled
        the speed — the frame raced ahead of the cursor."""
        m = TB_H + GAP + PAD
        dirty = QRegion(self._content.adjusted(-m, -m, m, m))
        self._content.moveTopLeft(tl)
        self._place_bars()
        self._place_size_label()
        self._update_mask()
        self.update(dirty.united(QRegion(self._content.adjusted(-m, -m, m, m))))

    def _ant_step(self):
        # animate only while selecting; a captured shot freezes the border
        if not self.isVisible() or self._image is not None:
            return
        self._ant_phase = (self._ant_phase + 2.2) % 16.0
        self.update()

    def _update_mask(self):
        """Input-only shape: frame + bars take events, the rest of the overlay
        is click-through for the desktop. In draw mode the whole overlay takes
        events (the rectangle can start anywhere). Never touches painting."""
        if self._drawing:
            set_input_region(self, None)
            return
        region = QRegion(self._content.adjusted(-8, -8, 8, 8))
        for bar in (self.bar_bottom, self.bar_top, self.bar_right):
            region = region.united(QRegion(bar.geometry()))
        if self.size_label.isVisible():
            region = region.united(QRegion(self.size_label.geometry()))
        if self._width_panel.isVisible():
            region = region.united(QRegion(self._width_panel.geometry()))
        if self._textsize_panel.isVisible():
            region = region.united(QRegion(self._textsize_panel.geometry()))
        set_input_region(self, region)

    def _place_size_label(self):
        lbl = self.size_label.sizeHint() if self.size_label.text() else QSize(60, 18)
        lx = self._content.right() - lbl.width() - 8
        ly = self._content.bottom() - lbl.height() - 8
        self.size_label.setGeometry(
            max(self._content.left() + 4, lx),
            max(self._content.top() + 4, ly),
            lbl.width(), lbl.height())

    def open_at_cursor(self):
        self.open_draw()

    def _enter_draw_mode(self):
        if self._content is not None and not self._content.isNull() and self._content.width() > 0:
            self._last_rect = QRect(self._content)
        self._drawing = True
        self._draw_start = None
        self._pre_draw_content = None
        self._content = QRect()
        self.setCursor(Qt.CrossCursor)
        self.size_label.hide()
        self.relayout()
        self._update_ui_state()

    def open_draw(self):
        """Activate in draw mode: crosshair, pull the rectangle from nothing."""
        self._end_session()
        vg = QGuiApplication.primaryScreen().virtualGeometry()
        self._desktop = QRect(QPoint(0, 0), QSize(vg.width(), vg.height()))
        self.setGeometry(vg.x(), vg.y(), self._desktop.width(), self._desktop.height())
        self._enter_draw_mode()
        self.show()
        self.raise_()
        self.activateWindow()
        self._update_mask()
        set_always_on_top(self, self._pinned)

    def start_redraw(self):
        """Close the current rectangle and draw a new one."""
        self._end_session()
        vg = QGuiApplication.primaryScreen().virtualGeometry()
        self._desktop = QRect(QPoint(0, 0), QSize(vg.width(), vg.height()))
        self.setGeometry(vg.x(), vg.y(), self._desktop.width(), self._desktop.height())
        self._enter_draw_mode()
        self.show()
        self.raise_()
        self.activateWindow()
        self._update_mask()
        set_always_on_top(self, self._pinned)

    def open_at(self, cx: int, cy: int):
        """Open with a ready frame at the given point (scripting/tests)."""
        self._end_session()
        vg = QGuiApplication.primaryScreen().virtualGeometry()
        self._desktop = QRect(QPoint(0, 0), QSize(vg.width(), vg.height()))
        self._drawing = False
        self._draw_start = None
        self._fullscreen = False
        w = min(self.settings["default_width"], self._desktop.width() - TB_H - 4 * PAD)
        h = min(self.settings["default_height"], self._desktop.height() - TB_H - 4 * PAD)
        x = max(PAD, min(cx - w // 2 - vg.x(), self._desktop.width() - w - PAD))
        y = max(PAD, min(cy - h // 2 - vg.y(), self._desktop.height() - h - TB_H - GAP - 2 * PAD))
        self._content = QRect(QPoint(x, y), QSize(w, h))
        self.unsetCursor()
        self.setGeometry(vg.x(), vg.y(), self._desktop.width(), self._desktop.height())
        self.relayout()
        self.show()
        self.raise_()
        self.activateWindow()
        set_always_on_top(self, self._pinned)
        self._apply_pin()
        self._pulse_border()

    def _pulse_border(self):
        seq = [90, 255, 90, 255, 90, 255]
        for i, alpha in enumerate(seq):
            QTimer.singleShot(90 * i, lambda a=alpha: self._set_border_alpha(a))

    def _set_border_alpha(self, alpha: int):
        self._border_alpha = alpha
        self.update()

    # ---------- tools / toolbar state ----------

    def set_tool(self, tool):
        if self._text_editing:
            self._commit_text()
        self._tool = tool
        if self._width_panel.isVisible():
            self._hide_width_panel()
        if self._textsize_panel.isVisible():
            self._hide_textsize_panel()
        self.btn_textsize.setEnabled(tool == TOOL_TEXT)
        for tid, b in ((TOOL_MOVE, self.btn_move), (TOOL_PEN, self.btn_pen),
                       (TOOL_ARROW, self.btn_arrow), (TOOL_RECT, self.btn_rect),
                       (TOOL_FILL, self.btn_fill), (TOOL_TEXT, self.btn_text)):
            b.setChecked(tid == tool)
            b.setIcon(icons.svg_icon(TOOL_NAMES[tid], icons.DARK if tid == tool else icons.LIGHT))

    def _refresh_text_style(self):
        self.text_edit.setStyleSheet(
            f"QLineEdit {{ background: rgba(255,255,255,230); color: {self._color.name()}; "
            f"font-size: {self._text_size}px; border: 1px dashed {self._color.name()}; }}"
        )

    def _set_color(self, color: QColor):
        self._color = QColor(color)
        self.btn_color.setIcon(icons.color_dot_icon(self._color.name()))
        self._refresh_text_style()
        self.update()

    def _set_typing_mode(self, on: bool):
        """While the text editor has the keys, letter/undo/arrow shortcuts
        must not steal them (they double as tool/history keys)."""
        for sc in self._typing_shortcuts:
            sc.setEnabled(not on)
        self._sc_undo.setEnabled(not on)

    def _capture_shortcut(self):
        if self._text_editing:
            self._commit_text()
        self.do_capture()

    def _set_width(self, w: int):
        self._width = int(w)
        self._width_preview.set_state(self._color, self._width)
        self._refresh_text_style()
        self.update()

    def _set_text_size(self, s: int):
        self._text_size = int(s)
        self._textsize_preview.set_state(self._color, self._text_size)
        self._refresh_text_style()
        self.update()

    def _width_btn_rect(self) -> QRect:
        return self.btn_width.geometry().translated(self.bar_bottom.geometry().topLeft())

    def _textsize_btn_rect(self) -> QRect:
        return self.btn_textsize.geometry().translated(self.bar_bottom.geometry().topLeft())

    def _anchor_panel(self, panel: QFrame, btn: QToolButton):
        bg = btn.geometry().translated(self.bar_bottom.geometry().topLeft())
        px = bg.center().x() - panel.width() // 2
        px = max(PAD, min(px, self._desktop.width() - panel.width() - PAD))
        py = bg.top() - GAP - panel.height()
        if py < 0:
            py = bg.bottom() + GAP
        panel.setGeometry(px, py, panel.width(), panel.height())

    def _toggle_width_panel(self):
        if self._width_panel.isVisible():
            self._hide_width_panel()
            return
        self._hide_textsize_panel()
        self._width_slider.setValue(self._width)
        self._width_preview.set_state(self._color, self._width)
        self._width_panel.adjustSize()
        self._anchor_panel(self._width_panel, self.btn_width)
        self._width_panel.show()
        self._width_panel.raise_()
        self._update_mask()

    def _hide_width_panel(self):
        self._width_panel.hide()
        self._update_mask()

    def _toggle_textsize_panel(self):
        if self._textsize_panel.isVisible():
            self._hide_textsize_panel()
            return
        self._hide_width_panel()
        self._textsize_slider.setValue(self._text_size)
        self._textsize_preview.set_state(self._color, self._text_size)
        self._textsize_panel.adjustSize()
        self._anchor_panel(self._textsize_panel, self.btn_textsize)
        self._textsize_panel.show()
        self._textsize_panel.raise_()
        self._update_mask()

    def _hide_textsize_panel(self):
        self._textsize_panel.hide()
        self._update_mask()

    def _open_color_picker(self):
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: rgba(13,43,82,242); border: 1px solid rgba(159,201,240,60);"
            f" border-radius: 0; padding: 6px; }}"
        )
        row = QWidget(menu)
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(4)
        for name in PALETTE:
            b = QToolButton(row)
            b.setFixedSize(22, 22)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                f"QToolButton {{ background: {name}; border: 1px solid rgba(255,255,255,70); }}"
            )
            b.clicked.connect(lambda _=False, n=name: (menu.close(), self._set_color(QColor(n))))
            h.addWidget(b)
        custom = QToolButton(row)
        custom.setFixedSize(22, 22)
        custom.setCursor(Qt.PointingHandCursor)
        custom.setText("+")
        custom.setStyleSheet(
            f"QToolButton {{ color: {CHROME_FG}; background: rgba(255,255,255,30); }}"
        )
        custom.setToolTip("Custom color…")
        custom.clicked.connect(lambda: (menu.close(), self._pick_custom_color()))
        h.addWidget(custom)
        wa = QWidgetAction(menu)
        wa.setDefaultWidget(row)
        menu.addAction(wa)
        menu.adjustSize()
        gp = self.btn_color.mapToGlobal(QPoint(0, 0))
        menu.exec(QPoint(gp.x() - 10, gp.y() - menu.height() - 8))

    def _pick_custom_color(self):
        c = QColorDialog.getColor(self._color, self, "Stroke color")
        if c.isValid():
            self._set_color(c)

    def undo(self):
        if self._text_editing:
            return
        if self._shapes:
            self._shapes.pop()
            self.btn_undo.setEnabled(bool(self._shapes) or bool(self._imprints()))
            self.update()
            return
        entry = self._current_entry()
        if entry and entry.get("imprints"):
            entry["imprints"].pop()
            self._rebuild_imprinted(entry)

    def _current_entry(self):
        if self.history and 0 <= self._hist_idx < len(self.history):
            return self.history[self._hist_idx]
        return None

    def _imprints(self):
        entry = self._current_entry()
        return entry.get("imprints", []) if entry else []

    def _paint_imprint(self, painter, shape):
        sx = self._image.width() / max(1, self._content.width())
        sy = self._image.height() / max(1, self._content.height())
        painter.scale(sx, sy)
        shape.draw(painter)

    def _rebuild_imprinted(self, entry):
        img = entry["base"].copy()
        p = QPainter(img)
        for shape in entry["imprints"]:
            self._paint_imprint(p, shape)
        p.end()
        entry["image"] = img
        self._image = img
        QApplication.clipboard().setImage(img)
        self.btn_undo.setEnabled(bool(entry["imprints"]))
        self.update()

    def _persist_baked(self, img: QImage):
        """Baked images get re-saved as a new copy and pushed to the clipboard."""
        try:
            path = self._save_image(img)
        except Exception:
            log.exception("bake save failed")
            return None
        QApplication.clipboard().setImage(img)
        return path

    def reset_scale(self):
        """1:1: size the frame to the image's real pixels."""
        if self._image is None:
            return
        w = min(self._image.width(), self._desktop.width() - 2 * PAD)
        h = min(self._image.height(), self._desktop.height() - TB_H - 3 * PAD)
        self._content.setSize(QSize(w, h))
        self.relayout()
        self._refresh_size_label()

    def bake_scale(self):
        """Bake the current display scale into the image (resample), clearing
        the imprint undo stack — they are now part of the pixels."""
        if self._image is None:
            return
        cw, ch = self._content.width(), self._content.height()
        img = QImage(cw, ch, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.drawImage(QRect(0, 0, cw, ch), self._image)
        p.end()
        self._image = img
        entry = self._current_entry()
        if entry is not None:
            entry["image"] = img
            entry["base"] = img.copy()
            entry["imprints"] = []
            entry["logical"] = QSize(cw, ch)
            new_path = self._persist_baked(img)
            if new_path:
                entry["path"] = new_path
                entry.setdefault("files", []).append(new_path)
        else:
            self._persist_baked(img)
        self.btn_undo.setEnabled(False)
        self._refresh_size_label()
        self.update()

    def delete_current(self):
        """Delete only the current screenshot: history entry plus its files."""
        if not self.history or not (0 <= self._hist_idx < len(self.history)):
            return
        entry = self.history.pop(self._hist_idx)
        for f in entry.get("files", []):
            try:
                Path(f).unlink()
            except OSError:
                pass
        if self.history:
            self.hist_goto(min(self._hist_idx, len(self.history) - 1))
        else:
            self._hist_idx = -1
            self._image = None
            self._image_px = None
            self._is_latest = False
        self._update_ui_state()
        self.relayout()

    def _open_settings(self):
        from .settings_dialog import SettingsDialog
        SettingsDialog(self.settings, self, self).exec()

    def apply_settings(self, s):
        """Live-apply what can change at runtime."""
        self._set_color(QColor(s["color"]))
        self._set_width(int(s["stroke_width"]))
        self._set_text_size(int(s["text_size"]))

    def clear_history(self):
        self.history = []
        self._hist_idx = -1
        self._image = None
        self._image_px = None
        self._is_latest = False
        self._update_ui_state()
        self.relayout()

    def reset(self):
        """Clear the view: hide the current image and drawings. The frame keeps
        its size and position; history is untouched."""
        if self._text_editing:
            self._cancel_text()
        self._shapes = []
        self._pending = None
        self._image = None
        self._image_px = None
        self._is_latest = False
        self.set_tool(TOOL_MOVE)
        self.btn_undo.setEnabled(False)
        self.relayout()
        self._refresh_size_label()
        self._update_ui_state()

    def _on_opacity(self, v):
        self._opacity = v / 100
        self.update()

    def toggle_pin(self):
        self._pinned = not self._pinned
        self.btn_pin.setChecked(self._pinned)
        self._apply_pin()

    def _apply_pin(self):
        """Pin is the always-on-top marker only: the bypass overlay is
        inherently on top and must always span the desktop. The old hug
        (setGeometry around the shot — a managed-window-era relic) jumped
        the frame, clipped the right bar and flipped drags into
        window-move mode."""
        vg = QGuiApplication.primaryScreen().virtualGeometry()
        cg = self.content_global_rect()
        self._content.moveTopLeft(QPoint(cg.left() - vg.x(), cg.top() - vg.y()))
        self.setGeometry(vg.x(), vg.y(), self._desktop.width(), self._desktop.height())
        self.relayout()

    def toggle_fullscreen(self):
        vg = QGuiApplication.primaryScreen().virtualGeometry()
        if self._fullscreen:
            self._fullscreen = False
            self._content = QRect(self._pre_fs)
        else:
            center_global = QPoint(vg.x() + self._content.center().x(),
                                   vg.y() + self._content.center().y())
            scr = QGuiApplication.screenAt(center_global) or QGuiApplication.primaryScreen()
            sg = scr.geometry()
            self._pre_fs = QRect(self._content)
            self._fullscreen = True
            self._content = QRect(QPoint(sg.x() - vg.x(), sg.y() - vg.y()),
                                  QSize(sg.width(), sg.height()))
        self.relayout()

    def _update_ui_state(self):
        has_img = self._image is not None
        view_hist = has_img and not self._is_latest
        tools_on = has_img and not view_hist

        for b in (self.btn_move, self.btn_pen, self.btn_arrow, self.btn_rect,
                  self.btn_fill, self.btn_text):
            b.setEnabled(tools_on or not has_img)
        self.btn_color.setEnabled(tools_on or not has_img)
        self.btn_width.setEnabled(tools_on or not has_img)
        self.btn_textsize.setEnabled(self._tool == TOOL_TEXT and (tools_on or not has_img))
        self.btn_undo.setEnabled(bool(self._shapes) or bool(self._imprints()))

        self.btn_copy.setEnabled(has_img)
        self.opacity_slider.setEnabled(has_img)
        self.btn_11.setEnabled(has_img)
        self.btn_bake.setEnabled(has_img)
        self.btn_delone.setEnabled(bool(self.history) and has_img)
        self.btn_prev.setEnabled(bool(self.history) and self._hist_idx > 0)
        self.btn_next.setEnabled(bool(self.history) and self._hist_idx < len(self.history) - 1)
        self.btn_trash.setEnabled(bool(self.history))
        self.hist_label.setText(f"{self._hist_idx + 1}/{len(self.history)}")

        if has_img:
            self.opacity_slider.setValue(int(self._opacity * 100))

    # ---------- painting ----------

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        c = self._content

        if self._drawing:
            if not c.isNull():
                outside = QRegion(self.rect()).subtracted(QRegion(c))
                p.setClipRegion(outside)
                p.fillRect(self.rect(), QColor(6, 24, 43, 110))
                p.setClipping(False)
                pen = QPen(QColor(ACCENT), 2)
                p.setPen(pen)
                p.drawRect(c.adjusted(1, 1, -1, -1))
            else:
                hint = QColor(INK)
                hint.setAlpha(200)
                p.setPen(hint)
                f = p.font()
                f.setPixelSize(15)
                p.setFont(f)
                p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                           "Draw the area: press and drag")
            return

        if self._image is not None:
            p.setOpacity(self._opacity)
            p.drawImage(c, self._image)
            p.setOpacity(1.0)
        else:
            dim = QColor(INK)
            dim.setAlpha(160)
            p.setPen(dim)
            f = p.font()
            f.setPixelSize(13)
            p.setFont(f)
            p.drawText(c, Qt.AlignmentFlag.AlignCenter,
                       "Adjust the frame, annotate, then press the yellow camera button (or Ctrl+C) to capture")

        p.save()
        p.setClipRect(c)
        p.translate(c.topLeft())
        try:
            render_shapes(self._shapes, p)
            if self._pending is not None:
                render_shapes([self._pending], p)
        except Exception:
            log.exception("shape render failed")
        p.restore()
        p.setClipping(False)

        border = QColor(ACCENT)
        border.setAlpha(self._border_alpha)
        pen = QPen(border, 2, Qt.PenStyle.DashLine)
        pen.setDashOffset(self._ant_phase)
        pen.setDashPattern([5.0, 5.0])
        p.setPen(pen)
        p.drawRect(c.adjusted(1, 1, -1, -1))

    # ---------- interaction ----------

    def _finish_draw(self, pos: QPoint):
        start = self._draw_start
        self._draw_start = None
        if start is None:
            return
        r = QRect(start, pos).normalized()
        if r.width() < 12 or r.height() < 12:
            # stray click: restore the previous rectangle if there was one
            restore = self._pre_draw_content
            self._pre_draw_content = None
            if restore is not None and not restore.isNull():
                self._drawing = False
                self._content = restore
                self.unsetCursor()
                self.relayout()
                self._refresh_size_label()
                self._update_ui_state()
            else:
                self._content = QRect()
                self.size_label.hide()
                self.update()
            return
        if r.width() < MIN_W:
            r.setWidth(MIN_W)
        if r.height() < MIN_H:
            r.setHeight(MIN_H)
        self._drawing = False
        self._pre_draw_content = None
        self._content = r
        self.unsetCursor()
        self.set_tool(TOOL_MOVE)
        self.relayout()
        self._refresh_size_label()
        self._update_ui_state()
        self._pulse_border()

    def _grip_at(self, pos: QPoint):
        c = self._content
        for name, pt in (("tl", c.topLeft()), ("tr", c.topRight()),
                         ("bl", c.bottomLeft()), ("br", c.bottomRight())):
            if (QPointF(pos) - QPointF(pt)).manhattanLength() <= GRIP:
                return name
        if self._image is None:  # edge resizing only before a capture
            e = EDGE_ZONE
            if abs(pos.y() - c.top()) <= e and c.left() < pos.x() < c.right():
                return "t"
            if abs(pos.y() - c.bottom()) <= e and c.left() < pos.x() < c.right():
                return "b"
            if abs(pos.x() - c.left()) <= e and c.top() < pos.y() < c.bottom():
                return "l"
            if abs(pos.x() - c.right()) <= e and c.top() < pos.y() < c.bottom():
                return "r"
        return None

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        pos = e.position().toPoint()
        gpos = e.globalPosition().toPoint()
        if self._width_panel.isVisible() and not self._width_panel.geometry().contains(pos):
            self._hide_width_panel()
        if self._textsize_panel.isVisible() and not self._textsize_panel.geometry().contains(pos):
            self._hide_textsize_panel()
        if self._text_editing:
            # first click anywhere releases the editor; it must not
            # fall through and instantly open a new one
            self._commit_text()
            return
        if self._drawing:
            self._draw_start = pos
            self._content = QRect(pos, QSize(0, 0))
            return
        corner = self._grip_at(pos)
        if corner:
            self._resize_corner = corner
            self._resize_ref = QRect(self._content)
            return
        if not self._content.isNull() and not self._content.contains(pos):
            # other tools: dragging on empty space still draws a fresh rectangle
            keep = QRect(self._content)
            self._enter_draw_mode()
            self._pre_draw_content = keep
            self._draw_start = pos
            self._content = QRect(pos, QSize(0, 0))
            return

        view_hist = self._image is not None and not self._is_latest
        if view_hist or self._tool == TOOL_MOVE:
            self._drag_offset = pos - self._content.topLeft()
            self._drag_offset_global = gpos - self.content_global_rect().topLeft()
            return

        cp = QPointF(pos) - QPointF(self._content.topLeft())
        if self._tool == TOOL_PEN:
            self._pending = PenShape(self._color, self._width, cp)
        elif self._tool == TOOL_ARROW:
            self._pending = ArrowShape(self._color, self._width, cp, cp)
        elif self._tool == TOOL_RECT:
            self._pending = RectShape(self._color, self._width, cp, cp)
        elif self._tool == TOOL_FILL:
            self._pending = FilledRectShape(self._color, self._width, cp, cp)
        elif self._tool == TOOL_TEXT:
            self._open_text_editor(pos)
        self.update()

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        gpos = e.globalPosition().toPoint()

        if self._resize_corner and self._resize_ref:
            self._do_resize(pos)
            return
        if self._drawing:
            if self._draw_start is not None:
                self._content = QRect(self._draw_start, pos).normalized()
                self._place_size_label()
                self.size_label.show()
                self.update()
            return
        if self._drag_offset is not None:
            if self._window_covers_desktop():
                tl = pos - self._drag_offset
                tl.setX(max(0, min(tl.x(), self._desktop.width() - self._content.width())))
                tl.setY(max(0, min(tl.y(), self._desktop.height() - self._content.height())))
                self._move_content_to(tl)
            else:
                # compact (hugged) window: the shot is glued to the window,
                # so move the window 1:1 and keep the content fixed inside it
                vg = QGuiApplication.primaryScreen().virtualGeometry()
                gtl = gpos - self._drag_offset_global
                gtl.setX(max(vg.x(), min(gtl.x(),
                         vg.x() + self._desktop.width() - self._content.width())))
                gtl.setY(max(vg.y(), min(gtl.y(),
                         vg.y() + self._desktop.height() - self._content.height())))
                self.move(gtl - self._content.topLeft())
                self.update()
            return
        if self._pending is not None:
            cp = QPointF(pos) - QPointF(self._content.topLeft())
            if isinstance(self._pending, PenShape):
                self._pending.add(cp)
            else:
                self._pending.p2 = cp
            self.update()
            return
        corner = self._grip_at(pos)
        if corner in ("tl", "br"):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif corner in ("tr", "bl"):
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif corner in ("l", "r"):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif corner in ("t", "b"):
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        elif c_contains(self._content, pos) and self._tool != TOOL_MOVE:
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, e):
        pos = e.position().toPoint()
        if self._drawing:
            self._finish_draw(pos)
            return
        if self._resize_corner:
            self._resize_corner = None
            self._resize_ref = None
            self.relayout()
        if self._drag_offset is not None:
            self._drag_offset = None
            self._drag_offset_global = None
            self.relayout()
        if self._pending is not None:
            shape = self._pending
            self._pending = None
            entry = self._current_entry()
            if self._image is not None and self._is_latest and entry is not None:
                # print the finished shape straight into the captured image
                p = QPainter(self._image)
                self._paint_imprint(p, shape)
                p.end()
                entry["image"] = self._image
                entry.setdefault("imprints", []).append(shape)
                new_path = self._persist_baked(self._image)
                if new_path:
                    entry["path"] = new_path
                    entry.setdefault("files", []).append(new_path)
                self.btn_undo.setEnabled(True)
            else:
                self._shapes.append(shape)
                self.btn_undo.setEnabled(True)
            self.update()

    def _do_resize(self, lpos: QPoint):
        """Aspect-locked resize (with an image) anchored at the opposite corner.
        Edge drags (no image yet) move only their own axis. Pure content-rect
        math inside the fixed overlay window."""
        cg0 = self._resize_ref
        corner = self._resize_corner
        horiz = "l" in corner or "r" in corner
        vert = "t" in corner or "b" in corner
        lo_x, hi_x = PAD, self._desktop.width() - PAD
        lo_y, hi_y = PAD, self._desktop.height() - PAD
        aspect = (self._image.width() / max(1, self._image.height())) if self._image is not None else None
        ax = cg0.right() if "l" in corner else cg0.left()
        ay = cg0.bottom() if "t" in corner else cg0.top()

        if not horiz:
            cgx, cw = cg0.left(), cg0.width()
        else:
            cw = max(MIN_W, abs(lpos.x() - ax))
            if "l" in corner:
                cgx = max(lo_x, ax - cw)
                cw = max(MIN_W, ax - cgx)
            else:
                cw = max(MIN_W, min(cw, hi_x - ax))
                cgx = ax

        if not vert:
            cgy, ch = cg0.top(), cg0.height()
        else:
            if aspect is not None and horiz:
                ch = max(MIN_H, round(cw / aspect))
            else:
                ch = max(MIN_H, abs(lpos.y() - ay))
            if "t" in corner:
                cgy = max(lo_y, ay - ch)
                ch = max(MIN_H, ay - cgy)
            else:
                ch = max(MIN_H, min(ch, hi_y - ay))
                cgy = ay

        if aspect is not None and horiz and vert:
            # vertical clamps may have adjusted ch: re-couple the width
            cw = max(MIN_W, round(ch * aspect))
            if "l" in corner:
                cgx = ax - cw

        self._content = QRect(QPoint(cgx, cgy), QSize(cw, ch))
        self.relayout()

    def contextMenuEvent(self, e):
        menu = QMenu(self)
        for name in PALETTE:
            act = menu.addAction(name)
            act.triggered.connect(lambda _=False, n=name: self._set_color(QColor(n)))
        menu.exec(e.globalPos())

    # ---------- text tool ----------

    def _open_text_editor(self, pos: QPoint):
        self._text_editing = True
        self._set_typing_mode(True)
        self.text_edit.setGeometry(QRect(pos, QSize(260, self._text_size + 8)))
        self.text_edit.show()
        self.text_edit.raise_()
        self.text_edit.setFocus()

    def _commit_text(self):
        if not self._text_editing:
            return
        text = self.text_edit.text().strip()
        tl = self.text_edit.geometry().topLeft() - self._content.topLeft()
        # anchor at the editor's own baseline: what you saw is what commits
        fm = QFontMetrics(TextShape(self._color, self._text_size, QPointF(tl), "").font())
        line_h = fm.height()
        baseline_y = (self.text_edit.height() - line_h) // 2 + fm.ascent()
        baseline = QPointF(tl.x() + 2, tl.y() + baseline_y)
        self._close_text_editor()
        if text:
            self._shapes.append(TextShape(self._color, self._text_size, baseline, text))
            self.btn_undo.setEnabled(True)
        self.setFocus()
        self.update()

    def _cancel_text(self):
        self._close_text_editor()
        self.setFocus()

    def _close_text_editor(self):
        self._text_editing = False
        self._set_typing_mode(False)
        self.text_edit.blockSignals(True)
        self.text_edit.clear()
        self.text_edit.hide()
        self.text_edit.blockSignals(False)

    # ---------- capture ----------

    def _on_capture_pressed(self):
        if not self._busy:
            QTimer.singleShot(0, self.do_capture)

    @Slot()
    def do_capture(self):
        if self._busy:
            return
        self._busy = True
        log.info("capture: start")
        try:
            self._capture_impl()
            log.info("capture: done")
        except Exception as ex:
            log.exception("capture failed: %s", ex)
            self.show()
            self.raise_()
            self.size_label.setText(f"capture failed: {ex}")
            self.size_label.show()
            self.relayout()
            QTimer.singleShot(4000, self._refresh_size_label)
        finally:
            self._busy = False

    def _capture_impl(self):
        if self._drag_timer is not None and self._drag_timer.isActive():
            self._drag_timer.stop()
        self.hide()
        QApplication.processEvents()
        time.sleep(0.15)

        img, src_path = capture.capture_screen()
        dpr = capture.device_pixel_ratio(self.screen())
        crop = capture.crop_image(img, self.content_global_rect(), dpr)

        if self._shapes:
            p = QPainter(crop)
            p.scale(dpr, dpr)
            render_shapes(self._shapes, p)
            p.end()

        path = self._save_image(crop)
        QApplication.clipboard().setImage(crop)

        try:
            Path(src_path).unlink()
        except OSError:
            pass

        self.history.append({"image": crop, "base": crop.copy(), "imprints": [],
                             "path": path, "files": [path],
                             "logical": QSize(self._content.width(), self._content.height())})
        limit = int(self.settings["history_limit"])
        if len(self.history) > limit:
            self.history = self.history[-limit:]
        self._hist_idx = len(self.history) - 1

        self._image = crop
        self._image_px = (crop.width(), crop.height())
        self._is_latest = True
        self._shapes = []

        self.show()
        self.raise_()
        self.activateWindow()
        set_always_on_top(self, self._pinned)
        self._refresh_size_label()
        self._update_ui_state()
        log.info("capture: shown visible=%s geo=%dx%d+%d+%d",
                 self.isVisible(), self.width(), self.height(), self.x(), self.y())

        if QGuiApplication.mouseButtons() & Qt.LeftButton:
            QTimer.singleShot(0, self._hold_drag)

    def _hold_drag(self):
        """Drag the shot around while the button is still held: the content
        rect follows the cursor inside the fixed overlay window."""
        if not (QGuiApplication.mouseButtons() & Qt.LeftButton):
            return
        last = QCursor.pos()

        def tick():
            if not self.isVisible() or not (QGuiApplication.mouseButtons() & Qt.LeftButton):
                drag_timer.stop()
                return
            nonlocal last
            pos = QCursor.pos()
            d = pos - last
            if d.isNull():
                return
            if self._window_covers_desktop():
                tl = self._content.topLeft() + d
                tl.setX(max(0, min(tl.x(), self._desktop.width() - self._content.width())))
                tl.setY(max(0, min(tl.y(), self._desktop.height() - self._content.height())))
                self._move_content_to(tl)
            else:
                self.move(self.pos() + d)
                self.update()
            last = pos

        drag_timer = QTimer(self)
        drag_timer.timeout.connect(tick)
        self._drag_timer = drag_timer
        drag_timer.start(25)

    def _save_image(self, img: QImage) -> str:
        import datetime as dt

        out_dir = self.settings.save_dir_path()
        out_dir.mkdir(parents=True, exist_ok=True)
        fmt = str(self.settings["format"]).lstrip(".").lower()
        base = dt.datetime.now().strftime(str(self.settings["filename_pattern"]))
        path = out_dir / f"{base}.{fmt}"
        n = 1
        while path.exists():
            path = out_dir / f"{base}-{n}.{fmt}"
            n += 1
        if not img.save(str(path)):
            raise capture.CaptureError(f"could not save {path}")
        return str(path)

    def _refresh_size_label(self):
        if self._image is not None:
            self.size_label.setText(f"{self._image.width()}×{self._image.height()}")
        else:
            c = self._content
            self.size_label.setText(f"{round(c.width())}×{round(c.height())}")
        self.size_label.show()
        self.relayout()

    # ---------- history / copy ----------

    def hist_step(self, delta: int):
        if self._text_editing:
            return
        self.hist_goto(self._hist_idx + delta)

    def hist_goto(self, idx: int):
        if not self.history:
            return
        idx = max(0, min(idx, len(self.history) - 1))
        if idx == self._hist_idx and self._image is not None:
            return
        self._hist_idx = idx
        entry = self.history[idx]
        self._image = entry["image"]
        self._image_px = (entry["image"].width(), entry["image"].height())
        self._is_latest = idx == len(self.history) - 1
        self._shapes = []
        self._pending = None
        if not self._fullscreen:
            self._content.setSize(entry["logical"])
            self.relayout()
        self._refresh_size_label()
        self._update_ui_state()

    def copy_image(self):
        if self._image is not None:
            QApplication.clipboard().setImage(self._image)

    # ---------- misc ----------

    def _end_session(self):
        """Clear the interactive state but keep the session history."""
        self._shapes = []
        self._pending = None
        self._image = None
        self._image_px = None
        self._is_latest = False
        if self._width_panel.isVisible():
            self._width_panel.hide()
        if self._textsize_panel.isVisible():
            self._textsize_panel.hide()
        if self._text_editing:
            self._close_text_editor()
        self._update_ui_state()

    def _dismiss(self):
        """Esc / ✕: hide. History is kept until cleared with the trash button."""
        if not self._content.isNull() and self._content.width() > 0:
            self._last_rect = QRect(self._content)
        self.hide()

    def _on_escape(self):
        if self._width_panel.isVisible():
            self._hide_width_panel()
        elif self._textsize_panel.isVisible():
            self._hide_textsize_panel()
        elif self._text_editing:
            self._cancel_text()
        elif self._drawing:
            self._dismiss()
        else:
            self._dismiss()

    @Slot(str)
    def _on_ipc(self, cmd: str):
        cmd = cmd.lstrip("-")  # accept "--toggle" style from the launcher
        if cmd in ("toggle", "show"):
            log.info("hotkey: showing frame at cursor")
            self.open_at_cursor()
        elif cmd.startswith("showat:"):
            xs, ys = cmd.split(":", 1)[1].split(",")
            self.open_at(int(xs), int(ys))
        elif cmd == "hide":
            self.hide()
        elif cmd == "capture":
            self.do_capture()
        elif cmd == "debug":
            log.info("state: tool=%s editing=%s content=%s window=%s editor=%s width=%s text_size=%s shapes=%d",
                     self._tool, self._text_editing, self._content, self.geometry(),
                     self.text_edit.geometry(), self._width, self._text_size, len(self._shapes))
        elif cmd == "quit":
            QApplication.quit()


def c_contains(r: QRect, p: QPoint) -> bool:
    return r.contains(p)
