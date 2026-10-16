"""Programmatically authored SVG icons rendered to QIcons."""
from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

LIGHT = "#eceff1"
DARK = "#102027"

_TPL = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="none" '
        'stroke="{c}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')

ICONS = {
    "move": ('<path d="M10 2v16M2 10h16"/>'
             '<path d="M8 4l2-2 2 2M8 16l2 2 2-2M4 8l-2 2 2 2M16 8l2 2-2 2"/>'),
    "pen": '<path d="M4 16l1-4L14 3l3 3-9 9-4 1z"/><path d="M12 5l3 3"/>',
    "arrow": '<path d="M4 16L16 4"/><path d="M9 4h7v7"/>',
    "rect": '<rect x="4" y="5" width="12" height="10" rx="1"/>',
    "text": '<path d="M5 5h10M10 5v10"/>',
    "undo": '<path d="M6 4L3 7l3 3"/><path d="M3 7h8a5 5 0 0 1 0 10H8"/>',
    "reset": '<path d="M16 10a6 6 0 1 1-1.8-4.3"/><path d="M16 3v4h-4"/>',
    "full": '<path d="M3 7V3h4M17 7V3h-4M3 13v4h4M17 13v4h-4"/>',
    "copy": '<rect x="7" y="7" width="9" height="9" rx="1"/><path d="M13 4H5a1 1 0 0 0-1 1v8"/>',
    "pin": ('<path d="M12 3l5 5-3.2 1.1-1.6 4.6-2-2L6 15.8 4.2 14l4.1-4.2-2-2L11 6.2z" '
            'fill="{c}" stroke="none"/>'),
    "opacity": ('<circle cx="10" cy="10" r="7"/>'
                '<path d="M10 3a7 7 0 0 1 0 14z" fill="{c}" stroke="none"/>'),
    "prev": '<path d="M12 4l-6 6 6 6"/>',
    "next": '<path d="M8 4l6 6-6 6"/>',
    "close": '<path d="M5 5l10 10M15 5L5 15"/>',
    "camera": ('<rect x="3" y="6" width="14" height="10" rx="2"/><path d="M7 6l1-2h4l1 2"/>'
               '<circle cx="10" cy="11" r="3"/>'),
    "trash": ('<path d="M4 6h12"/><path d="M8 6V4h4v2"/><path d="M6 6l1 11h6l1-11"/>'
              '<path d="M9 9v5M11 9v5"/>'),
    "bake": ('<rect x="3" y="3" width="14" height="14" rx="2"/>'
             '<path d="M6.5 10l2.5 2.5 4.5-5"/>'),
    "width": ('<path d="M4 5h12" stroke-width="1.4"/>'
              '<path d="M4 10h12" stroke-width="2.8"/>'
              '<path d="M4 15h12" stroke-width="4.2"/>'),
}


def svg_icon(name: str, color: str = LIGHT, size: int = 36) -> QIcon:
    body = ICONS[name].replace("{c}", color)
    renderer = QSvgRenderer(QByteArray(_TPL.format(c=color, body=body).encode()))
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    renderer.render(p, QRectF(0, 0, size * 2, size * 2))
    p.end()
    pm.setDevicePixelRatio(2.0)
    return QIcon(pm)


def color_dot_icon(color: str, size: int = 36) -> QIcon:
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(color)
    p.setBrush(color)
    d = size * 2 * 0.56
    p.drawEllipse((size * 2 - d) / 2, (size * 2 - d) / 2, d, d)
    p.end()
    pm.setDevicePixelRatio(2.0)
    return QIcon(pm)
