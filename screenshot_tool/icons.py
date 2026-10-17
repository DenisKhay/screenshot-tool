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
    "fillrect": '<rect x="4" y="5" width="12" height="10" fill="{c}" stroke="none"/>',
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
    "redraw": ('<rect x="3" y="4" width="14" height="11" rx="1" stroke-dasharray="3 2.5"/>'
               '<path d="M12 16l5-5"/><path d="M13.5 11H16v2.5"/>'),
    "delone": ('<rect x="3" y="4" width="14" height="12" rx="1"/>'
               '<path d="M7 8.5l6 5M13 8.5l-6 5"/>'),
    "gear": ('<g transform="scale(0.8333)">'
             '<path fill="{c}" stroke="none" d="M19.14,12.94c0.04-0.3,0.06-0.61,0.06-0.94c0-0.32-0.02-0.64-0.07-0.94l2.03-1.58c0.18-0.14,0.23-0.41,0.12-0.61l-1.92-3.32c-0.12-0.22-0.37-0.29-0.59-0.22l-2.39,0.96c-0.5-0.38-1.03-0.7-1.62-0.94L14.4,2.81c-0.04-0.24-0.24-0.41-0.48-0.41h-3.84c-0.24,0-0.43,0.17-0.47,0.41L9.25,5.35C8.66,5.59,8.12,5.92,7.63,6.29L5.24,5.33c-0.22-0.08-0.47,0-0.59,0.22L2.74,8.87C2.62,9.08,2.66,9.34,2.86,9.48l2.03,1.58C4.84,11.36,4.8,11.69,4.8,12s0.02,0.64,0.07,0.94l-2.03,1.58c-0.18,0.14-0.23,0.41-0.12,0.61l1.92,3.32c0.12,0.22,0.37,0.29,0.59,0.22l2.39-0.96c0.5,0.38,1.03,0.7,1.62,0.94l0.36,2.54c0.05,0.24,0.24,0.41,0.48,0.41h3.84c0.24,0,0.44-0.17,0.47-0.41l0.36-2.54c0.59-0.24,1.13-0.56,1.62-0.94l2.39,0.96c0.22,0.08,0.47,0,0.59-0.22l1.92-3.32c0.12-0.22,0.07-0.47-0.12-0.61L19.14,12.94z M12,15.6c-1.98,0-3.6-1.62-3.6-3.6s1.62-3.6,3.6-3.6s3.6,1.62,3.6,3.6S13.98,15.6,12,15.6z"/>'
             '</g>'),    "width": ('<path d="M4 5h12" stroke-width="1.4"/>'
              '<path d="M4 10h12" stroke-width="2.8"/>'
              '<path d="M4 15h12" stroke-width="4.2"/>'),
    "textsize": ('<path d="M3 16L8 4l5 12"/><path d="M5 11.5h6"/>'
                 '<path d="M16.5 6v9M14.5 8l2-2 2 2M14.5 13l2 2 2-2"/>'),
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
