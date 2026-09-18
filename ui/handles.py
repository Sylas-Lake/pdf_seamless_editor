"""选中对象控制点层。"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPen
from PySide6.QtWidgets import QGraphicsItem

ACCENT = QColor(21, 101, 192)
CORNERS = ("nw", "ne", "se", "sw")

HANDLE_CURSORS = {
    "n": Qt.CursorShape.SizeVerCursor,
    "s": Qt.CursorShape.SizeVerCursor,
    "e": Qt.CursorShape.SizeHorCursor,
    "w": Qt.CursorShape.SizeHorCursor,
    "nw": Qt.CursorShape.SizeFDiagCursor,
    "se": Qt.CursorShape.SizeFDiagCursor,
    "ne": Qt.CursorShape.SizeBDiagCursor,
    "sw": Qt.CursorShape.SizeBDiagCursor,
    "rot": Qt.CursorShape.CrossCursor,
    "move": Qt.CursorShape.SizeAllCursor,
}


class HandleLayer(QGraphicsItem):
    """选中对象控制层。mode: "image"（8点+旋转）/ "box"（左右调宽）。"""

    def __init__(self, canvas):
        super().__init__()
        self.canvas = canvas
        self.rect = QRectF()
        self.deg = 0.0
        self.mode = "image"
        self.setVisible(False)
        self.setZValue(50)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def set_state(self, rect: QRectF, deg=0.0, mode=None):
        self.prepareGeometryChange()
        self.rect = QRectF(rect)
        self.deg = deg
        if mode:
            self.mode = mode
        self.setVisible(True)
        self.update()

    def hide_layer(self):
        self.setVisible(False)

    def _z(self):
        return max(self.canvas.zoom, 0.05)

    def boundingRect(self):
        m = 40.0 / self._z()
        return self.rect.adjusted(-m, -m, m, m)

    def _local_points(self):
        r = self.rect
        cx = (r.left() + r.right()) / 2
        cy = (r.top() + r.bottom()) / 2
        if self.mode == "box":
            return {"w": (r.left(), cy), "e": (r.right(), cy)}
        pts = {"nw": (r.left(), r.top()), "n": (cx, r.top()),
               "ne": (r.right(), r.top()), "e": (r.right(), cy),
               "se": (r.right(), r.bottom()), "s": (cx, r.bottom()),
               "sw": (r.left(), r.bottom()), "w": (r.left(), cy)}
        pts["rot"] = (cx, r.top() - 32.0 / self._z())
        return pts

    def _to_local(self, sp: QPointF) -> QPointF:
        if self.mode == "image" and abs(self.deg) > 0.5:
            c = self.rect.center()
            rad = math.radians(-self.deg)
            dx, dy = sp.x() - c.x(), sp.y() - c.y()
            ca, sa = math.cos(rad), math.sin(rad)
            return QPointF(c.x() + dx * ca - dy * sa,
                           c.y() + dx * sa + dy * ca)
        return QPointF(sp)

    def _to_world(self, x, y):
        if self.mode == "image" and abs(self.deg) > 0.5:
            c = self.rect.center()
            rad = math.radians(self.deg)
            dx, dy = x - c.x(), y - c.y()
            ca, sa = math.cos(rad), math.sin(rad)
            return (c.x() + dx * ca - dy * sa, c.y() + dx * sa + dy * ca)
        return (x, y)

    def corner_points(self):
        return {name: self._to_world(x, y)
                for name, (x, y) in self._local_points().items()}

    def hit_handle(self, sp: QPointF):
        if not self.isVisible():
            return None
        p = self._to_local(sp)
        z = self._z()
        corner_tol = 14.0 / z
        edge_tol = 8.0 / z
        rot_tol = 12.0 / z
        r = self.rect
        pts = self._local_points()

        if "rot" in pts:
            hx, hy = pts["rot"]
            if abs(p.x() - hx) <= rot_tol and abs(p.y() - hy) <= rot_tol:
                return "rot"

        for name in CORNERS:
            if name not in pts:
                continue
            hx, hy = pts[name]
            if abs(p.x() - hx) <= corner_tol and abs(p.y() - hy) <= corner_tol:
                return name

        if self.mode == "image":
            inside_x = r.left() - edge_tol <= p.x() <= r.right() + edge_tol
            inside_y = r.top() - edge_tol <= p.y() <= r.bottom() + edge_tol
            if inside_x and abs(p.y() - r.top()) <= edge_tol:
                return "n"
            if inside_x and abs(p.y() - r.bottom()) <= edge_tol:
                return "s"
            if inside_y and abs(p.x() - r.right()) <= edge_tol:
                return "e"
            if inside_y and abs(p.x() - r.left()) <= edge_tol:
                return "w"
            if r.contains(p):
                return "move"
            return None

        for name in ("w", "e"):
            hx, hy = pts[name]
            if abs(p.x() - hx) <= corner_tol and abs(p.y() - hy) <= corner_tol:
                return name
        if r.contains(p):
            return "move"
        return None

    def paint(self, painter, option, widget):
        z = self._z()
        s = 10.0 / z
        painter.setPen(QPen(ACCENT, 0, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if self.deg and self.mode == "image":
            painter.save()
            painter.translate(self.rect.center())
            painter.rotate(self.deg)
            painter.drawRect(QRectF(-self.rect.width() / 2, -self.rect.height() / 2,
                                    self.rect.width(), self.rect.height()))
            painter.restore()
        else:
            painter.drawRect(self.rect)
        painter.setPen(QPen(ACCENT, 0))
        for name, (hx, hy) in self.corner_points().items():
            if name == "rot":
                painter.setBrush(QBrush(ACCENT))
                painter.drawEllipse(QPointF(hx, hy), s * 0.7, s * 0.7)
                tx, ty = self._to_world(self.rect.center().x(), self.rect.top())
                painter.drawLine(QPointF(tx, ty), QPointF(hx, hy))
            else:
                painter.setBrush(QBrush(QColor(255, 255, 255, 235)))
                painter.drawRect(QRectF(hx - s / 2, hy - s / 2, s, s))
