"""主窗口：文本框会话编辑控制器。

编辑模型（类 PPT）：
  - 单击文本框 → 选中（可拖动移动 / 左右手柄调宽）
  - 双击文本框 → 进入编辑（框内缓冲，光标/选区/输入法自然工作）
  - 提交 → 整体重建（一次 redact + 按行插入）
  - 撤销/重做 → 页面快照字节级恢复（真正回到原版 PDF）
"""
import os
import tempfile
from types import SimpleNamespace

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QAction, QImage, QPixmap
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QListView,
                               QListWidget, QListWidgetItem, QMainWindow,
                               QMessageBox, QToolButton, QVBoxLayout, QWidget)

from core.compat import fitz
from core import executor, verifier
from core.commands import (ImageReplaceCommand, PageStateCommand, UndoStack)
from core.extractor import extract_page, line_redact_rects
from core.fidelity import PageFidelity, worse
from core.fonts import FontOracle, FontResolver
from core.snapshot import (capture_page_state, restore_page_state)
from core.textbox import BoxBuffer
from ui.chrome import ChromeBar
from ui.diff_dialog import DiffDialog
from ui.icons import make_icon
from ui.page_canvas import PageCanvas
from ui.property_panel import PropertyPanel
from ui.session import EditSession
from ui.stage import StageHost
from ui.theme import APP_QSS

