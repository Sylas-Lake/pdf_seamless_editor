"""页面画布 v2：类 PPT 文本框编辑交互层。

- 单击文本框 → 选中（可拖动/调宽）；双击 → 进入框内编辑
- 框内编辑：光标/选区/输入法/换行全部基于内存缓冲，实时覆盖层渲染
- 图片：8 控制点缩放（等比/自由）+ 旋转手柄 + 拖动
- Ctrl+滚轮缩放；Shift+滚轮横向平移；滚轮在页边翻页
"""
import math
import os
import time

from PySide6.QtCore import Qt, QTimer, Signal, QRectF, QPointF, QEvent
from PySide6.QtGui import (QBrush, QColor, QCursor, QFont, QGuiApplication,
                           QPainter)
from PySide6.QtWidgets import (QApplication, QFrame, QGraphicsPixmapItem,
                               QGraphicsScene, QGraphicsView, QLabel, QMenu)

from core.geom import qrect_args, resize_rect
from ui.box_editor import BoxEditorItem
from ui.handles import CORNERS, HANDLE_CURSORS, HandleLayer


def to_qrect(xyxy) -> QRectF:
    """PDF (x0,y0,x1,y1) → QRectF(x, y, w, h)。"""
    x, y, w, h = qrect_args(xyxy)
    return QRectF(x, y, w, h)


def qrect_xyxy(r: QRectF) -> tuple:
    return (r.left(), r.top(), r.right(), r.bottom())


