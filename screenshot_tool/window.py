import logging
import time
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRect, QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import (
    QColor,
    QRegion,
    QCursor,
    QGuiApplication,
    QImage,
    QKeySequence,
    QPainter,
    QPen,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QColorDialog,
    QFrame,
    QHBoxLayout,
    QVBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QSlider,
    QToolButton,
    QWidget,
    QWidgetAction,
)

from . import capture, icons
from .settings import Settings
from .shapes import ArrowShape, PenShape, RectShape, TextShape, render_shapes

log = logging.getLogger("screenshot_tool")

ACCENT = "#00bcd4"
CHROME_FG = "#eceff1"
TB_H = 38   # toolbar row height
GAP = 6     # gap between frame and toolbar row
PAD = 6     # window margin around the frame (room for the handle dots)
BTN = 30    # icon button size
GRIP = 10   # grip hit radius
MIN_W, MIN_H = 240, 160

TOOL_MOVE, TOOL_PEN, TOOL_ARROW, TOOL_RECT, TOOL_TEXT = range(5)
TOOL_NAMES = ["move", "pen", "arrow", "rect", "text"]
PALETTE = ["#e53935", "#fb8c00", "#fdd835", "#43a047", "#1e88e5", "#8e24aa", "#ffffff", "#000000"]

BTN_CSS = f"""
QFrame {{ background: transparent; }}
QToolButton {{ background: rgba(38, 50, 56, 215); border: none; border-radius: 6px; padding: 0; }}
QToolButton:hover {{ background: rgba(55, 71, 79, 240); }}
QToolButton:checked {{ background: {ACCENT}; }}
QToolButton:disabled {{ background: rgba(38, 50, 56, 110); }}
QSlider {{ background: transparent; }}
QSlider::groove:horizontal {{ height: 3px; background: rgba(236,239,241,70); border-radius: 1px; }}
QSlider::handle:horizontal {{ width: 10px; height: 10px; margin: -4px 0; border-radius: 5px; background: {ACCENT}; }}
"""

CAPTURE_CSS = """
QToolButton { background: #43a047; border-radius: 17px; }
QToolButton:hover { background: #4caf50; }
QToolButton:pressed { background: #2e7d32; }
"""

LABEL_CSS = f"""
QLabel {{
  background: {ACCENT}; color: #102027; font-size: 11px; font-weight: bold;
  padding: 1px 6px; border-radius: 4px;
}}
"""


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