APP_TITLE = "PDF 无感编辑器 · 文本框编辑版"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1280, 860)

        self.doc = None
        self.doc_path = ""
        self.resolver = None
        self.ctx = None
        self.models = {}
        self.orig_renders = {}
        self.edit_regions = {}
        self.fidelities = {}
        self._can_modify = True

        self.session = None
        self.selected_block = None
        self.selected_image = None
        self._overflow_strategy = "shrink"
        self.zoom = 1.0
        self.page_no = 0

        self.undo_stack = UndoStack(on_change=self._update_undo_actions)

        self.setStyleSheet(APP_QSS)
        app = QApplication.instance()
        if app is not None:
            app.setStyle("Fusion")
        self._build_ui()

    # ================================================== UI 构建
    def _build_ui(self):
        self.canvas = PageCanvas(self)
        self.canvas.hover_pos.connect(self._on_hover)
        self.canvas.image_replace_requested.connect(self.replace_selected_image)

        def _act(text, shortcut, slot, icon, tip=None):
            act = QAction(make_icon(icon), text, self)
            if shortcut:
                act.setShortcut(shortcut)
            act.setToolTip(tip or (f"{text}（{shortcut}）" if shortcut else text))
            act.triggered.connect(slot)
            self.addAction(act)
            return act

        self.act_open = _act("打开", "Ctrl+O", lambda: self.open_file(), "open")
        self.act_save = _act("保存", "Ctrl+S", self.save, "save")
        self.act_save_as = _act("另存为", "Ctrl+Shift+S", self.save_as, "save_as",
                               "另存为（Ctrl+Shift+S）")
        self.act_export = _act("导出优化副本", None, self.save_optimized, "export",
                              "导出优化副本（字体子集化）")
        self.act_verify = _act("验证报告", None, self.reverify, "verify", "验证报告")
        self.act_undo = _act("撤销", "Ctrl+Z", self.undo, "undo")
        self.act_redo = _act("重做", "Ctrl+Y", self.redo, "redo")
        self.act_cut = _act("剪切", "Ctrl+X", self.cut, "cut")
        self.act_copy = _act("复制", "Ctrl+C", self.copy, "copy")
        self.act_paste = _act("粘贴", "Ctrl+V", self.paste, "paste")
        self.act_selall = _act("全选", "Ctrl+A", self.session_select_all, "select_all",
                              "全选（框内，Ctrl+A）")
        self.act_zoom_out = _act("缩小", "Ctrl+-", lambda: self.set_zoom(self.zoom / 1.15),
                                "zoom_out")
        self.act_zoom_in = _act("放大", "Ctrl+=", lambda: self.set_zoom(self.zoom * 1.15),
                               "zoom_in")
        self.act_fit = _act("适应宽度", None, self.fit_width, "fit", "适应宽度")
        self.act_prev = _act("上一页", None, lambda: self.set_page(self.page_no - 1), "prev")
        self.act_next = _act("下一页", None, lambda: self.set_page(self.page_no + 1), "next")
        self.act_fid_help = _act("保真等级说明", None, self.show_fidelity_help, "help",
                                "保真等级说明")
        self.act_about = _act("关于", None, self.show_about, "about", "关于")

        # 左侧目录（缩略图）
        self.pane_thumbs = QWidget()
        self.pane_thumbs.setObjectName("ThumbPane")
        self.pane_thumbs.setMinimumWidth(0)
        self.pane_thumbs.setMaximumWidth(16777215)
        thumbs_lay = QVBoxLayout(self.pane_thumbs)
        thumbs_lay.setContentsMargins(0, 0, 0, 0)
        thumbs_lay.setSpacing(0)
        title = QLabel("目录")
        title.setObjectName("PaneTitle")
        thumbs_lay.addWidget(title)
        self.thumb_list = QListWidget()
        self.thumb_list.setObjectName("ThumbList")
        self.thumb_list.setViewMode(QListView.ViewMode.IconMode)
        self.thumb_list.setMovement(QListView.Movement.Static)
        self.thumb_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.thumb_list.setFlow(QListView.Flow.TopToBottom)
        self.thumb_list.setWrapping(False)
        self.thumb_list.setSpacing(8)
        self.thumb_list.setIconSize(QSize(112, 154))
        self.thumb_list.setUniformItemSizes(True)
        self.thumb_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.thumb_list.itemClicked.connect(
            lambda it: self.set_page(self.thumb_list.row(it)))
        thumbs_lay.addWidget(self.thumb_list, 1)

        # 右侧属性（默认收起，由图标栏按钮召唤）
        self.pane_props = QWidget()
        self.pane_props.setObjectName("PropPane")
        props_lay = QVBoxLayout(self.pane_props)
        props_lay.setContentsMargins(0, 0, 0, 0)
        props_lay.setSpacing(0)
        head = QWidget()
        head_lay = QHBoxLayout(head)
        head_lay.setContentsMargins(12, 8, 8, 4)
        head_lay.setSpacing(8)
        pt = QLabel("属性")
        pt.setObjectName("PaneTitle")
        pt.setStyleSheet("padding: 0;")
        head_lay.addWidget(pt)
        head_lay.addStretch(1)
        props_lay.addWidget(head)
        self.panel = PropertyPanel(self)
        props_lay.addWidget(self.panel, 1)
        self.pane_props.setMinimumWidth(240)

        self.stage = StageHost(self.canvas, self.pane_thumbs, self.pane_props)

        self.act_thumbs = QAction(make_icon("panel_left"), "目录", self)
        self.act_thumbs.setCheckable(True)
        self.act_thumbs.setChecked(False)
        self.act_thumbs.setToolTip("显示/隐藏目录")
        self.act_thumbs.toggled.connect(lambda on: self.stage.set_left_open(on, emit=False))
        self.stage.left_toggled.connect(self._sync_thumbs_action)
        self.addAction(self.act_thumbs)

        self.act_props = QAction(make_icon("menu"), "属性栏", self)
        self.act_props.setCheckable(True)
        self.act_props.setChecked(False)
        self.act_props.setToolTip("显示/隐藏属性栏")
        self.act_props.toggled.connect(self.stage.set_right_open)
        self.addAction(self.act_props)

        self.chrome = ChromeBar(self)

        def _btn(act):
            btn = QToolButton(self.chrome)
            btn.setDefaultAction(act)
            btn.setAutoRaise(True)
            btn.setIconSize(QSize(18, 18))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            return btn

        def _label(text, min_w):
            lb = QLabel(text, self.chrome)
            lb.setObjectName("ChromeLabel")
            lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lb.setMinimumWidth(min_w)
            return lb

        self.chrome.add_end(_btn(self.act_thumbs), "left")
        self.chrome.add_end(_btn(self.act_props), "right")
        for act in (self.act_open, self.act_save, self.act_save_as,
                    self.act_export, self.act_verify):
            self.chrome.add_item(_btn(act))
        self.chrome.add_sep()
        self.chrome.add_item(_btn(self.act_undo))
        self.chrome.add_item(_btn(self.act_redo))
        self.chrome.add_sep()
        self.chrome.add_item(_btn(self.act_zoom_out))
        self.lb_zoom = _label("100%", 44)
        self.chrome.add_item(self.lb_zoom)
        self.chrome.add_item(_btn(self.act_zoom_in))
        self.chrome.add_item(_btn(self.act_fit))
        self.chrome.add_sep()
        self.chrome.add_item(_btn(self.act_prev))
        self.lb_page = _label("- / -", 56)
        self.chrome.add_item(self.lb_page)
        self.chrome.set_pivot(self.lb_page)
        self.chrome.add_item(_btn(self.act_next))
        self.chrome.add_sep()
        for act in (self.act_fid_help, self.act_about):
            self.chrome.add_item(_btn(act))

        shell = QWidget()
        shell.setObjectName("RootSplit")
        shell_lay = QVBoxLayout(shell)
        shell_lay.setContentsMargins(0, 0, 0, 0)
        shell_lay.setSpacing(0)
        shell_lay.addWidget(self.chrome)
        shell_lay.addWidget(self.stage, 1)
        self.setCentralWidget(shell)

        self.menuBar().hide()
        self.statusBar().hide()

        self.canvas.set_hint("打开 PDF 后：单击选中文本框，双击进入编辑；图片可拖动/缩放/旋转")
        self._update_undo_actions()

    def _sync_thumbs_action(self, on):
        self.act_thumbs.blockSignals(True)
        self.act_thumbs.setChecked(on)
        self.act_thumbs.blockSignals(False)

    # ================================================== 文档管理
    def open_file(self, path=None):
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, "打开 PDF", "",
                                                  "PDF 文件 (*.pdf)")
            if not path:
                return
        try:
            doc = fitz.open(path)
        except Exception as e:
            QMessageBox.critical(self, "打开失败", f"无法打开 PDF：{e}")
            return
        if doc.needs_pass:
            for _ in range(3):
                pw, ok = QInputDialog.getText(
                    self, "密码", "该 PDF 已加密，请输入打开密码：",
                    QLineEdit.EchoMode.Password)
                if not ok:
                    doc.close()
                    return
                if doc.authenticate(pw):
                    break
            else:
                QMessageBox.warning(self, "打开失败", "密码错误。")
                doc.close()
                return
        self._load_doc(doc, path)

    def _load_doc(self, doc, path):
        self.doc = doc
        self.doc_path = path
        self.resolver = FontResolver(doc)
        self.ctx = SimpleNamespace(doc=doc, resolver=self.resolver)
        self.models, self.orig_renders, self.edit_regions, self.fidelities = {}, {}, {}, {}
        self.undo_stack.clear()
        self.session = None
        self.selected_block = None
        self.selected_image = None
        perm = getattr(fitz, "PDF_PERM_MODIFY", 4)
        self._can_modify = (not doc.is_encrypted) or bool(doc.permissions & perm)
        self.setWindowTitle(f"{os.path.basename(path)} — {APP_TITLE}")
        self.zoom = 1.0
        self.lb_zoom.setText("100%")
        self.chrome.relayout()
        self.set_page(0)
        self._build_thumbs()
        self.status_hint("编辑直接修改内容流（真删除，非遮盖）；撤销为字节级原版恢复")

    def current_page(self):
        if self.doc is None:
            return None
        return self.doc[self.page_no]

    def page_model(self):
        """当前页的文本框/图片模型。"""
        return self.models.get(self.page_no)

    def _model(self):
        return self.page_model()

    def set_page(self, i, *, edge=None):
        if self.doc is None:
            return
        if self.session is not None:
            self.commit_session()
        i = max(0, min(i, self.doc.page_count - 1))
        if i == self.page_no and self.canvas.model is not None:
            if edge:
                self.canvas.scroll_to_edge(edge)
            return
        self.page_no = i
        self.selected_block = None
        self.selected_image = None
        page = self.doc[i]
        if i not in self.models:
            self.models[i] = extract_page(page, i)
        if i not in self.fidelities:
            self.fidelities[i] = PageFidelity(self.models[i])
        if i not in self.orig_renders:
            self.orig_renders[i] = verifier.render_page_png(page)
        model = self.models[i]
        pm = self._render_qpixmap(page)
        self.canvas.zoom = self.zoom
        self.canvas.set_page_content(pm, model, model.rect[2], model.rect[3])
        self.canvas.set_hint("")
        self.canvas.apply_zoom()
        self.canvas.refresh_overlays()
        if edge:
            self.canvas.scroll_to_edge(edge)
        self.lb_page.setText(f"{i + 1} / {self.doc.page_count}")
        self.chrome.relayout()
        self.thumb_list.setCurrentRow(i)
        self._update_panels()

    def _render_qpixmap(self, page):
        dpr = self.devicePixelRatioF() or 1.0
        z = self.zoom * dpr
        pix = page.get_pixmap(matrix=fitz.Matrix(z, z), alpha=False)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride,
                     QImage.Format.Format_RGB888).copy()
        img.setDevicePixelRatio(z)
        return QPixmap.fromImage(img)

    def _build_thumbs(self):
        self.thumb_list.clear()
        if self.doc is None:
            return
        for i in range(self.doc.page_count):
            page = self.doc[i]
            zw = 120 / max(page.rect.width, 1)
            pix = page.get_pixmap(matrix=fitz.Matrix(zw, zw), alpha=False)
            img = QImage(pix.samples, pix.width, pix.height, pix.stride,
                         QImage.Format.Format_RGB888).copy()
            item = QListWidgetItem(QPixmap.fromImage(img), f"{i + 1}")
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
            self.thumb_list.addItem(item)
            if i % 5 == 4:
                QApplication.processEvents()

    def _update_thumb(self, i):
        if self.doc is None or not (0 <= i < self.thumb_list.count()):
            return
        page = self.doc[i]
        zw = 120 / max(page.rect.width, 1)
        pix = page.get_pixmap(matrix=fitz.Matrix(zw, zw), alpha=False)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride,
                     QImage.Format.Format_RGB888).copy()
        self.thumb_list.item(i).setIcon(QPixmap.fromImage(img))

    # ================================================== 命中与选择
    def hit_block(self, x, y):
        model = self._model()
        if model is None:
            return None
        return model.block_at(x, y)

    def image_at(self, x, y):
        model = self._model()
        if model is None:
            return None
        return model.image_at(x, y)

    def select_block(self, block):
        if not self._editable():
            return
        self.selected_block = block
        self.selected_image = None
        self.canvas.refresh_overlays()
        self.status_hint("文本框已选中：拖动移动，左右手柄调宽；双击进入编辑；Delete 删除")

    def select_image(self, img):
        if not self._editable():
            return
        self.selected_image = img
        self.selected_block = None
        self.canvas.refresh_overlays()
        self.status_hint("图片已选中：拖动移动，8 控制点缩放，圆形手柄旋转；Delete 删除")

    def deselect_all(self):
        self.selected_block = None
        self.selected_image = None
        self.canvas.refresh_overlays()

    # ================================================== 会话生命周期
    def _editable(self):
        if self.doc is None:
            self.status_hint("请先打开 PDF 文件")
            return False
        if not self._can_modify:
            self.status_hint("文档权限禁止修改（受保护 PDF）")
            return False
        return True

    def start_session(self, block):
        if not self._editable():
            return
        if self.session is not None:
            self.commit_session()
        if not block.horizontal:
            self.status_hint("旋转/竖排文本暂不支持（第二阶段）")
            return
        page = self.current_page()
        before = capture_page_state(self.doc, page)
        buffer = BoxBuffer(block)
        oracle = FontOracle.from_block(self.resolver, page, block)
        buffer.set_measure(oracle.adv_fn())
        sess = EditSession(block, self.page_no, buffer, before, oracle)
        self.session = sess
        self.selected_block = None
        self.selected_image = None
        sess.cursor = (0, 0)
        self.canvas.refresh_overlays()
        self.canvas._grab_focus()
        self.status_hint("编辑中：单击定位，拖选/双击选词，Enter 换行，Esc 取消，点击框外提交")
        self._update_panels()

    def _restore_page(self, page_index, state):
        restore_page_state(self.doc, self.doc[page_index], state)
        if self.resolver is not None:
            self.resolver.invalidate_page(page_index)

    def _sync_session_preview(self):
        """缓冲已变则按提交路径重写当前页并重绘；恢复未改动则还原原页。"""
        sess = self.session
        if sess is None:
            return
        page_index = sess.page_index
        if not sess.buffer.changed:
            if sess.previewed:
                self._restore_page(page_index, sess.before_state)
                sess.previewed = False
                sess.oracle.page = self.doc[page_index]
                self._refresh_render_only()
            self.canvas.refresh_overlays()
            self.canvas._grab_focus()
            self._update_panels()
            return
        runs = sess.buffer.commit_runs(sess.oracle)
        page = executor.apply_box_rebuild(
            self.doc, page_index, sess.before_state,
            line_redact_rects(sess.block), runs, self.resolver)
        sess.oracle.page = page
        sess.previewed = True
        self._refresh_render_only()
        self.canvas.refresh_overlays()
        self.canvas._grab_focus()
        self._update_panels()

    def commit_session(self):
        sess = self.session
        if sess is None:
            return
        self.session = None
        self.session_preedit_clear()
        if not sess.buffer.changed:
            if sess.previewed:
                self._restore_page(sess.page_index, sess.before_state)
                self._refresh_page()
            else:
                self.canvas.refresh_overlays()
                self._update_panels()
            return
        runs = sess.buffer.commit_runs(sess.oracle)
        if not sess.previewed:
            executor.apply_box_rebuild(
                self.doc, sess.page_index, sess.before_state,
                line_redact_rects(sess.block), runs, self.resolver)
        page = self.doc[sess.page_index]
        after = capture_page_state(self.doc, page)
        cmd = PageStateCommand("编辑文本框", self.page_no,
                               sess.before_state, after)
        cmd.edit_rects = [_expand(sess.block.bbox, 1.0),
                          sess.buffer.bbox()]
        self._register_cmd(cmd, sess.buffer, runs)

    def cancel_session(self):
        sess = self.session
        if sess is None:
            return
        self.session = None
        self.session_preedit_clear()
        if sess.previewed:
            self._restore_page(sess.page_index, sess.before_state)
            self._refresh_page()
        else:
            self.canvas.refresh_overlays()
            self._update_panels()
        self.status_hint("已取消编辑（字节级恢复原版）")

    def _register_cmd(self, cmd, buffer=None, runs=None):
        self.undo_stack.push(cmd, self.ctx)
        fid = self.fidelities.setdefault(self.page_no, PageFidelity())
        model = self._model()
        if model is not None and model.scanned:
            fid.level = "red"
            if model.scanned_note not in fid.reasons:
                fid.reasons.append(model.scanned_note)
        if buffer is not None and runs is not None:
            level = "green"
            for text, st, x, bl, rf in runs:
                if not rf.is_original:
                    level = worse(level, "yellow")
                    r = f"新增字符使用{rf.source}"
                    if r not in fid.reasons:
                        fid.reasons.append(r)
            if len(buffer.visual) > getattr(buffer, "_orig_visual_count", 0):
                if "内容重排（自动换行）" not in fid.reasons:
                    fid.reasons.append("内容重排（自动换行）")
                level = worse(level, "yellow")
            fid.level = worse(fid.level, level)
        for r in getattr(cmd, "edit_rects", []) or []:
            self.edit_regions.setdefault(self.page_no, []).append(tuple(r))
        self._refresh_page()

    # ================================================== 会话编辑操作
    def session_click(self, x, y, extend=False):
        sess = self.session
        if sess is None:
            return
        pos = sess.buffer.hit_test(x, y, sess.oracle.adv_fn())
        if extend and sess.cursor is not None:
            anchor = sess.selection[0] if sess.selection else sess.cursor
            sess.selection = (anchor, pos)
        else:
            sess.selection = None
        sess.cursor = pos
        self.canvas.box_editor.update()

    def session_drag(self, x, y):
        sess = self.session
        if sess is None:
            return
        pos = sess.buffer.hit_test(x, y, sess.oracle.adv_fn())
        anchor = sess.selection[0] if sess.selection else sess.cursor
        sess.selection = (anchor, pos)
        sess.cursor = pos
        self.canvas.box_editor.update()

    def session_select_word(self, x, y):
        sess = self.session
        if sess is None:
            return
        pos = sess.buffer.hit_test(x, y, sess.oracle.adv_fn())
        hl, off = pos
        a, b = sess.buffer.word_bounds(pos)
        sess.selection = ((hl, a), (hl, b))
        sess.cursor = (hl, b)
        self.canvas.box_editor.update()

    def session_select_visual_line(self, x, y):
        sess = self.session
        if sess is None:
            return
        # 找到所在可视行
        best_v, best_d = None, None
        for v in sess.buffer.visual:
            d = abs(y - v.baseline)
            if best_d is None or d < best_d:
                best_v, best_d = v, d
        if best_v is None:
            return
        sess.selection = ((best_v.hard_idx, best_v.start), (best_v.hard_idx, best_v.end))
        sess.cursor = (best_v.hard_idx, best_v.end)
        self.canvas.box_editor.update()

    def session_select_all(self):
        sess = self.session
        if sess is None:
            if self.selected_block is not None:
                t = self.selected_block.text()
                if t:
                    QApplication.clipboard().setText(t)
                    self.status_hint("已复制选中文本框内容")
            return
        hl = len(sess.buffer.hard_lines) - 1
        sess.selection = ((0, 0), (hl, len(sess.buffer.hard_lines[hl])))
        sess.cursor = (hl, len(sess.buffer.hard_lines[hl]))
        self.canvas.box_editor.update()

    def session_insert(self, text):
        sess = self.session
        if sess is None:
            return
        text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
        if not text:
            return
        if sess.selection:
            sess.cursor = self._session_delete_selection()
        sess.cursor = sess.buffer.insert(sess.cursor, text)
        self._sync_session_preview()

    def session_split_line(self):
        sess = self.session
        if sess is None:
            return
        if sess.selection:
            self._session_delete_selection()
        sess.cursor = sess.buffer.split_line(sess.cursor)
        self._sync_session_preview()

    def session_backspace(self):
        sess = self.session
        if sess is None:
            return
        if sess.selection:
            sess.cursor = self._session_delete_selection()
        else:
            sess.cursor = sess.buffer.backspace(sess.cursor)
        self._sync_session_preview()

    def session_delete_forward(self):
        sess = self.session
        if sess is None:
            return
        if sess.selection:
            sess.cursor = self._session_delete_selection()
        else:
            sess.cursor = sess.buffer.delete_forward(sess.cursor)
        self._sync_session_preview()

    def _session_delete_selection(self):
        sess = self.session
        anchor, focus = sess.selection
        sess.selection = None
        return sess.buffer.delete_selection(anchor, focus)

    def session_move(self, delta, word=False, extend=False):
        sess = self.session
        if sess is None:
            return
        hl, off = sess.cursor
        if word:
            pos = sess.buffer.word_step(sess.cursor, delta > 0)
        else:
            hl_lines = sess.buffer.hard_lines
            if delta > 0:
                if off < len(hl_lines[hl]):
                    pos = (hl, off + 1)
                elif hl < len(hl_lines) - 1:
                    pos = (hl + 1, 0)
                else:
                    pos = sess.cursor
            else:
                if off > 0:
                    pos = (hl, off - 1)
                elif hl > 0:
                    pos = (hl - 1, len(hl_lines[hl - 1]))
                else:
                    pos = sess.cursor
        if extend:
            anchor = sess.selection[0] if sess.selection else sess.cursor
            sess.selection = (anchor, pos)
        else:
            sess.selection = None
        sess.cursor = pos
        self.canvas.box_editor.update()

    def session_move_vertical(self, dy, extend=False):
        sess = self.session
        if sess is None:
            return
        adv = sess.oracle.adv_fn()
        x, bl = sess.buffer.cursor_pos(sess.cursor, adv)
        target = bl + dy * sess.buffer.line_height
        pos = sess.buffer.hit_test(x, target, adv)
        if extend:
            anchor = sess.selection[0] if sess.selection else sess.cursor
            sess.selection = (anchor, pos)
        else:
            sess.selection = None
        sess.cursor = pos
        self.canvas.box_editor.update()

    def session_move_edge(self, head, extend=False):
        sess = self.session
        if sess is None:
            return
        hl, off = sess.cursor
        pos = (hl, 0 if head else len(sess.buffer.hard_lines[hl]))
        if extend:
            anchor = sess.selection[0] if sess.selection else sess.cursor
            sess.selection = (anchor, pos)
        else:
            sess.selection = None
        sess.cursor = pos
        self.canvas.box_editor.update()

    def session_cursor_pos(self):
        sess = self.session
        if sess is None:
            return (0, 0)
        return sess.buffer.cursor_pos(sess.cursor, sess.oracle.adv_fn())

    def session_set_preedit(self, text):
        sess = self.session
        if sess is None:
            return
        sess.preedit = text or ""
        self.canvas.box_editor.update()

    def session_preedit_clear(self):
        if self.session is not None:
            self.session.preedit = ""
            self.canvas.box_editor.update()

    def current_style(self):
        sess = self.session
        if sess is not None:
            return sess.buffer.style_at(sess.cursor)
        if self.selected_block is not None:
            return self.selected_block.dominant_style()
        return None

    # ================================================== 剪贴板
    def copy(self):
        sess = self.session
        if sess is None:
            if self.selected_block is not None:
                t = self.selected_block.text()
                if t:
                    QApplication.clipboard().setText(t)
                    self.status_hint("已复制选中文本框内容")
            return
        if sess.selection:
            t = sess.buffer.selection_text(*sess.selection)
            if t:
                QApplication.clipboard().setText(t)

    def cut(self):
        sess = self.session
        if sess is None or not sess.selection:
            return
        t = sess.buffer.selection_text(*sess.selection)
        if t:
            QApplication.clipboard().setText(t)
            sess.cursor = self._session_delete_selection()
            self._sync_session_preview()

    def paste(self):
        sess = self.session
        if sess is None:
            self.status_hint("请先双击进入文本框编辑")
            return
        text = QApplication.clipboard().text()
        if text:
            self.session_insert(text)

    # ================================================== 文本框变换（移动/调宽）
    def commit_block_transform(self, block, dx, dy, dw, title):
        if not self._editable():
            return
        page = self.current_page()
        before = capture_page_state(self.doc, page)
        buffer = BoxBuffer(block)
        oracle = FontOracle.from_block(self.resolver, page, block)
        buffer.set_measure(oracle.adv_fn())
        if abs(dx) > 0.5 or abs(dy) > 0.5:
            buffer.translate(dx, dy)
        if abs(dw) > 0.5:
            # dw 相对 PDF 框宽，不能加在 auto_width 后的内容宽度上
            buffer.set_width((block.bbox[2] - block.bbox[0]) + dw)
        executor.remove_text_region(page, line_redact_rects(block))
        runs = buffer.commit_runs(oracle)
        executor.insert_runs(page, runs, self.resolver)
        after = capture_page_state(self.doc, page)
        cmd = PageStateCommand(title, self.page_no, before, after)
        cmd.edit_rects = [_expand(block.bbox, 1.0), buffer.bbox()]
        self._register_cmd(cmd, buffer, runs)

    def delete_selected_block(self):
        if not self._editable() or self.selected_block is None:
            return
        block = self.selected_block
        page = self.current_page()
        before = capture_page_state(self.doc, page)
        executor.remove_text_region(page, line_redact_rects(block))
        after = capture_page_state(self.doc, page)
        cmd = PageStateCommand("删除文本框内容", self.page_no, before, after)
        cmd.edit_rects = [_expand(block.bbox, 1.0)]
        self.deselect_all()
        self._register_cmd(cmd)
        self.status_hint("文本框内容已删除（可撤销）")

    # ================================================== 图片操作
    def commit_image_transform(self, old_rect, old_deg, new_rect, new_deg, title):
        if not self._editable():
            return
        page = self.current_page()
        blob = None
        img = self.selected_image
        if img is not None:
            blob = executor.image_blob(self.doc, img.xref)
        if not blob:
            self.status_hint("无法提取图片数据")
            return
        before = capture_page_state(self.doc, page)
        executor.place_image(self.doc, page, [old_rect], new_rect, new_deg, blob)
        page = self.current_page()  # place_image 内部 reload，需重新获取
        after = capture_page_state(self.doc, page)
        cmd = PageStateCommand(title, self.page_no, before, after)
        cmd.edit_rects = [old_rect, new_rect]
        self._register_cmd(cmd)
        # 重新选中新位置的图片
        model = self._model()
        best, best_v = None, 0.0
        for im in (model.images if model else []):
            v = _rect_iou(im.rect, new_rect)
            if v > best_v:
                best_v, best = v, im
        if best is not None and best_v > 0.7:
            self.selected_image = best
        self.canvas.refresh_overlays()

    def delete_selected_image(self):
        if not self._editable() or self.selected_image is None:
            return
        img = self.selected_image
        page = self.current_page()
        before = capture_page_state(self.doc, page)
        executor.place_image(self.doc, page, [img.rect], None, 0, b"")
        page = self.current_page()  # reload 后重新获取
        after = capture_page_state(self.doc, page)
        cmd = PageStateCommand("删除图片", self.page_no, before, after)
        cmd.edit_rects = [img.rect]
        self.deselect_all()
        self._register_cmd(cmd)
        self.status_hint("图片已删除（可撤销）")

    def replace_selected_image(self):
        if not self._editable() or self.selected_image is None:
            return
        img = self.selected_image
        path, _ = QFileDialog.getOpenFileName(
            self, "选择替换图片", "", "图片 (*.png *.jpg *.jpeg *.bmp)")
        if not path:
            return
        with open(path, "rb") as f:
            new_blob = f.read()
        old_blob = executor.image_blob(self.doc, img.xref)
        if not old_blob:
            self.status_hint("无法提取原图数据")
            return
        cmd = ImageReplaceCommand(self.page_no, img.xref, old_blob, new_blob)
        cmd.edit_rects = [img.rect]
        self.undo_stack.push(cmd, self.ctx)
        self.edit_regions.setdefault(self.page_no, []).append(tuple(img.rect))
        self._refresh_page()
        self.status_hint("图片内容已替换（位置与变换不变）")

    # ================================================== 撤销/重做
    def undo(self):
        if self.doc is None:
            return
        if self.session is not None:
            self.cancel_session()
            return
        r = self.undo_stack.undo(self.ctx)
        if not r:
            return
        cmd, _ = r
        for rect in getattr(cmd, "edit_rects", []) or []:
            self.edit_regions.setdefault(self.page_no, []).append(tuple(rect))
        self.deselect_all()
        self._refresh_page()
        self.status_hint(f"已撤销：{cmd.title}（页面字节级恢复）")

    def redo(self):
        if self.doc is None or self.session is not None:
            return
        r = self.undo_stack.redo(self.ctx)
        if not r:
            return
        cmd, _ = r
        for rect in getattr(cmd, "edit_rects", []) or []:
            self.edit_regions.setdefault(self.page_no, []).append(tuple(rect))
        self.deselect_all()
        self._refresh_page()

    # ================================================== 刷新
    def _refresh_render_only(self):
        """仅重渲染页面位图（会话开始/编辑中用，模型不变）。"""
        if self.doc is None:
            return
        page = self.current_page()
        pm = self._render_qpixmap(page)
        model = self._model()
        self.canvas.zoom = self.zoom
        self.canvas.set_page_content(pm, model,
                                     model.rect[2], model.rect[3])
        self.canvas.apply_zoom()

    def _refresh_page(self):
        if self.doc is None:
            return
        page = self.doc[self.page_no]
        model = extract_page(page, self.page_no)
        self.models[self.page_no] = model
        pm = self._render_qpixmap(page)
        self.canvas.zoom = self.zoom
        self.canvas.set_page_content(pm, model, model.rect[2], model.rect[3])
        self.canvas.apply_zoom()
        self.canvas.refresh_overlays()
        self._update_thumb(self.page_no)
        self._update_panels()

    # ================================================== 保存与验证
    def save(self):
        if self.doc is None:
            return
        if self.session is not None:
            self.commit_session()
        self._do_save(self.doc_path)

    def save_as(self):
        if self.doc is None:
            return
        if self.session is not None:
            self.commit_session()
        path, _ = QFileDialog.getSaveFileName(self, "另存为", self.doc_path,
                                              "PDF 文件 (*.pdf)")
        if path:
            if self._do_save(path):
                self.doc_path = path
                self.setWindowTitle(f"{os.path.basename(path)} — {APP_TITLE}")

    def _do_save(self, path):
        tmp = path + ".tmp.pdf"
        try:
            self.doc.save(tmp, garbage=3, deflate=True)
            os.replace(tmp, path)
        except PermissionError:
            QMessageBox.warning(self, "保存失败",
                                "目标文件被占用，请关闭其他 PDF 阅读器后重试。")
            return False
        except Exception as e:
            QMessageBox.warning(self, "保存失败", f"保存出错：{e}")
            return False
        self.status_hint("已保存，正在执行保存后验证…")
        report = verifier.verify(path, self.orig_renders, self.edit_regions,
                                 page_count=self.doc.page_count)
        DiffDialog(report, self).exec()
        return True

    def save_optimized(self):
        if self.doc is None:
            return
        if self.session is not None:
            self.commit_session()
        path, _ = QFileDialog.getSaveFileName(
            self, "导出优化副本", self.doc_path.replace(".pdf", "_opt.pdf"),
            "PDF 文件 (*.pdf)")
        if not path:
            return
        try:
            blob = self.doc.tobytes(garbage=3, deflate=True)
            d2 = fitz.open("pdf", blob)
            try:
                d2.subset_fonts()
            except Exception:
                pass
            d2.save(path, garbage=3, deflate=True)
            d2.close()
        except Exception as e:
            QMessageBox.warning(self, "导出失败", f"{e}")
            return
        QMessageBox.information(self, "导出完成",
                                 f"优化副本已保存：\n{path}\n（原字体子集化，体积更小；"
                                 "当前编辑会话不受影响）")

    def reverify(self):
        if self.doc is None:
            return
        if self.session is not None:
            self.commit_session()
        fd, tmp = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        try:
            self.doc.save(tmp, garbage=3, deflate=True)
            report = verifier.verify(tmp, self.orig_renders, self.edit_regions,
                                     page_count=self.doc.page_count)
            DiffDialog(report, self).exec()
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

    # ================================================== 视图
    def set_zoom(self, z):
        z = max(0.15, min(6.0, z))
        self.zoom = z
        self.lb_zoom.setText(f"{z * 100:.0f}%")
        self.chrome.relayout()
        if self.doc is None:
            return
        self._refresh_render_only()
        self.canvas.refresh_overlays()

    def fit_width(self):
        if self.doc is None:
            return
        page = self.doc[self.page_no]
        z = (self.canvas.viewport().width() - 20) / max(page.rect.width, 1)
        self.set_zoom(z)

    def _on_hover(self, x, y):
        return

    def status_hint(self, msg):
        return

    def _update_panels(self):
        self.panel.update_style(self.current_style())
        sess = self.session
        if sess is not None:
            n = sess.buffer.char_count()
            sel = 0
            if sess.selection:
                for (hi, s, e) in sess.buffer.selection_range(*sess.selection):
                    sel += e - s
            self.panel.update_selection(sel, n)
        else:
            self.panel.update_selection(0, 0)
        fid = self.fidelities.get(self.page_no)
        if fid is not None:
            self.panel.update_fidelity(fid.level, fid.reasons)
        else:
            self.panel.update_fidelity("—", [])

    def _update_undo_actions(self):
        self.act_undo.setEnabled(self.undo_stack.can_undo)
        self.act_redo.setEnabled(self.undo_stack.can_redo)
        if self.undo_stack.can_undo:
            self.act_undo.setText(f"撤销（{len(self.undo_stack._undo)}）")
        else:
            self.act_undo.setText("撤销")
        if self.undo_stack.can_redo:
            self.act_redo.setText(f"重做（{len(self.undo_stack._redo)}）")
        else:
            self.act_redo.setText("重做")

    # ================================================== 其他
    def show_fidelity_help(self):
        QMessageBox.information(
            self, "保真等级说明",
            "绿色 原生编辑：原字体、原布局完整保留。\n\n"
            "黄色 局部重建：相同或近似字体重建（字体替代、自动换行重排）。\n\n"
            "橙色 覆盖编辑：文本超出原区域或覆盖实现。\n\n"
            "红色 图像/OCR 编辑：页面为扫描件图像。")

    def show_about(self):
        QMessageBox.about(
            self, "关于",
            "<b>PDF 无感编辑器（文本框编辑版）</b><br><br>"
            "类 PPT 交互：单击选中文本框（拖动/调宽），双击进入框内编辑；"
            "图片拖动/缩放/旋转。<br>"
            "编辑直接修改内容流（真删除，非遮盖）；撤销为<b>页面快照字节级恢复</b>，"
            "可回到原版 PDF；保存后自动执行结构校验与视觉回归验证。")

    def closeEvent(self, e):
        if self.session is not None:
            self.commit_session()
        if self.doc is not None and self.undo_stack.can_undo:
            ret = QMessageBox.question(
                self, "未保存的修改", "存在未保存的修改，确定退出吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ret != QMessageBox.StandardButton.Yes:
                e.ignore()
                return
        super().closeEvent(e)


# ---------------------------------------------------------------- 工具函数

def _expand(rect, m):
    return (rect[0] - m, rect[1] - m, rect[2] + m, rect[3] + m)


def _rect_iou(a, b):
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    ua = ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)
    return inter / ua if ua > 0 else 0.0
