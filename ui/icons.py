"""工具栏线框小图标（自绘，不引入图标包）。"""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap


def make_icon(kind: str, size: int = 18, color: str = "#E8ECF1") -> QIcon:
    dpr = 2.0
    px = int(size * dpr)
    pm = QPixmap(px, px)
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(QColor(color), 1.55)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    s = float(size)
    m = 2.2
    r = QRectF(m, m, s - 2 * m, s - 2 * m)
    _draw(p, kind, r, QColor(color))
    p.end()
    return QIcon(pm)


def _draw(p: QPainter, kind: str, r: QRectF, col: QColor):
    x, y, w, h = r.x(), r.y(), r.width(), r.height()
    if kind == "open":
        p.drawLine(QPointF(x, y + h * 0.38), QPointF(x + w * 0.38, y + h * 0.38))
        p.drawLine(QPointF(x, y + h * 0.38), QPointF(x, y + h))
        p.drawLine(QPointF(x, y + h), QPointF(x + w, y + h))
        p.drawLine(QPointF(x + w, y + h), QPointF(x + w, y + h * 0.28))
        p.drawLine(QPointF(x + w, y + h * 0.28), QPointF(x + w * 0.55, y + h * 0.28))
        p.drawLine(QPointF(x + w * 0.55, y + h * 0.28), QPointF(x + w * 0.42, y))
        p.drawLine(QPointF(x + w * 0.42, y), QPointF(x + w * 0.08, y))
        p.drawLine(QPointF(x + w * 0.08, y), QPointF(x, y + h * 0.38))
    elif kind == "save":
        p.drawRoundedRect(r, 1.4, 1.4)
        p.drawRect(QRectF(x + w * 0.22, y, w * 0.56, h * 0.38))
        p.drawRect(QRectF(x + w * 0.28, y + h * 0.52, w * 0.44, h * 0.32))
    elif kind == "undo":
        path = QPainterPath()
        path.moveTo(x + w * 0.78, y + h * 0.28)
        path.cubicTo(x + w * 0.55, y, x + w * 0.18, y + h * 0.18,
                     x + w * 0.22, y + h * 0.62)
        p.drawPath(path)
        p.drawLine(QPointF(x + w * 0.22, y + h * 0.62),
                   QPointF(x + w * 0.08, y + h * 0.38))
        p.drawLine(QPointF(x + w * 0.22, y + h * 0.62),
                   QPointF(x + w * 0.46, y + h * 0.52))
    elif kind == "redo":
        path = QPainterPath()
        path.moveTo(x + w * 0.22, y + h * 0.28)
        path.cubicTo(x + w * 0.45, y, x + w * 0.82, y + h * 0.18,
                     x + w * 0.78, y + h * 0.62)
        p.drawPath(path)
        p.drawLine(QPointF(x + w * 0.78, y + h * 0.62),
                   QPointF(x + w * 0.92, y + h * 0.38))
        p.drawLine(QPointF(x + w * 0.78, y + h * 0.62),
                   QPointF(x + w * 0.54, y + h * 0.52))
    elif kind == "zoom_out":
        p.drawEllipse(QRectF(x, y, w * 0.72, h * 0.72))
        p.drawLine(QPointF(x + w * 0.18, y + h * 0.36),
                   QPointF(x + w * 0.54, y + h * 0.36))
        p.drawLine(QPointF(x + w * 0.58, y + h * 0.58),
                   QPointF(x + w, y + h))
    elif kind == "zoom_in":
        p.drawEllipse(QRectF(x, y, w * 0.72, h * 0.72))
        cx, cy = x + w * 0.36, y + h * 0.36
        p.drawLine(QPointF(cx - w * 0.16, cy), QPointF(cx + w * 0.16, cy))
        p.drawLine(QPointF(cx, cy - h * 0.16), QPointF(cx, cy + h * 0.16))
        p.drawLine(QPointF(x + w * 0.58, y + h * 0.58),
                   QPointF(x + w, y + h))
    elif kind == "fit":
        p.drawLine(QPointF(x, y + h * 0.32), QPointF(x, y))
        p.drawLine(QPointF(x, y), QPointF(x + w * 0.32, y))
        p.drawLine(QPointF(x + w * 0.68, y), QPointF(x + w, y))
        p.drawLine(QPointF(x + w, y), QPointF(x + w, y + h * 0.32))
        p.drawLine(QPointF(x + w, y + h * 0.68), QPointF(x + w, y + h))
        p.drawLine(QPointF(x + w, y + h), QPointF(x + w * 0.68, y + h))
        p.drawLine(QPointF(x + w * 0.32, y + h), QPointF(x, y + h))
        p.drawLine(QPointF(x, y + h), QPointF(x, y + h * 0.68))
    elif kind == "prev":
        p.drawLine(QPointF(x + w * 0.68, y), QPointF(x + w * 0.28, y + h * 0.5))
        p.drawLine(QPointF(x + w * 0.28, y + h * 0.5), QPointF(x + w * 0.68, y + h))
    elif kind == "next":
        p.drawLine(QPointF(x + w * 0.32, y), QPointF(x + w * 0.72, y + h * 0.5))
        p.drawLine(QPointF(x + w * 0.72, y + h * 0.5), QPointF(x + w * 0.32, y + h))
    elif kind == "verify":
        p.drawRoundedRect(r, 2.0, 2.0)
        p.drawLine(QPointF(x + w * 0.22, y + h * 0.52),
                   QPointF(x + w * 0.42, y + h * 0.72))
        p.drawLine(QPointF(x + w * 0.42, y + h * 0.72),
                   QPointF(x + w * 0.78, y + h * 0.30))
    elif kind == "sample":
        p.drawRect(QRectF(x + w * 0.12, y, w * 0.76, h))
        p.drawLine(QPointF(x + w * 0.28, y + h * 0.32),
                   QPointF(x + w * 0.72, y + h * 0.32))
        p.drawLine(QPointF(x + w * 0.28, y + h * 0.52),
                   QPointF(x + w * 0.72, y + h * 0.52))
        p.drawLine(QPointF(x + w * 0.28, y + h * 0.72),
                   QPointF(x + w * 0.58, y + h * 0.72))
    elif kind == "menu":
        y0 = y + h * 0.22
        gap = h * 0.22
        for i in range(3):
            yy = y0 + i * gap
            p.drawLine(QPointF(x + w * 0.16, yy), QPointF(x + w * 0.84, yy))
    elif kind == "save_as":
        p.drawRoundedRect(QRectF(x, y, w * 0.72, h * 0.72), 1.2, 1.2)
        p.drawRect(QRectF(x + w * 0.16, y, w * 0.40, h * 0.28))
        p.drawLine(QPointF(x + w * 0.55, y + h * 0.55),
                   QPointF(x + w * 0.92, y + h * 0.92))
        p.drawLine(QPointF(x + w * 0.72, y + h * 0.92),
                   QPointF(x + w * 0.92, y + h * 0.92))
        p.drawLine(QPointF(x + w * 0.92, y + h * 0.72),
                   QPointF(x + w * 0.92, y + h * 0.92))
    elif kind == "export":
        p.drawRect(QRectF(x + w * 0.08, y, w * 0.62, h * 0.78))
        p.drawLine(QPointF(x + w * 0.39, y + h * 0.18),
                   QPointF(x + w * 0.39, y + h * 0.62))
        p.drawLine(QPointF(x + w * 0.39, y + h * 0.62),
                   QPointF(x + w * 0.24, y + h * 0.48))
        p.drawLine(QPointF(x + w * 0.39, y + h * 0.62),
                   QPointF(x + w * 0.54, y + h * 0.48))
        p.drawLine(QPointF(x + w * 0.78, y + h * 0.42),
                   QPointF(x + w * 0.78, y + h))
        p.drawLine(QPointF(x + w * 0.78, y + h),
                   QPointF(x + w, y + h * 0.82))
        p.drawLine(QPointF(x + w * 0.78, y + h),
                   QPointF(x + w * 0.56, y + h * 0.82))
    elif kind == "cut":
        p.drawEllipse(QRectF(x, y + h * 0.52, w * 0.36, h * 0.42))
        p.drawEllipse(QRectF(x + w * 0.64, y + h * 0.52, w * 0.36, h * 0.42))
        p.drawLine(QPointF(x + w * 0.30, y + h * 0.58),
                   QPointF(x + w * 0.78, y + h * 0.08))
        p.drawLine(QPointF(x + w * 0.70, y + h * 0.58),
                   QPointF(x + w * 0.22, y + h * 0.08))
    elif kind == "copy":
        p.drawRect(QRectF(x + w * 0.22, y, w * 0.70, h * 0.70))
        p.drawRect(QRectF(x, y + h * 0.28, w * 0.70, h * 0.70))
    elif kind == "paste":
        p.drawRoundedRect(QRectF(x + w * 0.08, y + h * 0.22, w * 0.84, h * 0.72), 1.4, 1.4)
        p.drawRect(QRectF(x + w * 0.28, y, w * 0.44, h * 0.32))
        p.drawLine(QPointF(x + w * 0.28, y + h * 0.52),
                   QPointF(x + w * 0.72, y + h * 0.52))
        p.drawLine(QPointF(x + w * 0.28, y + h * 0.68),
                   QPointF(x + w * 0.62, y + h * 0.68))
    elif kind == "select_all":
        pen = p.pen()
        pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawRect(r)
        pen.setStyle(Qt.PenStyle.SolidLine)
        p.setPen(pen)
        p.drawLine(QPointF(x + w * 0.22, y + h * 0.38),
                   QPointF(x + w * 0.78, y + h * 0.38))
        p.drawLine(QPointF(x + w * 0.22, y + h * 0.58),
                   QPointF(x + w * 0.62, y + h * 0.58))
    elif kind == "panel_left":
        p.drawRect(r)
        p.drawLine(QPointF(x + w * 0.38, y), QPointF(x + w * 0.38, y + h))
        p.setBrush(col)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRect(QRectF(x + 1.0, y + 1.0, w * 0.38 - 1.5, h - 2.0))
    elif kind == "bold":
        f = p.font()
        f.setPixelSize(max(10, int(h * 0.88)))
        f.setBold(True)
        p.setFont(f)
        p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), "B")
    elif kind == "italic":
        f = p.font()
        f.setPixelSize(max(10, int(h * 0.88)))
        f.setItalic(True)
        p.setFont(f)
        p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), "I")
    elif kind == "help":
        p.drawEllipse(r)
        f = p.font()
        f.setPixelSize(max(8, int(h * 0.72)))
        f.setBold(True)
        p.setFont(f)
        p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), "?")
    elif kind == "about":
        p.drawEllipse(r)
        f = p.font()
        f.setPixelSize(max(8, int(h * 0.72)))
        f.setBold(True)
        p.setFont(f)
        p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), "i")
    else:
        p.setBrush(col)
        p.drawEllipse(r.adjusted(4, 4, -4, -4))
