"""页面画布 v2：类 PPT 文本框编辑交互层。

- 单击文本框 → 选中（可拖动/调宽）；双击 → 进入框内编辑
- 框内编辑：光标/选区/输入法/换行全部基于内存缓冲，实时覆盖层渲染
- 图片：8 控制点缩放（等比/自由）+ 旋转手柄 + 拖动
- Ctrl+滚轮缩放
"""
import math

from PySide6.QtCore import Qt, QTimer, Signal, QRectF, QPointF
from PySide6.QtGui import (QBrush, QColor, QCursor, QFont, QPainter, QPen)
from PySide6.QtWidgets import (QApplication, QFrame, QGraphicsItem,
                               QGraphicsPixmapItem, QGraphicsScene,
                               QGraphicsView, QMenu)

from core.geom import qrect_args, resize_rect

ACCENT = QColor(21, 101, 192)
CORNERS = ("nw", "ne", "se", "sw")

_HANDLE_CURSORS = {
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


def to_qrect(xyxy) -> QRectF:
    """PDF (x0,y0,x1,y1) → QRectF(x, y, w, h)。"""
    x, y, w, h = qrect_args(xyxy)
    return QRectF(x, y, w, h)


def qrect_xyxy(r: QRectF) -> tuple:
    return (r.left(), r.top(), r.right(), r.bottom())


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

    # ------------------------------------------------ 绘制
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

        # 框边界
        bb = buf.bbox()
        painter.setPen(QPen(QColor(ACCENT.red(), ACCENT.green(), ACCENT.blue(), 160),
                            0, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(bb[0], bb[1], bb[2] - bb[0], bb[3] - bb[1]))

        # 选区
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

        # 文本（逐字符按 PDF 度量定位）
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

        # 光标（屏幕约 2px，避免缩放过细看不见）
        if self._blink_on and sess.preedit == "":
            x, bl = buf.cursor_pos(sess.cursor, adv)
            cw = max(1.2, 2.0 / max(self.canvas.zoom, 0.05))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(ACCENT))
            painter.drawRect(QRectF(x - cw / 2, bl - lh * 0.78, cw, lh))

        # 输入法预编辑
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


class PageCanvas(QGraphicsView):
    """页面视图。"""

    hover_pos = Signal(float, float)
    image_replace_requested = Signal()

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.zoom = 1.0
        self.model = None
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        # 事件全部由视图处理，避免 pixmap 吞掉 Windows 的 DblClick
        self.setInteractive(False)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setBackgroundBrush(QBrush(QColor("#4B4F55")))
        self._scene.setBackgroundBrush(QBrush(QColor("#4B4F55")))

        self.pixmap_item = None
        self.box_editor = BoxEditorItem(self)
        self._scene.addItem(self.box_editor)
        self.handle_layer = HandleLayer(self)
        self._scene.addItem(self.handle_layer)
        self.hint_item = self._scene.addText("", QFont("Microsoft YaHei", 12))
        self.hint_item.setDefaultTextColor(QColor("#C5CAD1"))
        self.hint_item.setPos(24, 12)
        self.hint_item.setZValue(5)
        self.hint_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

        # 交互状态
        self._selecting = False
        self._click_count = 0
        self._last_click_ms = 0.0
        self._drag = None  # {kind:"image"|"block", mode, start_rect, cur_rect, deg, corner, start_pos, block}

        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(530)
        self._blink_timer.timeout.connect(self._on_blink)
        self._blink_timer.start()

    # ------------------------------------------------ 页面内容
    def set_page_content(self, pixmap, model, page_w, page_h):
        self.model = model
        if self.pixmap_item is not None:
            self._scene.removeItem(self.pixmap_item)
        if pixmap is None:
            self.pixmap_item = None
            self._scene.setSceneRect(QRectF(0, 0, page_w, page_h))
            return
        self.pixmap_item = QGraphicsPixmapItem(pixmap)
        self.pixmap_item.setZValue(-10)
        self.pixmap_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._scene.addItem(self.pixmap_item)
        self._scene.setSceneRect(QRectF(0, 0, page_w, page_h))

    def apply_zoom(self):
        self.resetTransform()
        self.scale(self.zoom, self.zoom)
        self.handle_layer.update()
        self.box_editor.update()

    def set_hint(self, text: str):
        self.hint_item.setPlainText(text)
        self.hint_item.setVisible(bool(text))

    def session_active(self):
        return self.controller.session is not None

    def _on_blink(self):
        if self.session_active():
            self.box_editor.blink()

    def refresh_overlays(self):
        sess = self.controller.session
        self.box_editor.setVisible(sess is not None)
        if sess is not None:
            self.box_editor.prepareGeometryChange()
            self.box_editor._font_cache.clear()
            self.box_editor._blink_on = True
            self.box_editor.update()
        self._refresh_handles()

    def _refresh_handles(self):
        c = self.controller
        if c.session is not None:
            self.handle_layer.hide_layer()
            return
        img = c.selected_image
        blk = c.selected_block
        if img is not None:
            self.handle_layer.set_state(to_qrect(img.rect), img.deg, "image")
        elif blk is not None:
            self.handle_layer.set_state(to_qrect(blk.bbox), 0.0, "box")
        else:
            self.handle_layer.hide_layer()

    # ------------------------------------------------ 输入法
    def inputMethodEvent(self, e):
        c = self.controller
        if self.session_active():
            commit = e.commitString()
            if commit:
                c.session_insert(commit)
            c.session_set_preedit(e.preeditString())
            e.accept()
            return
        e.ignore()

    def inputMethodQuery(self, q):
        if q == Qt.InputMethodQuery.ImEnabled:
            return self.session_active()
        if q == Qt.InputMethodQuery.ImCursorRectangle:
            c = self.controller
            if self.session_active():
                x, bl = c.session_cursor_pos()
                r = QRectF(x, bl - c.session.buffer.line_height, 2,
                           c.session.buffer.line_height)
                return self.mapFromScene(r).boundingRect()
            return QRectF()
        return super().inputMethodQuery(q)

    # ------------------------------------------------ 鼠标
    def _pdf_pos(self, e):
        p = self.mapToScene(e.position().toPoint())
        return p.x(), p.y()

    def _grab_focus(self):
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.viewport().setFocus(Qt.FocusReason.MouseFocusReason)

    def _try_begin_edit(self, x, y):
        """双击文本框进入编辑。成功返回 True。"""
        c = self.controller
        block = c.hit_block(x, y)
        if block is None:
            return False
        c.start_session(block)
        if c.session is None:
            return False
        c.session_click(x, y)
        self._grab_focus()
        self.box_editor._blink_on = True
        self.box_editor.update()
        return True

    def mousePressEvent(self, e):
        self._grab_focus()
        if e.button() != Qt.MouseButton.LeftButton:
            if e.button() == Qt.MouseButton.RightButton and self.controller.selected_image:
                self._image_menu(e)
            super().mousePressEvent(e)
            return
        import time
        now = time.monotonic() * 1000
        self._click_count = (self._click_count + 1
                             if now - self._last_click_ms < 450 else 1)
        self._last_click_ms = now

        sp = self.mapToScene(e.position().toPoint())
        x, y = sp.x(), sp.y()
        c = self.controller
        shift = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)

        # 会话内：定位缓冲光标 / 选词 / 选行
        if self.session_active():
            bb = c.session.buffer.bbox()
            pad = 6
            if (bb[0] - pad <= x <= bb[2] + pad and bb[1] - pad <= y <= bb[3] + pad):
                if self._click_count == 2:
                    c.session_select_word(x, y)
                elif self._click_count >= 3:
                    c.session_select_visual_line(x, y)
                else:
                    c.session_click(x, y, extend=shift)
                    self._selecting = True
                e.accept()
                return
            # 会话外点击：提交并继续处理
            c.commit_session()

        # Windows 上第二次按下是 DblClick；部分平台会再发 Press。
        # 必须先于手柄命中，否则选中框会把双击吞成拖动。
        if self._click_count >= 2 and self._try_begin_edit(x, y):
            e.accept()
            return

        # 手柄命中（图片/文本框变换）
        hit = self.handle_layer.hit_handle(sp)
        if hit and not self.session_active():
            self._start_drag(hit, sp)
            e.accept()
            return

        block = c.hit_block(x, y)
        if block is not None:
            c.select_block(block)
            e.accept()
            return

        img = c.image_at(x, y)
        if img is not None:
            c.select_image(img)
            e.accept()
            return

        c.deselect_all()
        e.accept()

    def _image_menu(self, e):
        m = QMenu(self)
        m.addAction("替换图片…", self.image_replace_requested.emit)
        m.addAction("删除图片", self.controller.delete_selected_image)
        m.addAction("取消选择", lambda: self.controller.deselect_all())
        gp = e.globalPos()
        m.exec(int(gp.x()), int(gp.y()))

    def mouseMoveEvent(self, e):
        sp = self.mapToScene(e.position().toPoint())
        x, y = sp.x(), sp.y()
        self.hover_pos.emit(x, y)
        if self._drag is not None:
            self._update_drag(sp)
            e.accept()
            return
        if self._selecting and self.session_active():
            self.controller.session_drag(x, y)
            e.accept()
            return
        self._update_hover_cursor(sp)
        super().mouseMoveEvent(e)

    def _update_hover_cursor(self, sp: QPointF):
        if self.session_active():
            self.viewport().setCursor(QCursor(Qt.CursorShape.IBeamCursor))
            return
        hit = self.handle_layer.hit_handle(sp)
        if hit:
            self.viewport().setCursor(QCursor(_HANDLE_CURSORS.get(
                hit, Qt.CursorShape.ArrowCursor)))
            return
        x, y = sp.x(), sp.y()
        c = self.controller
        if c.hit_block(x, y) is not None:
            self.viewport().setCursor(QCursor(Qt.CursorShape.IBeamCursor))
        elif c.image_at(x, y) is not None:
            self.viewport().setCursor(QCursor(Qt.CursorShape.OpenHandCursor))
        else:
            self.viewport().unsetCursor()

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            self._finish_drag()
            e.accept()
            return
        self._selecting = False
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        # Windows：双击第二次按下只发 MouseButtonDblClick，不会再进 mousePressEvent。
        if e.button() != Qt.MouseButton.LeftButton:
            super().mouseDoubleClickEvent(e)
            return
        self._grab_focus()
        x, y = self._pdf_pos(e)
        c = self.controller
        if self.session_active():
            bb = c.session.buffer.bbox()
            pad = 6
            if (bb[0] - pad <= x <= bb[2] + pad and bb[1] - pad <= y <= bb[3] + pad):
                c.session_select_word(x, y)
            e.accept()
            return
        if self._try_begin_edit(x, y):
            e.accept()
            return
        e.accept()

    def wheelEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            d = e.angleDelta().y()
            if d:
                self.controller.set_zoom(self.zoom * (1.15 if d > 0 else 1 / 1.15))
            e.accept()
        else:
            super().wheelEvent(e)

    def focusInEvent(self, e):
        super().focusInEvent(e)
        if self.session_active():
            self.box_editor._blink_on = True
            self.box_editor.update()

    # ------------------------------------------------ 拖拽（图片 / 文本框）
    def _start_drag(self, hit, sp):
        c = self.controller
        img = c.selected_image
        blk = c.selected_block
        if img is not None and self.handle_layer.mode == "image":
            r = to_qrect(img.rect)
            self._drag = {"kind": "image", "mode": hit, "start_rect": QRectF(r),
                          "cur_rect": QRectF(r), "deg": img.deg, "corner": hit,
                          "start_pos": QPointF(sp), "img": img}
            self.viewport().setCursor(QCursor(_HANDLE_CURSORS.get(
                hit, Qt.CursorShape.SizeAllCursor)))
        elif blk is not None and self.handle_layer.mode == "box":
            r = to_qrect(blk.bbox)
            self._drag = {"kind": "block", "mode": hit, "start_rect": QRectF(r),
                          "cur_rect": QRectF(r), "corner": hit,
                          "start_pos": QPointF(sp), "block": blk}

    def _update_drag(self, sp):
        d = self._drag
        if d is None:
            return
        p = QPointF(sp)
        r0 = d["start_rect"]
        if d["kind"] == "block":
            if d["mode"] == "move":
                d["cur_rect"] = QRectF(r0).translated(p - d["start_pos"])
            elif d["mode"] in ("e", "w"):
                if d["mode"] == "e":
                    right = max(p.x(), r0.left() + 20)
                    d["cur_rect"] = QRectF(r0.left(), r0.top(),
                                            right - r0.left(), r0.height())
                else:
                    left = min(p.x(), r0.right() - 20)
                    d["cur_rect"] = QRectF(left, r0.top(),
                                            r0.right() - left, r0.height())
            self.handle_layer.set_state(d["cur_rect"], 0.0, "box")
            return
        # 图片
        if d["mode"] == "move":
            d["cur_rect"] = QRectF(r0).translated(p - d["start_pos"])
        elif d["mode"] == "rot":
            ctr = r0.center()
            ang = math.degrees(math.atan2(p.y() - ctr.y(), p.x() - ctr.x())) + 90.0
            d["deg"] = ang % 360
        else:
            deg = d.get("deg", 0.0) or 0.0
            if abs(deg) > 0.5:
                cpt = r0.center()
                rad = math.radians(-deg)
                dx, dy = p.x() - cpt.x(), p.y() - cpt.y()
                ca, sa = math.cos(rad), math.sin(rad)
                p = QPointF(cpt.x() + dx * ca - dy * sa,
                            cpt.y() + dx * sa + dy * ca)
            mods = QApplication.keyboardModifiers()
            shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
            keep_aspect = (d["mode"] in CORNERS) != shift
            nr = resize_rect(qrect_xyxy(r0), d["mode"], p.x(), p.y(),
                             keep_aspect=keep_aspect, min_size=8.0)
            d["cur_rect"] = to_qrect(nr)
        self.handle_layer.set_state(d["cur_rect"], d.get("deg", 0.0), "image")

    def _finish_drag(self):
        d = self._drag
        self._drag = None
        self.viewport().unsetCursor()
        if d is None:
            return
        c = self.controller
        if d["kind"] == "image":
            old = d["start_rect"]
            new = d["cur_rect"]
            if (abs(new.x() - old.x()) < 0.5 and abs(new.y() - old.y()) < 0.5
                    and abs(new.width() - old.width()) < 0.5
                    and abs(new.height() - old.height()) < 0.5
                    and abs(d["deg"] - (d["img"].deg % 360)) % 360 < 0.5):
                self._refresh_handles()
                return
            title = {"move": "移动图片", "rot": "旋转图片"}.get(d["mode"], "缩放图片")
            c.commit_image_transform(
                qrect_xyxy(old),
                d["img"].deg,
                qrect_xyxy(new),
                d["deg"], title)
        else:
            old = d["start_rect"]
            new = d["cur_rect"]
            dx = new.x() - old.x()
            dy = new.y() - old.y()
            if abs(dx) < 0.5 and abs(dy) < 0.5 and abs(new.width() - old.width()) < 0.5:
                self._refresh_handles()
                return
            title = "移动文本框" if abs(new.width() - old.width()) < 0.5 else "调整文本框"
            c.commit_block_transform(d["block"], dx, dy,
                                     new.width() - old.width(), title)

    # ------------------------------------------------ 键盘
    def keyPressEvent(self, e):
        c = self.controller
        mods = e.modifiers()
        ctrl = mods & Qt.KeyboardModifier.ControlModifier
        shift = mods & Qt.KeyboardModifier.ShiftModifier
        key = e.key()

        # 选中图片：Delete 删除
        if c.selected_image is not None and key in (Qt.Key.Key_Delete,
                                                    Qt.Key.Key_Backspace):
            c.delete_selected_image()
            e.accept()
            return
        # 选中文本框：Delete 删除框内容
        if c.selected_block is not None and c.session is None and key in (
                Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            c.delete_selected_block()
            e.accept()
            return

        if self.session_active():
            self._session_key(e)
            return

        text = e.text()
        if text and not ctrl and not (mods & Qt.KeyboardModifier.AltModifier):
            if text not in ("\r", "\n") and text.isprintable():
                c.status_hint("双击文本框进入编辑（类 PPT 交互）")
                e.accept()
                return
        super().keyPressEvent(e)

    def _session_key(self, e):
        c = self.controller
        mods = e.modifiers()
        ctrl = mods & Qt.KeyboardModifier.ControlModifier
        shift = mods & Qt.KeyboardModifier.ShiftModifier
        key = e.key()
        if key == Qt.Key.Key_Left:
            c.session_move(-1, word=bool(ctrl), extend=bool(shift))
        elif key == Qt.Key.Key_Right:
            c.session_move(1, word=bool(ctrl), extend=bool(shift))
        elif key == Qt.Key.Key_Up:
            c.session_move_vertical(-1, extend=bool(shift))
        elif key == Qt.Key.Key_Down:
            c.session_move_vertical(1, extend=bool(shift))
        elif key == Qt.Key.Key_Home:
            c.session_move_edge(head=True, extend=bool(shift))
        elif key == Qt.Key.Key_End:
            c.session_move_edge(head=False, extend=bool(shift))
        elif key == Qt.Key.Key_Backspace:
            c.session_backspace()
        elif key == Qt.Key.Key_Delete:
            c.session_delete_forward()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if ctrl:
                c.commit_session()
            else:
                c.session_split_line()
        elif key == Qt.Key.Key_Escape:
            c.cancel_session()
        else:
            if ctrl:
                # 快捷键由窗口级 QAction 处理（复制/粘贴/撤销等）
                super().keyPressEvent(e)
                return
            text = e.text()
            if text and not (mods & Qt.KeyboardModifier.AltModifier):
                if text in ("\r", "\n"):
                    c.session_split_line()
                elif text.isprintable():
                    c.session_insert(text)
                e.accept()
                return
            super().keyPressEvent(e)
            return
        e.accept()
