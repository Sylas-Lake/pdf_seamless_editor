"""文本框编辑覆盖层。"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPen
from PySide6.QtWidgets import QGraphicsItem

from ui.handles import ACCENT


class BoxEditorItem(QGraphicsItem):
    """文本框编辑覆盖层：实时渲染缓冲内容、光标、选区、输入法预编辑。"""

    def __init__(self, canvas):
        super().__init__()
        self.canvas = canvas
        self.setZValue(40)
        self.setVisible(False)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._blink_on = True
        self._font_cache = {}

    def boundingRect(self):
        sess = self.canvas.controller.session
        if sess is None:
            return QRectF()
        bb = sess.buffer.bbox()
        m = 12.0
        return QRectF(bb[0] - m, bb[1] - m, (bb[2] - bb[0]) + 2 * m,
                      (bb[3] - bb[1]) + 2 * m)

    def _qfont(self, rf, st):
        key = (getattr(rf, "key", ""), st.key)
        if key not in self._font_cache:
            from ui.overlay_fonts import qfont_for_style
            self._font_cache[key] = qfont_for_style(rf, st)
        return self._font_cache[key]

    def paint(self, painter, option, widget):
        c = self.canvas.controller
        sess = c.session
        if sess is None:
            return
        buf = sess.buffer
        oracle = sess.oracle
        adv = oracle.adv_fn()
        lh = buf.line_height

        bb = buf.bbox()
        painter.setPen(QPen(QColor(ACCENT.red(), ACCENT.green(), ACCENT.blue(), 160),
                            0, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(bb[0], bb[1], bb[2] - bb[0], bb[3] - bb[1]))

        if sess.selection:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(30, 120, 215, 70)))
            for (hi, s, e) in buf.selection_range(*sess.selection):
                for v in buf.visual:
                    if v.hard_idx != hi or v.end <= v.start:
                        continue
                    s2 = max(s, v.start)
                    e2 = min(e, v.end)
                    if e2 <= s2:
                        continue
                    hl = buf.hard_lines[hi]
                    x0 = v.x + sum(adv(*hl[i]) for i in range(v.start, s2))
                    x1 = v.x + sum(adv(*hl[i]) for i in range(v.start, e2))
                    painter.drawRect(QRectF(x0, v.baseline - lh * 0.78,
                                            max(x1 - x0, 1.0), lh))

        for v in buf.visual:
            hl = buf.hard_lines[v.hard_idx]
            cx = v.x
            for i in range(v.start, v.end):
                ch, st = hl[i]
                rf = oracle.char_font(st, ch)
                painter.setFont(self._qfont(rf, st))
                painter.setPen(QColor(int(st.color[0] * 255),
                                      int(st.color[1] * 255),
                                      int(st.color[2] * 255)))
                painter.drawText(QPointF(cx, v.baseline), ch)
                cx += oracle.advance(st, ch)

        if self._blink_on and sess.preedit == "":
            x, bl = buf.cursor_pos(sess.cursor, adv)
            cw = max(1.2, 2.0 / max(self.canvas.zoom, 0.05))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(ACCENT))
            painter.drawRect(QRectF(x - cw / 2, bl - lh * 0.78, cw, lh))

        if sess.preedit:
            st = buf.style_at(sess.cursor)
            rf = oracle.char_font(st, "预")
            f = self._qfont(rf, st)
            painter.setFont(f)
            painter.setPen(QPen(QColor(ACCENT)))
            x, bl = buf.cursor_pos(sess.cursor, adv)
            painter.drawText(QPointF(x, bl), sess.preedit)
            fm = painter.fontMetrics()
            w = fm.horizontalAdvance(sess.preedit)
            painter.setBrush(QBrush(QColor(ACCENT)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRect(QRectF(x, bl + 1.5, w, 1.2))

    def blink(self):
        self._blink_on = not self._blink_on
        if self.isVisible():
            self.update()