def local_pdf_paths(mime) -> list[str]:
    """从拖放 MIME 取出本地 PDF 路径。"""
    if mime is None or not mime.hasUrls():
        return []
    out = []
    seen = set()
    for url in mime.urls():
        path = url.toLocalFile()
        if not path:
            continue
        path = os.path.normpath(path)
        if path.lower().endswith(".pdf") and os.path.isfile(path) and path not in seen:
            seen.add(path)
            out.append(path)
    return out


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
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        self.setInputMethodHints(Qt.InputMethodHint.ImhNone)
        self.viewport().installEventFilter(self)
        # 事件全部由视图处理，避免 pixmap 吞掉 Windows 的 DblClick
        self.setInteractive(False)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setBackgroundBrush(QBrush(QColor("#4B4F55")))
        self._scene.setBackgroundBrush(QBrush(QColor("#4B4F55")))
        self.setAcceptDrops(True)

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

        self._empty_label = QLabel("点击此处打开 PDF\n或将文件拖到这里", self.viewport())
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._empty_label.setStyleSheet(
            "QLabel { color: #D0D4DA; font-size: 16px; background: transparent; }"
        )
        self._empty_label.hide()

        # 交互状态
        self._selecting = False
        self._click_count = 0
        self._last_click_ms = 0.0
        self._drag = None  # {kind:"image"|"block", mode, start_rect, cur_rect, deg, corner, start_pos, block}
        self._pan = None
        self._last_page_turn = 0.0

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
            self.set_empty_prompt(True)
            return
        self.pixmap_item = QGraphicsPixmapItem(pixmap)
        self.pixmap_item.setZValue(-10)
        self.pixmap_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._scene.addItem(self.pixmap_item)
        self._scene.setSceneRect(QRectF(0, 0, page_w, page_h))
        self.set_empty_prompt(False)

    def set_empty_prompt(self, on: bool):
        self._empty_label.setVisible(bool(on))
        self._layout_empty_prompt()

    def empty_prompt_visible(self) -> bool:
        return self._empty_label.isVisible()

    def _layout_empty_prompt(self):
        if not self._empty_label.isVisible():
            return
        self._empty_label.setGeometry(self.viewport().rect())

    def showEvent(self, e):
        super().showEvent(e)
        self._layout_empty_prompt()

    def apply_zoom(self):
        self.resetTransform()
        self.scale(self.zoom, self.zoom)
        self.handle_layer.update()
        self.box_editor.update()

    def center_page(self):
        def _go():
            r = self.sceneRect()
            if r.width() <= 0 or r.height() <= 0:
                return
            self.centerOn(r.center())
        QTimer.singleShot(0, _go)

    def scroll_to_edge(self, edge):
        def _go():
            bar = self.verticalScrollBar()
            if edge == "bottom":
                bar.setValue(bar.maximum())
            else:
                bar.setValue(bar.minimum())
        QTimer.singleShot(0, _go)

    def set_hint(self, text: str):
        self.hint_item.setPlainText(text)
        self.hint_item.setVisible(bool(text))

    def hint_text(self) -> str:
        if not self.hint_item.isVisible():
            return ""
        return self.hint_item.toPlainText()

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

    def try_turn_page(self, delta: int, from_edge: str) -> bool:
        """公开页边翻页。delta=+1 下一页，-1 上一页。"""
        self._last_page_turn = 0.0
        return bool(self._try_page_turn(delta, from_edge))

    def begin_handle_drag(self, handle: str, scene_pos: QPointF) -> None:
        self._start_drag(handle, scene_pos)

    def update_handle_drag(self, scene_pos: QPointF) -> None:
        self._update_drag(scene_pos)

    def drag_rect(self):
        d = self._drag
        return None if not d else d.get("cur_rect")

    def cancel_handle_drag(self) -> None:
        self._drag = None
        self._refresh_handles()

    # ------------------------------------------------ 输入法
    def _grab_focus(self):
        # 焦点必须留在视图上：绑到 viewport 时 Windows IME 收不到 inputMethodEvent
        self.setFocus(Qt.FocusReason.MouseFocusReason)

    def prepare_ime(self):
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled, True)
        self._grab_focus()
        im = QGuiApplication.inputMethod()
        if im is not None:
            im.update(Qt.InputMethodQuery.ImQueryAll)

    def eventFilter(self, obj, ev):
        if obj is self.viewport():
            et = ev.type()
            if et == QEvent.Type.InputMethod:
                self.inputMethodEvent(ev)
                return True
            if et == QEvent.Type.InputMethodQuery:
                queries = ev.queries()
                for q in (
                    Qt.InputMethodQuery.ImEnabled,
                    Qt.InputMethodQuery.ImCursorRectangle,
                    Qt.InputMethodQuery.ImFont,
                    Qt.InputMethodQuery.ImCursorPosition,
                    Qt.InputMethodQuery.ImAnchorPosition,
                    Qt.InputMethodQuery.ImSurroundingText,
                    Qt.InputMethodQuery.ImCurrentSelection,
                    Qt.InputMethodQuery.ImHints,
                ):
                    if queries & q:
                        ev.setValue(q, self.inputMethodQuery(q))
                return True
            if et == QEvent.Type.Resize:
                self._layout_empty_prompt()
        return super().eventFilter(obj, ev)

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
            return True
        if q == Qt.InputMethodQuery.ImHints:
            return Qt.InputMethodHint.ImhNone
        if q == Qt.InputMethodQuery.ImFont:
            return QFont("Microsoft YaHei", 12)
        c = self.controller
        if q == Qt.InputMethodQuery.ImCursorRectangle:
            if self.session_active():
                x, bl = c.session_cursor_pos()
                r = QRectF(x, bl - c.session.buffer.line_height, 2,
                           c.session.buffer.line_height)
                return self.mapFromScene(r).boundingRect()
            return QRectF()
        if q in (Qt.InputMethodQuery.ImCursorPosition,
                 Qt.InputMethodQuery.ImAnchorPosition):
            if self.session_active():
                return int(c.session.cursor[1])
            return 0
        if q == Qt.InputMethodQuery.ImSurroundingText:
            if self.session_active():
                h, _o = c.session.cursor
                line = c.session.buffer.hard_lines[h]
                return "".join(ch for ch, _st in line)
            return ""
        if q == Qt.InputMethodQuery.ImCurrentSelection:
            if self.session_active() and c.session.selection:
                return c.session.buffer.selection_text(*c.session.selection)
            return ""
        return super().inputMethodQuery(q)

    def _pdf_pos(self, e):
        p = self.mapToScene(e.position().toPoint())
        return p.x(), p.y()

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
        self.prepare_ime()
        self.box_editor._blink_on = True
        self.box_editor.update()
        return True

    def mousePressEvent(self, e):
        self._grab_focus()
        if e.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(e)
            return
        c = self.controller
        if c.doc is None:
            c.open_file()
            e.accept()
            return
        now = time.monotonic() * 1000
        self._click_count = (self._click_count + 1
                             if now - self._last_click_ms < 450 else 1)
        self._last_click_ms = now

        sp = self.mapToScene(e.position().toPoint())
        x, y = sp.x(), sp.y()
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
        self._pan = {
            "origin": e.position(),
            "h": self.horizontalScrollBar().value(),
            "v": self.verticalScrollBar().value(),
        }
        self.viewport().setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
        e.accept()

    def contextMenuEvent(self, e):
        c = self.controller
        m = QMenu(self)
        if c.selected_image is not None:
            m.addAction("替换图片…", self.image_replace_requested.emit)
            m.addAction("删除图片", c.delete_selected_image)
            m.addSeparator()
        m.addAction(c.act_cut)
        m.addAction(c.act_copy)
        m.addAction(c.act_paste)
        m.addAction(c.act_selall)
        m.exec(e.globalPos())
        e.accept()

    def mouseMoveEvent(self, e):
        sp = self.mapToScene(e.position().toPoint())
        x, y = sp.x(), sp.y()
        self.hover_pos.emit(x, y)
        if self._pan is not None:
            delta = e.position() - self._pan["origin"]
            self.horizontalScrollBar().setValue(int(self._pan["h"] - delta.x()))
            self.verticalScrollBar().setValue(int(self._pan["v"] - delta.y()))
            e.accept()
            return
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
            self.viewport().setCursor(QCursor(HANDLE_CURSORS.get(
                hit, Qt.CursorShape.ArrowCursor)))
            return
        x, y = sp.x(), sp.y()
        c = self.controller
        if c.hit_block(x, y) is not None:
            self.viewport().setCursor(QCursor(Qt.CursorShape.IBeamCursor))
        elif c.image_at(x, y) is not None:
            self.viewport().setCursor(QCursor(Qt.CursorShape.OpenHandCursor))
        elif self.model is not None:
            self.viewport().setCursor(QCursor(Qt.CursorShape.OpenHandCursor))
        elif c.doc is None:
            self.viewport().setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        else:
            self.viewport().unsetCursor()

    def mouseReleaseEvent(self, e):
        if self._pan is not None:
            self._pan = None
            self.viewport().unsetCursor()
            e.accept()
            return
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
        mods = e.modifiers()
        dx, dy = self._wheel_delta(e)
        if mods & Qt.KeyboardModifier.ControlModifier:
            if dy:
                self.controller.set_zoom(self.zoom * (1.15 if dy > 0 else 1 / 1.15))
            e.accept()
            return
        if mods & Qt.KeyboardModifier.ShiftModifier:
            step = dx if abs(dx) >= abs(dy) else dy
            if step:
                bar = self.horizontalScrollBar()
                bar.setValue(bar.value() - step)
            e.accept()
            return
        if dy:
            vbar = self.verticalScrollBar()
            at_top = vbar.value() <= vbar.minimum()
            at_bottom = vbar.value() >= vbar.maximum()
            if dy > 0 and at_top and self._try_page_turn(-1, "bottom"):
                e.accept()
                return
            if dy < 0 and at_bottom and self._try_page_turn(1, "top"):
                e.accept()
                return
        super().wheelEvent(e)

    def _wheel_delta(self, e):
        pix = e.pixelDelta()
        if not pix.isNull():
            return pix.x(), pix.y()
        ang = e.angleDelta()
        return ang.x(), ang.y()

    def _try_page_turn(self, delta, edge):
        c = self.controller
        if c.doc is None:
            return False
        nxt = c.page_no + delta
        if nxt < 0 or nxt >= c.doc.page_count:
            return False
        now = time.monotonic()
        if now - self._last_page_turn < 0.28:
            return True
        self._last_page_turn = now
        c.set_page(nxt, edge=edge)
        return True

    def focusInEvent(self, e):
        super().focusInEvent(e)
        if self.session_active():
            self.box_editor._blink_on = True
            self.box_editor.update()

    def dragEnterEvent(self, e):
        if local_pdf_paths(e.mimeData()):
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        self.dragEnterEvent(e)

    def dropEvent(self, e):
        paths = local_pdf_paths(e.mimeData())
        if not paths:
            e.ignore()
            return
        self.controller.open_file(paths[0])
        e.acceptProposedAction()

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
            self.viewport().setCursor(QCursor(HANDLE_CURSORS.get(
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