class SnipperWindow(QWidget):
    ipc_command = Signal(str)

    def __init__(self, settings: Settings):
        super().__init__(None)
        self.settings = settings
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.X11BypassWindowManagerHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMouseTracking(True)

        self._content = QRect(0, 0, settings["default_width"], settings["default_height"])
        self._strip_mode = "below"
        self._fullscreen = False
        self._pre_fs = None
        self._pinned = False
        self._busy = False

        self._image = None
        self._image_px = None
        self._is_latest = False
        self._opacity = max(0.2, min(1.0, settings["default_opacity"] / 100))

        self._tool = TOOL_MOVE
        self._color = QColor(settings["color"])
        self._width = int(settings["stroke_width"])
        self._shapes = []

        self.history = []
        self._hist_idx = -1

        self._pending = None
        self._drag_offset = None
        self._resize_corner = None
        self._resize_ref = None
        self._text_editing = False
        self._border_alpha = 255
        self._drawing = False
        self._draw_start = None
        self._last_rect = None
        self._pre_draw_content = None
        self._drag_timer = None

        self._build_ui()

        QShortcut(QKeySequence(Qt.Key_Escape), self, self._on_escape)
        QShortcut(QKeySequence("Ctrl+Z"), self, self.undo)
        QShortcut(QKeySequence(Qt.Key_Left), self, lambda: self.hist_step(-1))
        QShortcut(QKeySequence(Qt.Key_Right), self, lambda: self.hist_step(1))
        QShortcut(QKeySequence("Ctrl+C"), self,
                  lambda: None if self._text_editing else self.do_capture())
        for key, t in (("m", TOOL_MOVE), ("p", TOOL_PEN), ("a", TOOL_ARROW),
                       ("r", TOOL_RECT), ("t", TOOL_TEXT)):
            QShortcut(QKeySequence(key), self,
                      lambda t=t: None if self._text_editing else self.set_tool(t))

        self.ipc_command.connect(self._on_ipc)

    # ---------- UI construction ----------

    def _build_ui(self):
        self.bar_bottom = QFrame(self)
        self.bar_top = QFrame(self)
        self.bar_right = QFrame(self)
        for bar in (self.bar_bottom, self.bar_top, self.bar_right):
            bar.setStyleSheet(BTN_CSS)
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
            b.clicked.connect(cb)
            return b

        self._mkbtn = lambda *a, **k: mkbtn(self.bar_bottom, *a, **k)

        # tools (bottom edge): move, pen, arrow, rect, text, color, width
        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)
        self.btn_move = mkbtn(self.bar_bottom, "move", "Move / transform the frame (M)", lambda: self.set_tool(TOOL_MOVE), True)
        self.btn_pen = mkbtn(self.bar_bottom, "pen", "Pen (P)", lambda: self.set_tool(TOOL_PEN), True)
        self.btn_arrow = mkbtn(self.bar_bottom, "arrow", "Arrow (A)", lambda: self.set_tool(TOOL_ARROW), True)
        self.btn_rect = mkbtn(self.bar_bottom, "rect", "Rectangle (R)", lambda: self.set_tool(TOOL_RECT), True)
        self.btn_text = mkbtn(self.bar_bottom, "text", "Text (T)", lambda: self.set_tool(TOOL_TEXT), True)
        for i, b in enumerate((self.btn_move, self.btn_pen, self.btn_arrow, self.btn_rect, self.btn_text)):
            self.tool_group.addButton(b, i)
            lay_b.addWidget(b)
        self.btn_move.setChecked(True)

        self.btn_color = mkbtn(self.bar_bottom, "pen", "Stroke color", self._open_color_picker)
        self.btn_color.setIcon(icons.color_dot_icon(self._color.name()))
        lay_b.addWidget(self.btn_color)
        self.btn_width = mkbtn(self.bar_bottom, "width", "Stroke width", self._open_width_popup)
        lay_b.addWidget(self.btn_width)

        # adjust (right edge): undo, 1:1, bake, reset, fullscreen
        self.btn_undo = mkbtn(self.bar_right, "undo", "Undo (Ctrl+Z)", self.undo)
        self.btn_11 = QToolButton(self.bar_right)
        self.btn_11.setText("1:1")
        self.btn_11.setFixedSize(BTN, BTN)
        self.btn_11.setToolTip("Reset scale: frame shows the image pixel-per-pixel")
        self.btn_11.setStyleSheet(
            f"QToolButton {{ background: rgba(38,50,56,215); border: none; border-radius: 6px;"
            f" color: {CHROME_FG}; font-size: 10px; font-weight: bold; }}"
            f"QToolButton:hover {{ background: rgba(55,71,79,240); }}")
        self.btn_11.clicked.connect(self.reset_scale)
        self.btn_bake = mkbtn(self.bar_right, "bake", "Bake: commit the resized scale into the image", self.bake_scale)
        self.btn_redraw = mkbtn(self.bar_right, "redraw", "Close this rectangle and draw a new one", self.start_redraw)
        self.btn_reset = mkbtn(self.bar_right, "reset", "Reset: clear drawings, default size", self.reset)
        self.btn_full = mkbtn(self.bar_right, "full", "Fullscreen on the current monitor", self.toggle_fullscreen)
        for b in (self.btn_undo, self.btn_11, self.btn_bake, self.btn_redraw, self.btn_reset, self.btn_full):
            lay_r.addWidget(b)

        # actions (top edge): camera, copy, pin, opacity, history, close
        self.capture_btn = QToolButton(self.bar_top)
        self.capture_btn.setIcon(icons.svg_icon("camera", "#ffffff"))
        self.capture_btn.setIconSize(QSize(20, 20))
        self.capture_btn.setFixedSize(34, 34)
        self.capture_btn.setStyleSheet(CAPTURE_CSS)
        self.capture_btn.setToolTip("Capture: save to folder + clipboard (hold to drag the shot)")
        self.capture_btn.pressed.connect(self._on_capture_pressed)
        lay_t.addWidget(self.capture_btn)

        self.btn_copy = mkbtn(self.bar_top, "copy", "Copy image to clipboard", self.copy_image)
        lay_t.addWidget(self.btn_copy)
        self.btn_pin = mkbtn(self.bar_top, "pin", "Pin on top (hide chrome)", self.toggle_pin, True)
        lay_t.addWidget(self.btn_pin)

        self.opacity_slider = QSlider(Qt.Horizontal, self.bar_top)
        self.opacity_slider.setRange(20, 100)
        self.opacity_slider.setValue(int(self._opacity * 100))
        self.opacity_slider.setFixedWidth(64)
        self.opacity_slider.setToolTip("Image opacity")
        self.opacity_slider.valueChanged.connect(self._on_opacity)
        lay_t.addWidget(self.opacity_slider)

        self.btn_prev = mkbtn(self.bar_top, "prev", "Previous capture (←)", lambda: self.hist_step(-1))
        self.hist_label = QLabel("0/0", self.bar_top)
        self.hist_label.setStyleSheet(f"QLabel {{ color: {CHROME_FG}; font-size: 11px; background: transparent; }}")
        self.btn_next = mkbtn(self.bar_top, "next", "Next capture (→)", lambda: self.hist_step(1))
        lay_t.addWidget(self.btn_prev)
        lay_t.addWidget(self.hist_label)
        lay_t.addWidget(self.btn_next)
        self.btn_trash = mkbtn(self.bar_top, "trash", "Clear history (manual)", self.clear_history)
        lay_t.addWidget(self.btn_trash)
        self.btn_close = mkbtn(self.bar_top, "close", "Hide (Esc)", self._dismiss)
        lay_t.addWidget(self.btn_close)

        self.size_label = QLabel(self)
        self.size_label.setStyleSheet(LABEL_CSS)
        self.size_label.hide()

        self.text_edit = QLineEdit(self)
        self.text_edit.hide()
        self._refresh_text_style()
        self.text_edit.returnPressed.connect(self._commit_text)
        self.text_edit.editingFinished.connect(self._commit_text)

        self._update_ui_state()

    # ---------- geometry ----------

    def content_global_rect(self) -> QRect:
        # from REQUESTED geometry: mapToGlobal can lag behind the WM-acked
        # position right after moves/resizes and must not drive placement
        return QRect(QPoint(self.x() + self._content.x(), self.y() + self._content.y()),
                     self._content.size())

    def relayout(self):
        """Place the edge bars around the frame inside the fixed overlay
        window. Bottom = tools, top = actions, right = adjust; each flips
        inside the frame when there is no room outside."""
        if self._drawing:
            self.bar_bottom.hide()
            self.bar_top.hide()
            self.bar_right.hide()
            self.clearMask()
            self.update()
            return

        for bar in (self.bar_bottom, self.bar_top, self.bar_right):
            bar.show()

        cw, ch = self._content.width(), self._content.height()
        d_w, d_h = self._desktop.width(), self._desktop.height()

        bb = self.bar_bottom.sizeHint(); bb.setWidth(max(bb.width(), 40)); bb.setHeight(TB_H)
        tb = self.bar_top.sizeHint(); tb.setWidth(max(tb.width(), 40)); tb.setHeight(TB_H)
        rb = self.bar_right.sizeHint(); rb.setWidth(TB_H); rb.setHeight(max(rb.height(), 40))

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

        self._place_size_label()
        self._update_mask()
        self.update()

    def _place_size_label(self):
        lbl = self.size_label.sizeHint() if self.size_label.text() else QSize(60, 18)
        lx = self._content.right() - lbl.width() - 8
        ly = self._content.bottom() - lbl.height() - 8
        self.size_label.setGeometry(
            max(self._content.left() + 4, lx),
            max(self._content.top() + 4, ly),
            lbl.width(), lbl.height())

    def open_at_cursor(self):
        self.open_last()

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

    def open_last(self):
        """Hotkey default: rectangle at its previous position, ready to use;
        dragging on empty space draws a fresh one."""
        self._end_session()
        vg = QGuiApplication.primaryScreen().virtualGeometry()
        self._desktop = QRect(QPoint(0, 0), QSize(vg.width(), vg.height()))
        self.setGeometry(vg.x(), vg.y(), self._desktop.width(), self._desktop.height())
        if self._last_rect is not None and not self._last_rect.isNull():
            r = self._last_rect
            r = r.intersected(QRect(QPoint(0, 0), self._desktop.size()))
            if r.width() >= MIN_W // 2:
                r.setWidth(max(MIN_W, r.width()))
                r.setHeight(max(MIN_H, r.height()))
            self._drawing = False
            self._content = r
            self.unsetCursor()
            self.relayout()
            self._refresh_size_label()
            self._update_ui_state()
        else:
            self._enter_draw_mode()
        self.show()
        self.raise_()
        self.activateWindow()
        if not self._drawing:
            self._pulse_border()

    def start_redraw(self):
        """Close the current rectangle and draw a new one."""
        self._enter_draw_mode()

    def open_at(self, cx: int, cy: int):
        """Open with a ready frame at the given point (scripting/tests)."""
        self._end_session()
        vg = QGuiApplication.primaryScreen().virtualGeometry()
        self._desktop = QRect(QPoint(0, 0), QSize(vg.width(), vg.height()))
        self._drawing = False
        self._draw_start = None
        self._fullscreen = False
        self._pinned = False
        self.btn_pin.setChecked(False)
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
        self._pulse_border()

    def _end_session(self):
        self._shapes = []
        self._pending = None
        self._image = None
        self._is_latest = False
        if self._text_editing:
            self._close_text_editor()
        self._update_ui_state()

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
            return
        self._tool = tool
        for i, b in enumerate((self.btn_move, self.btn_pen, self.btn_arrow, self.btn_rect, self.btn_text)):
            b.setChecked(i == tool)
            b.setIcon(icons.svg_icon(TOOL_NAMES[i], icons.DARK if i == tool else icons.LIGHT))

    def _refresh_text_style(self):
        self.text_edit.setStyleSheet(
            f"QLineEdit {{ background: rgba(255,255,255,230); color: {self._color.name()}; "
            f"font-size: {10 + self._width * 4}px; border: 1px dashed {self._color.name()}; }}"
        )

    def _set_color(self, color: QColor):
        self._color = QColor(color)
        self.btn_color.setIcon(icons.color_dot_icon(self._color.name()))
        self._refresh_text_style()
        self.update()

    def _set_width(self, w: int):
        self._width = int(w)
        self._refresh_text_style()
        self.update()

    def _open_color_picker(self):
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{ background: rgba(38,50,56,242); border: 1px solid rgba(255,255,255,40);"
            f" border-radius: 8px; padding: 6px; }}"
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
                f"QToolButton {{ background: {name}; border-radius: 5px;"
                f" border: 1px solid rgba(255,255,255,70); }}"
            )
            b.clicked.connect(lambda _=False, n=name: (menu.close(), self._set_color(QColor(n))))
            h.addWidget(b)
        custom = QToolButton(row)
        custom.setFixedSize(22, 22)
        custom.setCursor(Qt.PointingHandCursor)
        custom.setText("+")
        custom.setStyleSheet(
            f"QToolButton {{ color: {CHROME_FG}; background: rgba(255,255,255,30);"
            f" border-radius: 5px; font-weight: bold; }}"
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

    def _open_width_popup(self):
        panel = QWidget(None, Qt.Popup | Qt.FramelessWindowHint)
        panel.setAttribute(Qt.WA_TranslucentBackground)
        panel.setStyleSheet("QWidget { background: rgba(38, 50, 56, 242); border-radius: 8px; }")
        v = QHBoxLayout(panel)
        v.setContentsMargins(14, 8, 14, 10)
        v.setSpacing(8)
        prev = _WidthPreview(self._color, self._width)
        slider = QSlider(Qt.Horizontal)
        slider.setRange(1, 30)
        slider.setValue(self._width)
        slider.setFixedWidth(190)
        slider.setCursor(Qt.PointingHandCursor)

        def on_change(val):
            self._set_width(val)
            prev.set_state(self._color, val)

        slider.valueChanged.connect(on_change)
        v.addWidget(prev)
        v.addWidget(slider)
        panel.adjustSize()
        gp = self.btn_width.mapToGlobal(QPoint(0, 0))
        panel.move(gp.x() - 24, max(4, gp.y() - panel.height() - 8))
        panel.show()

    def undo(self):
        if self._text_editing:
            return
        if self._shapes:
            self._shapes.pop()
            self.btn_undo.setEnabled(bool(self._shapes) or self._imprints())
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
        else:
            self._persist_baked(img)
        self.btn_undo.setEnabled(False)
        self._refresh_size_label()
        self.update()

    def clear_history(self):
        self.history = []
        self._hist_idx = -1
        self._image = None
        self._image_px = None
        self._is_latest = False
        self._update_ui_state()
        self.relayout()

    def reset(self):
        """Clear drawings and return the frame to its pristine state."""
        if self._text_editing:
            self._cancel_text()
        self._shapes = []
        self._pending = None
        self._image = None
        self._image_px = None
        self._is_latest = False
        self._opacity = max(0.2, min(1.0, self.settings["default_opacity"] / 100))
        self.opacity_slider.setValue(int(self._opacity * 100))
        self.set_tool(TOOL_MOVE)
        self.btn_undo.setEnabled(False)
        if not self._fullscreen:
            self._content.setSize(QSize(self.settings["default_width"], self.settings["default_height"]))
            self.relayout()
        self._refresh_size_label()
        self._update_ui_state()
        self.update()

    def _on_opacity(self, v):
        self._opacity = v / 100
        self.update()

    def toggle_pin(self):
        self._pinned = not self._pinned
        self.btn_pin.setChecked(self._pinned)
        self._update_ui_state()
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
        tools_on = has_img and not view_hist and not self._pinned

        for b in (self.btn_move, self.btn_pen, self.btn_arrow, self.btn_rect, self.btn_text):
            b.setEnabled(tools_on or not has_img)
        self.btn_color.setEnabled(tools_on or not has_img)
        self.btn_width.setEnabled(tools_on or not has_img)
        self.btn_undo.setEnabled(bool(self._shapes))

        self.btn_copy.setVisible(has_img)
        self.opacity_slider.setVisible(has_img)
        self.btn_pin.setVisible(has_img)
        self.btn_11.setVisible(has_img)
        self.btn_bake.setVisible(has_img)
        self.btn_redraw.setVisible(not self._drawing)
        self.btn_trash.setVisible(bool(self.history))
        self.btn_prev.setEnabled(bool(self.history) and self._hist_idx > 0)
        self.btn_next.setEnabled(bool(self.history) and self._hist_idx < len(self.history) - 1)
        self.hist_label.setText(f"{self._hist_idx + 1}/{len(self.history)}")
        hist_on = bool(self.history)
        self.hist_label.setVisible(hist_on)
        self.btn_prev.setVisible(hist_on)
        self.btn_next.setVisible(hist_on)

        self.capture_btn.setVisible(not self._pinned)
        if self._drawing:
            for bar in (self.bar_bottom, self.bar_top, self.bar_right):
                bar.hide()
        else:
            self.bar_bottom.setVisible(not self._pinned)
            self.bar_right.setVisible(not self._pinned)
            # top bar stays: pin toggle + close remain reachable while pinned
        if has_img:
            self.opacity_slider.setValue(int(self._opacity * 100))

    # ---------- painting ----------

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        c = self._content

        if self._drawing:
            if not self._content.isNull():
                # dim everything outside the rect being pulled
                outside = QRegion(self.rect()).subtracted(QRegion(self._content))
                p.setClipRegion(outside)
                p.fillRect(self.rect(), QColor(0, 0, 0, 70))
                p.setClipping(False)
                pen = QPen(QColor(ACCENT), 2)
                p.setPen(pen)
                p.drawRect(self._content.adjusted(1, 1, -1, -1))
            else:
                p.setPen(QColor(255, 255, 255, 120))
                f = p.font()
                f.setPixelSize(15)
                p.setFont(f)
                p.drawText(self.rect(), Qt.AlignCenter, "Draw the area: press and drag")
            return

        if self._image is not None:
            p.setOpacity(self._opacity)
            p.drawImage(c, self._image)
            p.setOpacity(1.0)
        elif not self._pinned:
            p.setPen(QColor(255, 255, 255, 90))
            f = p.font()
            f.setPixelSize(13)
            p.setFont(f)
            p.drawText(c, Qt.AlignCenter,
                       "Adjust the frame, annotate, then press the green camera button to capture")

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
        p.setPen(QPen(border, 2))
        p.drawRect(c.adjusted(1, 1, -1, -1))

        if not self._pinned:
            p.setRenderHint(QPainter.Antialiasing, True)
            inset = 6 if self._fullscreen else 0
            for pt in (c.topLeft(), c.topRight(), c.bottomLeft(), c.bottomRight()):
                d = QPointF(
                    inset if pt.x() <= c.center().x() else -inset,
                    inset if pt.y() <= c.center().y() else -inset,
                )
                p.setPen(QPen(border, 1.5))
                p.setBrush(border)
                p.drawEllipse(QPointF(pt) + d, 4.5, 4.5)

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
        if self._pinned:
            return None
        c = self._content
        for name, pt in (("tl", c.topLeft()), ("tr", c.topRight()),
                         ("bl", c.bottomLeft()), ("br", c.bottomRight())):
            if (QPointF(pos) - QPointF(pt)).manhattanLength() <= GRIP:
                return name
        return None

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        pos = e.position().toPoint()
        gpos = e.globalPosition().toPoint()
        if self._drawing:
            self._draw_start = pos
            self._content = QRect(pos, QSize(0, 0))
            return
        if not self._content.isNull() and not self._content.contains(pos):
            # other tools: dragging on empty space still draws a fresh rectangle
            keep = QRect(self._content)
            self._enter_draw_mode()
            self._pre_draw_content = keep
            self._draw_start = pos
            self._content = QRect(pos, QSize(0, 0))
            return
        corner = self._grip_at(pos)
        if corner:
            self._resize_corner = corner
            self._resize_ref = QRect(self._content)
            return
        if not self._content.contains(pos):
            return

        view_hist = self._image is not None and not self._is_latest
        if self._pinned or view_hist or self._tool == TOOL_MOVE:
            self._drag_offset = pos - self._content.topLeft()
            return
        if self._text_editing:
            self._commit_text()

        cp = QPointF(pos) - QPointF(self._content.topLeft())
        if self._tool == TOOL_PEN:
            self._pending = PenShape(self._color, self._width, cp)
        elif self._tool == TOOL_ARROW:
            self._pending = ArrowShape(self._color, self._width, cp, cp)
        elif self._tool == TOOL_RECT:
            self._pending = RectShape(self._color, self._width, cp, cp)
        elif self._tool == TOOL_TEXT:
            self._open_text_editor(pos)
        self.update()

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        gpos = e.globalPosition().toPoint()

        if self._drawing:
            if self._draw_start is not None:
                self._content = QRect(self._draw_start, pos).normalized()
                self._place_size_label()
                self.size_label.show()
                self.update()
            return
        if self._resize_corner and self._resize_ref:
            self._do_resize(pos)
            return
        if self._drag_offset is not None:
            tl = pos - self._drag_offset
            tl.setX(max(0, min(tl.x(), self._desktop.width() - self._content.width())))
            tl.setY(max(0, min(tl.y(), self._desktop.height() - self._content.height())))
            self._content.moveTopLeft(tl)
            self.relayout()
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
            self.setCursor(Qt.SizeFDiagCursor)
        elif corner in ("tr", "bl"):
            self.setCursor(Qt.SizeBDiagCursor)
        elif self._content.contains(pos) and not (self._pinned or self._tool == TOOL_MOVE):
            self.setCursor(Qt.CrossCursor)
        else:
            self.setCursor(Qt.ArrowCursor)

    def mouseReleaseEvent(self, e):
        if self._drawing:
            self._finish_draw(e.position().toPoint())
            return
        if self._resize_corner:
            self._resize_corner = None
            self._resize_ref = None
            self.relayout()
        if self._drag_offset is not None:
            self._drag_offset = None
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
                self.btn_undo.setEnabled(True)
            else:
                self._shapes.append(shape)
                self.btn_undo.setEnabled(True)
            self.update()

    def _update_mask(self):
        """Restrict window painting + input to frame, toolbar and label; clicks
        elsewhere (the rest of the overlay) pass through to the desktop."""
        region = QRegion(self._content.adjusted(-8, -8, 8, 8))
        for bar in (self.bar_bottom, self.bar_top, self.bar_right):
            region = region.united(QRegion(bar.geometry()))
        if self.size_label.isVisible():
            region = region.united(QRegion(self.size_label.geometry()))
        self.setMask(region)

    def _do_resize(self, lpos: QPoint):
        """Aspect-locked resize anchored at the opposite corner — pure
        content-rect math inside the fixed overlay window. No window geometry
        ever changes, so there is nothing the compositor can present stale."""
        cg0 = self._resize_ref
        corner = self._resize_corner
        grow_left = "l" in corner
        grow_top = "t" in corner
        ax = cg0.right() if grow_left else cg0.left()
        ay = cg0.bottom() if grow_top else cg0.top()
        lo_x, hi_x = PAD, self._desktop.width() - PAD
        lo_y, hi_y = PAD, self._desktop.height() - PAD

        iw = max(MIN_W, abs(lpos.x() - ax))
        ih = max(MIN_H, abs(lpos.y() - ay))

        if self._image is not None:
            aspect = self._image.width() / max(1, self._image.height())
            if ih * aspect >= iw:
                ch, cw = ih, round(ih * aspect)
            else:
                cw, ch = iw, round(iw / aspect)
            cw = max(MIN_W, cw)
            ch = max(MIN_H, ch)
            if grow_left:
                cgx = max(lo_x, ax - cw)
                cw = max(MIN_W, ax - cgx)
                ch = max(MIN_H, round(cw / aspect))
            else:
                cw = max(MIN_W, min(cw, hi_x - ax))
                cgx = ax
                ch = max(MIN_H, round(cw / aspect))
            if grow_top:
                cgy = max(lo_y, ay - ch)
                ch = max(MIN_H, ay - cgy)
                cw = max(MIN_W, round(ch * aspect))
                if grow_left:
                    cgx = ax - cw
            else:
                ch = max(MIN_H, min(ch, hi_y - ay))
                cgy = ay
                cw = max(MIN_W, round(ch * aspect))
                if grow_left:
                    cgx = ax - cw
        else:
            if grow_left:
                cgx = max(lo_x, ax - iw)
                cw = max(MIN_W, ax - cgx)
            else:
                cw = max(MIN_W, min(iw, hi_x - ax))
                cgx = ax
            if grow_top:
                cgy = max(lo_y, ay - ih)
                ch = max(MIN_H, ay - cgy)
            else:
                ch = max(MIN_H, min(ih, hi_y - ay))
                cgy = ay

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
        self.text_edit.setGeometry(QRect(self._content.topLeft() + pos, QSize(260, 10 + self._width * 4)))
        self.text_edit.show()
        self.text_edit.raise_()
        self.text_edit.setFocus()

    def _commit_text(self):
        if not self._text_editing:
            return
        text = self.text_edit.text().strip()
        tl = self.text_edit.geometry().topLeft() - self._content.topLeft()
        self._close_text_editor()
        if text:
            self._shapes.append(TextShape(self._color, self._width, QPointF(tl), text))
            self.btn_undo.setEnabled(True)
        self.setFocus()
        self.update()

    def _cancel_text(self):
        self._close_text_editor()
        self.setFocus()

    def _close_text_editor(self):
        self._text_editing = False
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
                             "path": path,
                             "logical": QSize(self._content.width(), self._content.height())})
        limit = int(self.settings["history_limit"])
        if len(self.history) > limit:
            self.history = self.history[-limit:]
        self._hist_idx = len(self.history) - 1

        self._image = crop
        self._image_px = (crop.width(), crop.height())
        self._is_latest = True
        self._shapes = []
        self.set_tool(TOOL_MOVE)

        self.show()
        self.raise_()
        self.activateWindow()
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
        vg = QGuiApplication.primaryScreen().virtualGeometry()
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
            tl = self._content.topLeft() + QPoint(d.x(), d.y())
            tl.setX(max(0, min(tl.x(), self._desktop.width() - self._content.width())))
            tl.setY(max(0, min(tl.y(), self._desktop.height() - self._content.height())))
            self._content.moveTopLeft(tl)
            self.relayout()
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
        self.set_tool(TOOL_MOVE)
        if not self._fullscreen:
            self._content.setSize(entry["logical"])
            self.relayout()
        self._refresh_size_label()
        self._update_ui_state()

    def copy_image(self):
        if self._image is not None:
            QApplication.clipboard().setImage(self._image)

    # ---------- misc ----------

    def _dismiss(self):
        """Esc / ✕: hide. History is kept until cleared with the trash button."""
        if not self._content.isNull() and self._content.width() > 0:
            self._last_rect = QRect(self._content)
        self.hide()

    def _on_escape(self):
        if self._text_editing:
            self._cancel_text()
        elif self._drawing:
            self._dismiss()
        elif self._pinned:
            self.toggle_pin()
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
        elif cmd == "quit":
            QApplication.quit()
