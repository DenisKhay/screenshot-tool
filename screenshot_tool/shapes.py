import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QFont, QPainter, QPainterPath, QPen


class Shape:
    def __init__(self, color, width):
        self.color = color
        self.width = width

    def draw(self, painter: QPainter):
        raise NotImplementedError


class PenShape(Shape):
    def __init__(self, color, width, p0):
        super().__init__(color, width)
        self.points = [p0]

    def add(self, p):
        self.points.append(p)

    def draw(self, painter):
        if len(self.points) < 2:
            painter.setPen(QPen(self.color, self.width))
            painter.drawPoint(self.points[0])
            return
        pen = QPen(self.color, self.width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        path = QPainterPath(self.points[0])
        for p in self.points[1:]:
            path.lineTo(p)
        painter.drawPath(path)


class ArrowShape(Shape):
    def __init__(self, color, width, p1, p2):
        super().__init__(color, width)
        self.p1 = p1
        self.p2 = p2

    def draw(self, painter):
        pen = QPen(self.color, self.width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(self.p1, self.p2)
        angle = math.atan2(self.p2.y() - self.p1.y(), self.p2.x() - self.p1.x())
        head = 10 + self.width * 3
        spread = 0.45
        for a in (angle + math.pi - spread, angle + math.pi + spread):
            painter.drawLine(
                self.p2,
                QPointF(self.p2.x() + head * math.cos(a), self.p2.y() + head * math.sin(a)),
            )


class RectShape(Shape):
    def __init__(self, color, width, p1, p2):
        super().__init__(color, width)
        self.p1 = p1
        self.p2 = p2

    def rect(self):
        return QRectF(self.p1, self.p2).normalized()

    def draw(self, painter):
        painter.setPen(QPen(self.color, self.width))
        painter.drawRect(self.rect())


class FilledRectShape(Shape):
    def __init__(self, color, width, p1, p2):
        super().__init__(color, width)
        self.p1 = p1
        self.p2 = p2

    def rect(self):
        return QRectF(self.p1, self.p2).normalized()

    def draw(self, painter):
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.color)
        painter.drawRect(self.rect())


class TextShape(Shape):
    def __init__(self, color, size, pos, text):
        super().__init__(color, size)
        self.size = size
        self.pos = pos
        self.text = text

    def font(self):
        f = QFont()
        f.setPixelSize(self.size)
        return f

    def draw(self, painter):
        painter.setPen(self.color)
        painter.setFont(self.font())
        painter.drawText(QPointF(self.pos.x(), self.pos.y()), self.text)


def render_shapes(shapes, painter: QPainter):
    painter.setRenderHint(QPainter.Antialiasing, True)
    for s in shapes:
        s.draw(painter)
