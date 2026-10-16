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
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
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
        self._drag_timer = None
        self._win_x = 0
        self._win_y = 0
        self._slack_l = 0
        self._slack_t = 0
        self._last_cgx = 0
        self._last_cgy = 0

        self._build_ui()

        QShortcut(QKeySequence(Qt.Key_Escape), self, self._on_escape)
        QShortcut(QKeySequence("Ctrl+Z"), self, self.undo)
        QShortcut(QKeySequence(Qt.Key_Left), self, lambda: self.hist_step(-1))
        QShortcut(QKeySequence(Qt.Key_Right), self, lambda: self.hist_step(1))
        for key, t in (("m", TOOL_MOVE), ("p", TOOL_PEN), ("a", TOOL_ARROW),
                       ("r", TOOL_RECT), ("t", TOOL_TEXT)):
            QShortcut(QKeySequence(key), self,
                      lambda t=t: None if self._text_editing else self.set_tool(t))

        self.ipc_command.connect(self._on_ipc)

    # ---------- UI construction ----------

    def _mkbtn(self, name, tip, cb, checkable=False):
        b = QToolButton(self.toolbar)
        b.setIcon(icons.svg_icon(name))
        b.setIconSize(QSize(18, 18))
        b.setFixedSize(BTN, BTN)
        b.setToolTip(tip)
        b.setCheckable(checkable)
        b.clicked.connect(cb)
        return b

    def _build_ui(self):
        self.toolbar = QFrame(self)
        self.toolbar.setStyleSheet(BTN_CSS)
        lay = QHBoxLayout(self.toolbar)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)
        self.btn_move = self._mkbtn("move", "Move the frame (M)", lambda: self.set_tool(TOOL_MOVE), True)
        self.btn_pen = self._mkbtn("pen", "Pen (P)", lambda: self.set_tool(TOOL_PEN), True)
        self.btn_arrow = self._mkbtn("arrow", "Arrow (A)", lambda: self.set_tool(TOOL_ARROW), True)
        self.btn_rect = self._mkbtn("rect", "Rectangle (R)", lambda: self.set_tool(TOOL_RECT), True)
        self.btn_text = self._mkbtn("text", "Text (T)", lambda: self.set_tool(TOOL_TEXT), True)
        for i, b in enumerate((self.btn_move, self.btn_pen, self.btn_arrow, self.btn_rect, self.btn_text)):
            self.tool_group.addButton(b, i)
            lay.addWidget(b)
        self.btn_move.setChecked(True)

        self.btn_color = self._mkbtn("pen", "Stroke color", self._open_color_picker)
        self.btn_color.setIcon(icons.color_dot_icon(self._color.name()))
        lay.addWidget(self.btn_color)

        self.btn_width = self._mkbtn("width", "Stroke width", self._open_width_popup)
        lay.addWidget(self.btn_width)

        self.btn_undo = self._mkbtn("undo", "Undo (Ctrl+Z)", self.undo)
        lay.addWidget(self.btn_undo)

        self.btn_reset = self._mkbtn("reset", "Reset: clear drawings, default size", self.reset)
        lay.addWidget(self.btn_reset)

        self.btn_full = self._mkbtn("full", "Expand to fullscreen / restore", self.toggle_fullscreen)
        lay.addWidget(self.btn_full)

        self.opacity_slider = QSlider(Qt.Horizontal, self.toolbar)
        self.opacity_slider.setRange(20, 100)
        self.opacity_slider.setValue(int(self._opacity * 100))
        self.opacity_slider.setFixedWidth(64)
        self.opacity_slider.setToolTip("Image opacity")
        self.opacity_slider.valueChanged.connect(self._on_opacity)
        lay.addWidget(self.opacity_slider)

        self.btn_pin = self._mkbtn("pin", "Pin on top (hide chrome)", self.toggle_pin, True)
        lay.addWidget(self.btn_pin)

        self.btn_copy = self._mkbtn("copy", "Copy image to clipboard", self.copy_image)
        lay.addWidget(self.btn_copy)

        self.btn_prev = self._mkbtn("prev", "Previous capture (←)", lambda: self.hist_step(-1))
        self.hist_label = QLabel("0/0", self.toolbar)
        self.hist_label.setStyleSheet(f"QLabel {{ color: {CHROME_FG}; font-size: 11px; background: transparent; }}")
        self.btn_next = self._mkbtn("next", "Next capture (→)", lambda: self.hist_step(1))
        lay.addWidget(self.btn_prev)
        lay.addWidget(self.hist_label)
        lay.addWidget(self.btn_next)

        lay.addStretch(1)
        self.btn_close = self._mkbtn("close", "Hide and clear session history (Esc)", self._dismiss)
        lay.addWidget(self.btn_close)

        self.capture_btn = QToolButton(self.toolbar)
        self.capture_btn.setIcon(icons.svg_icon("camera", "#ffffff"))
        self.capture_btn.setIconSize(QSize(20, 20))
        self.capture_btn.setFixedSize(34, 34)
        self.capture_btn.setStyleSheet(CAPTURE_CSS)
        self.capture_btn.setToolTip("Capture: save to folder + clipboard (hold to drag the shot)")
        self.capture_btn.pressed.connect(self._on_capture_pressed)
        if "left" in str(self.settings["capture_button_corner"]):
            lay.insertWidget(0, self.capture_btn)
        else:
            lay.addWidget(self.capture_btn)

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

    def _screen_rect(self) -> QRect:
        scr = self.screen() or QGuiApplication.primaryScreen()
        return scr.geometry()

    def relayout(self):
        cw, ch = self._content.width(), self._content.height()
        tb_w = max(self.toolbar.sizeHint().width(), 40)
        sg = self._screen_rect()
        content_top = self.y() + self._content.y()
        pad = 0 if self._fullscreen else PAD

        if self._fullscreen:
            mode = "inside"
        else:
            below = content_top + ch + GAP + TB_H + pad <= sg.bottom() + 1
            above = content_top - GAP - TB_H - pad >= sg.top() - 1
            mode = "below" if below else ("above" if above else "inside")
        self._strip_mode = mode

        inner_w = max(cw, tb_w)
        win_w = inner_w + 2 * pad
        tdx = pad + (inner_w - tb_w) // 2
        cdx = pad + (inner_w - cw) // 2
        if mode == "below":
            coy, toy, win_h = pad, pad + ch + GAP, ch + 2 * pad + GAP + TB_H
        elif mode == "above":
            coy, toy, win_h = pad + GAP + TB_H, pad, ch + 2 * pad + GAP + TB_H
        else:  # floating overlay on top of the content
            coy, toy, win_h = 0, 6, ch
            tdx = max(4, cdx + (cw - tb_w) // 2)

        cgl_x = self.x() + self._content.x()
        cgl_y = self.y() + self._content.y()
        self.setGeometry(cgl_x - cdx, cgl_y - coy, win_w, win_h)
        self._content.moveTopLeft(QPoint(cdx, coy))
        self.toolbar.setGeometry(tdx, toy, tb_w, TB_H)
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
        self.open_at(QCursor.pos().x(), QCursor.pos().y())

    def open_at(self, cx: int, cy: int):
        scr = QGuiApplication.screenAt(QPoint(cx, cy)) or QGuiApplication.primaryScreen()
        sg = scr.geometry()
        strip = TB_H + GAP + PAD
        w = min(self.settings["default_width"], sg.width() - strip - 20)
        h = min(self.settings["default_height"], sg.height() - strip - 20)
        self._content.setSize(QSize(w, h))
        self._fullscreen = False
        self._pinned = False
        self.btn_pin.setChecked(False)
        cx = max(sg.left() + w // 2, min(cx, sg.right() - w // 2))
        cy = max(sg.top() + h // 2, min(cy, sg.bottom() - h // 2 - strip))
        self._content.moveTop(strip)
        self.setGeometry(cx - w // 2 - PAD, cy - h // 2 - strip, w + 2 * PAD, h + strip + PAD)
        self.relayout()
        self.show()
        self.raise_()
        self.activateWindow()
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
            self.btn_undo.setEnabled(bool(self._shapes))
            self.update()

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
        if self._fullscreen:
            pre = self._pre_fs
            self._fullscreen = False
            self._content.setSize(pre["size"])
            self._content.moveTopLeft(QPoint(PAD, PAD + GAP + TB_H))
            self.setGeometry(pre["win_x"], pre["win_y"], pre["size"].width() + 2 * PAD,
                             pre["size"].height() + 2 * PAD + GAP + TB_H)
            self.relayout()
            return
        scr = self._screen_rect()
        self._pre_fs = {
            "size": QSize(self._content.width(), self._content.height()),
            "win_x": self.x(),
            "win_y": self.y(),
        }
        self._fullscreen = True
        self._content = QRect(0, 0, scr.width(), scr.height())
        self.setGeometry(scr)
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
        self.btn_full.setEnabled(not self._pinned)

        self.btn_copy.setVisible(has_img)
        self.opacity_slider.setVisible(has_img)
        self.btn_pin.setVisible(has_img)
        self.btn_prev.setEnabled(bool(self.history) and self._hist_idx > 0)
        self.btn_next.setEnabled(bool(self.history) and self._hist_idx < len(self.history) - 1)
        self.hist_label.setText(f"{self._hist_idx + 1}/{len(self.history)}")
        self.hist_label.setVisible(bool(self.history))
        self.btn_prev.setVisible(bool(self.history))
        self.btn_next.setVisible(bool(self.history))
        self.capture_btn.setVisible(not self._pinned)
        if has_img:
            self.opacity_slider.setValue(int(self._opacity * 100))

    # ---------- painting ----------

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        c = self._content

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
        corner = self._grip_at(pos)
        if corner:
            self._resize_corner = corner
            self._resize_ref = (gpos, self.content_global_rect())
            self._begin_slack_resize(corner)
            return
        if not self._content.contains(pos):
            return

        view_hist = self._image is not None and not self._is_latest
        if self._pinned or view_hist or self._tool == TOOL_MOVE:
            self._drag_offset = gpos - self.geometry().topLeft()
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

        if self._resize_corner and self._resize_ref:
            self._do_resize(gpos)
            return
        if self._drag_offset is not None:
            sg = QGuiApplication.primaryScreen().virtualGeometry()
            tl = gpos - self._drag_offset
            tl.setX(max(sg.left(), min(tl.x(), sg.right() - 60)))
            tl.setY(max(sg.top(), min(tl.y(), sg.bottom() - 60)))
            self.move(tl)
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
        if self._resize_corner:
            self._resize_corner = None
            self._resize_ref = None
            self._slack_l = 0
            self._slack_t = 0
            self.relayout()
        if self._drag_offset is not None:
            self._drag_offset = None
            self.relayout()
        if self._pending is not None:
            self._shapes.append(self._pending)
            self._pending = None
            self.btn_undo.setEnabled(True)
            self.update()

    def _update_mask(self):
        """Restrict window painting + input to frame, toolbar and label; clicks
        elsewhere (e.g. the reserved slack) pass through to the desktop."""
        region = QRegion(self._content.adjusted(-8, -8, 8, 8))
        region = region.united(QRegion(self.toolbar.geometry()))
        if self.size_label.isVisible():
            region = region.united(QRegion(self.size_label.geometry()))
        self.setMask(region)

    def _begin_slack_resize(self, corner):
        """Pre-reserve transparent slack toward the pulled sides so the window
        top-left stays FROZEN for the whole drag. Invariant achieved: the
        anchored corner keeps a constant position IN THE BUFFER, so even stale
        composited frames show it exactly in place."""
        _g0, cg0 = self._resize_ref
        vg = QGuiApplication.primaryScreen().virtualGeometry()
        above = self._strip_mode == "above"
        top_room = cg0.top() - vg.top() - (GAP + TB_H + PAD if above else PAD) - PAD
        slack_l = max(0, min(cg0.left() - vg.left() - 2 * PAD, 1000)) if "l" in corner else 0
        slack_t = max(0, min(top_room, 1000)) if "t" in corner else 0
        self._slack_l, self._slack_t = int(slack_l), int(slack_t)

        m_l = PAD + self._slack_l
        m_t = (PAD + GAP + TB_H if above else PAD) + self._slack_t
        cg = self.content_global_rect()
        self._win_x, self._win_y = cg.x() - m_l, cg.y() - m_t
        tb_w = max(self.toolbar.sizeHint().width(), 40)
        win_w = max(cg.width(), tb_w) + m_l + PAD
        win_h = cg.height() + m_t + (PAD if above else GAP + TB_H + PAD)
        self.setGeometry(self._win_x, self._win_y, win_w, win_h)
        self._content.moveTopLeft(QPoint(m_l, m_t))
        tdx = m_l + ((cg.width() - tb_w) // 2 if tb_w <= cg.width() else 0)
        toy = (m_t - GAP - TB_H) if above else m_t + cg.height() + GAP
        self.toolbar.setGeometry(tdx, toy, tb_w, TB_H)
        self._update_mask()

        # the WM may apply the resize but override the position; re-anchor the
        # frozen origin to what it actually applied (content global preserved)
        self._last_cgx, self._last_cgy = cg.x(), cg.y()

    def _do_resize(self, gpos):
        """Aspect-locked resize (with an image) anchored at the opposite corner.
        Screen-edge clamps cap the SIZE, never move the anchored edge."""
        _g0, cg0 = self._resize_ref
        corner = self._resize_corner
        grow_left = "l" in corner
        grow_top = "t" in corner
        ax = cg0.right() if grow_left else cg0.left()
        ay = cg0.bottom() if grow_top else cg0.top()
        vg = QGuiApplication.primaryScreen().virtualGeometry()
        lo_x, hi_x = vg.left() + PAD, vg.right() - PAD
        lo_y, hi_y = vg.top() + PAD, vg.bottom() - PAD
        if self._slack_l:
            lo_x = max(lo_x, self._win_x + PAD)
        if self._slack_t:
            lo_y = max(lo_y, self._win_y + PAD)

        iw = max(MIN_W, abs(gpos.x() - ax))
        ih = max(MIN_H, abs(gpos.y() - ay))

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

        self._content.setSize(QSize(cw, ch))
        self._last_cgx, self._last_cgy = cgx, cgy
        self._apply_drag_geometry()

    def _apply_drag_geometry(self):
        """Place content/toolbar from the frozen origin. The anchor corner keeps
        a constant BUFFER position (lx + cw == ax - win_x), so stale composited
        frames show it exactly in place."""
        cw, ch = self._content.width(), self._content.height()
        cgx, cgy = self._last_cgx, self._last_cgy
        tb_w = max(self.toolbar.sizeHint().width(), 40)
        above = self._strip_mode == "above"
        lx = cgx - self._win_x
        ly = cgy - self._win_y
        tdx = lx + ((cw - tb_w) // 2 if tb_w <= cw else 0)
        toy = (ly - GAP - TB_H) if above else ly + ch + GAP
        win_w = max(lx + cw, tdx + tb_w) + PAD
        win_h = max(ly + ch + (PAD if above else GAP + TB_H + PAD), toy + TB_H + PAD)

        self._content = QRect(QPoint(lx, ly), QSize(cw, ch))
        self.toolbar.setGeometry(tdx, toy, tb_w, TB_H)
        self.setGeometry(self._win_x, self._win_y, win_w, win_h)
        self._place_size_label()
        self._update_mask()
        self.update()

    def moveEvent(self, e):
        super().moveEvent(e)
        # During a drag: if the WM placed us somewhere other than the frozen
        # origin (configure override), adopt the real position and re-apply so
        # the anchor invariant holds against actuality.
        if self._resize_corner and (e.pos().x() != self._win_x or e.pos().y() != self._win_y):
            self._win_x, self._win_y = e.pos().x(), e.pos().y()
            self._apply_drag_geometry()

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

        self.history.append({"image": crop, "path": path,
                             "logical": QSize(self._content.width(), self._content.height())})
        limit = int(self.settings["history_limit"])
        if len(self.history) > limit:
            self.history = self.history[-limit:]
        self._hist_idx = len(self.history) - 1

        self._image = crop
        self._image_px = (crop.width(), crop.height())
        self._is_latest = True
        self._shapes = []
        self._tool = TOOL_MOVE
        self.btn_move.setChecked(True)

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
        """Drag the shot around while the button is still held (deterministic:
        window follows the cursor, no WM move mode involved)."""
        if not (QGuiApplication.mouseButtons() & Qt.LeftButton):
            return
        last = QCursor.pos()
        vg = QGuiApplication.primaryScreen().virtualGeometry()

        def tick():
            if not self.isVisible() or not (QGuiApplication.mouseButtons() & Qt.LeftButton):
                drag_timer.stop()
                return
            nonlocal last
            pos = QCursor.pos()
            d = pos - last
            if d.isNull():
                return
            new_tl = self.pos() + d
            new_tl.setX(max(vg.left(), min(new_tl.x(), vg.right() - 60)))
            new_tl.setY(max(vg.top(), min(new_tl.y(), vg.bottom() - 60)))
            self.move(new_tl)
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
        self._tool = TOOL_MOVE
        self.btn_move.setChecked(True)
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
        """Esc / ✕: hide and end the capture session (clears in-session history;
        saved files on disk are untouched)."""
        self.history = []
        self._hist_idx = -1
        self._update_ui_state()
        self.hide()

    def _on_escape(self):
        if self._text_editing:
            self._cancel_text()
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
