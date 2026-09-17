"""页面画布：类 PPT 编辑交互层（对应方案第四、八、九章客户端层）。

- 单击定位 / 拖拽选段 / 双击选词 / 三击选行
- 光标与键盘导航（用户感知字符）
- 输入法（composition start/update/end，预编辑文本内联显示）
- 图片选择、8 控制点缩放、旋转手柄、拖动
- Ctrl+滚轮缩放
"""
import math

from PySide6.QtCore import Qt, QTimer, Signal, QRectF, QPointF
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QImage,
                           QPainter, QPen, QPixmap)
from PySide6.QtWidgets import (QGraphicsItem, QGraphicsPixmapItem,
                               QGraphicsRectItem, QGraphicsScene,
                               QGraphicsSimpleTextItem, QGraphicsView, QMenu)

SEL_COLOR = QColor(30, 120, 215, 80)


class HandleLayer(QGraphicsItem):
    """图片编辑控制层（8 缩放控制点 + 旋转手柄 + 参考框，纯绘制）。"""

    def __init__(self, canvas):
        super().__init__()
        self.canvas = canvas
        self.rect = QRectF()
        self.deg = 0.0
        self.setVisible(False)
        self.setZValue(50)

    def set_state(self, rect: QRectF, deg: float):
        self.prepareGeometryChange()
        self.rect = QRectF(rect)
        self.deg = deg
        self.setVisible(True)
        self.update()

    def hide_layer(self):
        self.setVisible(False)

    def boundingRect(self):
        m = 34.0 / max(self.canvas.zoom, 0.05)
        return self.rect.adjusted(-m, -m, m, m)

    def corner_points(self):
        r = self.rect
        cx, cy = (r.left() + r.right()) / 2, (r.top() + r.bottom()) / 2
        pts = {"nw": (r.left(), r.top()), "n": (cx, r.top()),
               "ne": (r.right(), r.top()), "e": (r.right(), cy),
               "se": (r.right(), r.bottom()), "s": (cx, r.bottom()),
               "sw": (r.left(), r.bottom()), "w": (r.left(), cy)}
        pts["rot"] = (cx, r.top() - 26.0 / self.canvas.zoom)
        return pts

    def hit_handle(self, sp: QPointF):
        if not self.isVisible():
            return None
        tol = 7.0 / self.canvas.zoom
        for name, (hx, hy) in self.corner_points().items():
            if abs(sp.x() - hx) <= tol and abs(sp.y() - hy) <= tol:
                return name
        if self.rect.contains(sp):
            return "move"
        return None

    def paint(self, painter, option, widget):
        z = max(self.canvas.zoom, 0.05)
        s = 8.0 / z
        painter.setPen(QPen(QColor("#1565c0"), 0, Qt.PenStyle.NoPen))
        painter.setPen(QPen(QColor("#1565c0"), 0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#1565c0"), 0, Qt.PenStyle.DashLine))
        painter.drawRect(self.rect)
        painter.setPen(QPen(QColor("#1565c0"), 0))
        painter.setBrush(QBrush(QColor(255, 255, 255, 230)))
        for name, (hx, hy) in self.corner_points().items():
            if name == "rot":
                painter.setBrush(QBrush(QColor("#1565c0")))
                painter.drawEllipse(QPointF(hx, hy), s * 0.75, s * 0.75)
                painter.drawLine(QPointF(self.rect.center().x(), self.rect.top()),
                                 QPointF(hx, hy))
            else:
                painter.setBrush(QBrush(QColor(255, 255, 255, 230)))
                painter.drawRect(QRectF(hx - s / 2, hy - s / 2, s, s))


class PageCanvas(QGraphicsView):
    """页面视图：承载页面渲染图与编辑覆盖层。"""

    hover_pos = Signal(float, float)
    image_transform_committed = Signal(dict)   # {old_rect, old_deg, new_rect, new_deg}
    image_delete_requested = Signal()
    image_replace_requested = Signal()
    image_selected_cleared = Signal()

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
        self.setMouseTracking(False)
        self.setBackgroundRole(self.backgroundRole())

        # 覆盖层元素
        self.pixmap_item = None
        self.sel_items = []
        self.cursor_item = self._scene.addLine(0, 0, 0, 10,
                                               QPen(QColor("#111111"), 0))
        self.cursor_item.setZValue(40)
        self.cursor_item.hide()
        self.preedit_item = QGraphicsSimpleTextItem()
        self.preedit_item.setZValue(45)
        self.preedit_item.hide()
        self._scene.addItem(self.preedit_item)
        self.preedit_line = self._scene.addRect(
            0, 0, 0, 0, QPen(Qt.PenStyle.NoPen), QBrush(QColor("#1565c0")))
        self.preedit_line.setZValue(45)
        self.preedit_line.hide()
        self.hint_item = QGraphicsSimpleTextItem()
        self.hint_item.setBrush(QBrush(QColor("#8a8a8a")))
        self.hint_item.hide()

        self.handle_layer = HandleLayer(self)
        self._scene.addItem(self.handle_layer)

        # 交互状态
        self._selecting = False
        self._click_count = 0
        self._last_click_ms = 0
        self._img_drag = None  # {mode, start_rect, cur_rect, deg, corner, start_pos}

        # 光标闪烁
        self._blink_on = True
        self._blink_timer = QTimer(self)
        self._blink_timer.setInterval(530)
        self._blink_timer.timeout.connect(self._blink)
        self._blink_timer.start()

    # ------------------------------------------------------------ 页面内容
    def set_page_content(self, pixmap: QPixmap, model, page_w: float,
                         page_h: float):
        self.model = model
        if self.pixmap_item is not None:
            self._scene.removeItem(self.pixmap_item)
        if pixmap is None:
            self.pixmap_item = None
            self._scene.setSceneRect(QRectF(0, 0, page_w, page_h))
            return
        self.pixmap_item = QGraphicsPixmapItem(pixmap)
        self.pixmap_item.setZValue(-10)
        self._scene.addItem(self.pixmap_item)
        self._scene.setSceneRect(QRectF(0, 0, page_w, page_h))

    def apply_zoom(self):
        self.resetTransform()
        self.scale(self.zoom, self.zoom)
        self._refresh_handles()
        self._layout_preedit()

    def set_hint(self, text: str):
        if text:
            self.hint_item.setText(text)
            f = QFont()
            f.setPointSize(12)
            self.hint_item.setFont(f)
            self.hint_item.setPos(24, 16)
            self._scene.addItem(self.hint_item)
            self.hint_item.show()
        else:
            self.hint_item.hide()

    # ------------------------------------------------------------ 光标与选区
    def update_cursor_item(self):
        st = self.controller.cursor_state()
        if st is None:
            self.cursor_item.hide()
            return
        line, gi = st
        if line is None or not line.glyphs:
            self.cursor_item.hide()
            return
        from core.extractor import boundary_x
        bx = boundary_x(line, gi)
        self.cursor_item.setLine(bx, line.bbox[1], bx, line.bbox[3])
        self.cursor_item.setPen(QPen(QColor("#1565c0") if self._blink_on
                                      else QColor("#1565c0"), 0))
        self.cursor_item.show()
        self._layout_preedit()

    def _blink(self):
        if not self.cursor_item.isVisible():
            return
        self._blink_on = not self._blink_on
        pen = self.cursor_item.pen()
        pen.setColor(QColor(21, 101, 192, 255 if self._blink_on else 40))
        self.cursor_item.setPen(pen)

    def update_selection_items(self):
        for it in self.sel_items:
            self._scene.removeItem(it)
        self.sel_items = []
        for (line, s, e) in self.controller.selection_segments():
            if s >= e:
                continue
            boxes = [g.bbox for g in line.glyphs[s:e]]
            if not boxes:
                continue
            r = QRectF(min(b[0] for b in boxes), min(b[1] for b in boxes),
                       0, 0)
            r.setRight(max(b[2] for b in boxes))
            r.setBottom(max(b[3] for b in boxes))
            it = self._scene.addRect(r, QPen(Qt.PenStyle.NoPen), QBrush(SEL_COLOR))
            it.setZValue(30)
            self.sel_items.append(it)

    # ------------------------------------------------------------ 输入法
    def inputMethodEvent(self, e):
        commit = e.commitString()
        if commit:
            self.controller.insert_text(commit)
        self.set_preedit(e.preeditString())
        e.accept()

    def inputMethodQuery(self, q):
        if q == Qt.InputMethodQuery.ImEnabled:
            return True
        if q == Qt.InputMethodQuery.ImCursorRectangle:
            if self.cursor_item.isVisible():
                r = self.cursor_item.boundingRect().translated(self.cursor_item.pos())
                poly = self.mapFromScene(r)
                return poly.boundingRect()
            return QRectF()
        return super().inputMethodQuery(q)

    def set_preedit(self, text: str):
        if not text:
            self.preedit_item.hide()
            self.preedit_line.hide()
            return
        style = self.controller.current_style() or None
        size = style.size if style else 12
        f = QFont()
        fam = "Microsoft YaHei"
        if style and style.font_name:
            fam = style.font_name.split("+")[-1]
        f.setFamily(fam)
        f.setPixelSize(max(7, round(size * self.zoom * 1.33)))
        f.setUnderline(True)
        self.preedit_item.setFont(f)
        self.preedit_item.setText(text)
        self.preedit_item.setBrush(QBrush(QColor(21, 101, 192)))
        self._layout_preedit()
        self.preedit_item.show()

    def _layout_preedit(self):
        if not self.preedit_item.isVisible():
            self.preedit_line.hide()
            return
        st = self.controller.cursor_state()
        if st is None:
            self.preedit_item.hide()
            return
        line, gi = st
        from core.extractor import boundary_x
        bx = boundary_x(line, gi)
        self.preedit_item.setPos(bx, line.bbox[1])
        fm = QFontMetrics(self.preedit_item.font())
        w = fm.horizontalAdvance(self.preedit_item.text())
        self.preedit_line.setRect(bx, line.bbox[1] + fm.height() * 0.92,
                                  w, 1.5 / self.zoom)
        self.preedit_line.show()

    # ------------------------------------------------------------ 鼠标
    def _pdf_pos(self, e):
        p = self.mapToScene(e.position().toPoint())
        return p.x(), p.y()

    def mousePressEvent(self, e):
        self.setFocus()
        if e.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(e)
            return
        import time
        now = time.monotonic() * 1000
        self._click_count = (self._click_count + 1
                             if now - self._last_click_ms < 450 else 1)
        self._last_click_ms = now

        sp = self.mapToScene(e.position().toPoint())
        x, y = sp.x(), sp.y()

        # 图片交互
        hit = self.handle_layer.hit_handle(sp)
        if hit:
            self._start_img_drag(hit, sp)
            e.accept()
            return

        # 文本定位
        pos = self.controller.hit_test(x, y)
        if pos is not None:
            li, gi = pos
            if self._click_count == 2:
                self.controller.select_word(li, gi)
            elif self._click_count >= 3:
                self.controller.select_line(li)
            else:
                self.controller.set_cursor(li, gi, extend=e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
                self._selecting = True
            e.accept()
            return

        # 图片选择
        img = self.controller.image_at(x, y)
        if img is not None:
            self.controller.select_image(img)
            e.accept()
            return

        # 空白：清除
        self.controller.clear_selection()
        self.controller.deselect_image()
        self.image_selected_cleared.emit()
        e.accept()

    def mouseMoveEvent(self, e):
        sp = self.mapToScene(e.position().toPoint())
        x, y = sp.x(), sp.y()
        self.hover_pos.emit(x, y)
        if self._img_drag is not None:
            self._update_img_drag(sp)
            e.accept()
            return
        if self._selecting:
            pos = self.controller.hit_test(x, y)
            if pos is not None:
                self.controller.extend_selection(*pos)
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._img_drag is not None:
            self._finish_img_drag()
            e.accept()
            return
        self._selecting = False
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        # 计数交由 press 处理
        e.accept()

    def wheelEvent(self, e):
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            d = e.angleDelta().y()
            if d:
                self.controller.set_zoom(self.zoom * (1.15 if d > 0 else 1 / 1.15))
            e.accept()
        else:
            super().wheelEvent(e)

    def contextMenuEvent(self, e):
        if self.controller.selected_image() is not None:
            m = QMenu(self)
            m.addAction("替换图片…", self.image_replace_requested.emit)
            m.addAction("删除图片", self.image_delete_requested.emit)
            m.addSeparator()
            m.addAction("取消选择", lambda: (self.controller.deselect_image(),
                                             self._refresh_handles()))
            gp = e.globalPos()
            m.exec(int(gp.x()), int(gp.y()))
        else:
            super().contextMenuEvent(e)

    # ------------------------------------------------------------ 键盘
    def keyPressEvent(self, e):
        mods = e.modifiers()
        ctrl = mods & Qt.KeyboardModifier.ControlModifier
        shift = mods & Qt.KeyboardModifier.ShiftModifier
        key = e.key()

        if self.controller.selected_image() is not None and key in (
                Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.image_delete_requested.emit()
            e.accept()
            return

        c = self.controller
        if key == Qt.Key.Key_Left:
            c.move_cursor(-1, word=bool(ctrl), extend=bool(shift))
        elif key == Qt.Key.Key_Right:
            c.move_cursor(1, word=bool(ctrl), extend=bool(shift))
        elif key == Qt.Key.Key_Up:
            c.move_cursor_vertical(-1, extend=bool(shift))
        elif key == Qt.Key.Key_Down:
            c.move_cursor_vertical(1, extend=bool(shift))
        elif key == Qt.Key.Key_Home:
            c.move_cursor_edge(head=True, page=bool(ctrl), extend=bool(shift))
        elif key == Qt.Key.Key_End:
            c.move_cursor_edge(head=False, page=bool(ctrl), extend=bool(shift))
        elif key == Qt.Key.Key_Backspace:
            c.backspace()
        elif key == Qt.Key.Key_Delete:
            c.delete_forward()
        elif key == Qt.Key.Key_Escape:
            c.clear_selection()
            c.deselect_image()
            self._refresh_handles()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            c.status_hint("段落换行重排属第二阶段能力，当前为原位替换模式")
        else:
            text = e.text()
            if text and not ctrl and not (mods & Qt.KeyboardModifier.AltModifier):
                if text in ("\r", "\n"):
                    c.status_hint("段落换行重排属第二阶段能力，当前为原位替换模式")
                    e.accept()
                    return
                if text.isprintable():
                    c.insert_text(text)
                    e.accept()
                    return
            super().keyPressEvent(e)
            return
        e.accept()

    # ------------------------------------------------------------ 图片拖拽
    def _start_img_drag(self, hit, sp):
        img = self.controller.selected_image()
        if img is None:
            return
        r = QRectF(*img.rect)
        self._img_drag = {
            "mode": hit if hit in ("nw", "n", "ne", "e", "se", "s", "sw", "w", "rot") else "move",
            "start_rect": QRectF(r),
            "cur_rect": QRectF(r),
            "deg": img.deg,
            "corner": hit,
            "start_pos": QPointF(sp),
        }

    def _update_img_drag(self, sp):
        d = self._img_drag
        if d is None:
            return
        r0 = d["start_rect"]
        p = QPointF(sp)
        if d["mode"] == "move":
            delta = p - d["start_pos"]
            d["cur_rect"] = QRectF(r0).translated(delta)
        elif d["mode"] == "rot":
            c = r0.center()
            ang = math.degrees(math.atan2(p.y() - c.y(), p.x() - c.x())) + 90.0
            if d.get("snap"):
                ang = round(ang / 15.0) * 15.0
            d["deg"] = ang % 360
        else:
            # 缩放：对角为锚点，默认等比例
            anchors = {"nw": r0.bottomRight(), "n": QPointF(r0.center().x(), r0.bottom()),
                       "ne": r0.bottomLeft(), "e": QPointF(r0.left(), r0.center().y()),
                       "se": r0.topLeft(), "s": QPointF(r0.center().x(), r0.top()),
                       "sw": r0.topRight(), "w": QPointF(r0.right(), r0.center().y())}
            a = anchors[d["corner"]]
            v = QPointF(p - a)
            aspect = (r0.width() / r0.height()) if r0.height() != 0 else 1.0
            if d["corner"] in ("n", "s"):
                h = abs(v.y())
                w = h * aspect
                v = QPointF((w if v.x() >= 0 else -w), v.y())
            elif d["corner"] in ("e", "w"):
                w = abs(v.x())
                h = w / aspect
                v = QPointF(v.x(), (h if v.y() >= 0 else -h))
            else:
                if abs(v.x()) < 1e-6:
                    v.setX(1e-6)
                if abs(v.y()) < 1e-6:
                    v.setY(1e-6)
                # 等比例：取主轴
                if abs(v.x()) / aspect >= abs(v.y()):
                    s = abs(v.x()) / (abs(v.x()) or 1)
                    vy = (v.y() / abs(v.y()) if v.y() != 0 else 1) * abs(v.x()) / aspect
                    v = QPointF(v.x(), vy)
                else:
                    vx = (v.x() / abs(v.x()) if v.x() != 0 else 1) * abs(v.y()) * aspect
                    v = QPointF(vx, v.y())
            nr = QRectF(a, a + v).normalized()
            if nr.width() >= 4 and nr.height() >= 4:
                d["cur_rect"] = nr
            elif d["cur_rect"].width() < 4 or d["cur_rect"].height() < 4:
                d["cur_rect"] = QRectF(a.x(), a.y(), 4, 4)
        self.handle_layer.set_state(d["cur_rect"], d["deg"])

    def _finish_img_drag(self):
        d = self._img_drag
        self._img_drag = None
        if d is None:
            return
        img = self.controller.selected_image()
        old = img.rect if img else None
        old_deg = img.deg if img else 0.0
        if old is None:
            self._refresh_handles()
            return
        new = (d["cur_rect"].left(), d["cur_rect"].top(),
               d["cur_rect"].right(), d["cur_rect"].bottom())
        if (abs(new[0] - old[0]) < 0.5 and abs(new[1] - old[1]) < 0.5
                and abs(new[2] - old[2]) < 0.5 and abs(new[3] - old[3]) < 0.5
                and abs(d["deg"] - old_deg) % 360 < 0.5):
            self._refresh_handles()
            return
        mode = d["mode"]
        title = {"move": "移动图片", "rot": "旋转图片"}.get(mode, "缩放图片")
        self.image_transform_committed.emit({
            "old_rect": old, "old_deg": old_deg,
            "new_rect": new, "new_deg": d["deg"], "title": title})

    def _refresh_handles(self):
        img = self.controller.selected_image()
        if img is None:
            self.handle_layer.hide_layer()
            return
        self.handle_layer.set_state(QRectF(*img.rect), img.deg)
