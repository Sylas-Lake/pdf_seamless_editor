"""主窗口：编辑控制器 + 客户端编辑层编排。

对应方案第九章推荐架构：
  客户端编辑层（本窗口/画布/面板）
  ↕ 文档中间层（core.commands 统一命令 + 撤销栈）
  ↕ PDF 核心引擎（PyMuPDF + core.executor 内容流重写）
"""
import os
import tempfile
from types import SimpleNamespace

try:
    import pymupdf as fitz
except ImportError:
    import fitz

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QImage, QPixmap
from PySide6.QtWidgets import (QApplication, QComboBox, QDockWidget,
                                QFileDialog, QInputDialog, QLabel, QLineEdit,
                                QListWidget, QListWidgetItem, QMainWindow,
                                QMessageBox, QToolBar, QWidget)

from core import executor, verifier
from core.commands import (ImageDeleteCommand, ImageReplaceCommand,
                           ImageState, ImageTransformCommand, TextStyleCommand,
                           TextEditCommand, UndoStack)
from core.extractor import (boundary_x, cursor_index_at, extract_page,
                             find_line, find_line_by_bbox, inherited_style,
                             n_grapheme_clusters, select_word_at, word_step)
from core.fidelity import COLORS, LABELS, PageFidelity
from core.fonts import FontResolver
from core.sample import create_sample_pdf
from ui.diff_dialog import DiffDialog
from ui.page_canvas import PageCanvas
from ui.property_panel import PropertyPanel

APP_TITLE = "PDF 无感编辑器 · 第一阶段 MVP"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1280, 860)

        # ---- 文档状态 ----
        self.doc = None
        self.doc_path = ""
        self.resolver = None
        self.models = {}
        self.orig_renders = {}
        self.edit_regions = {}
        self.fidelities = {}
        self._can_modify = True

        # ---- 编辑状态 ----
        self.cursor = None          # (line_index, glyph_index)
        self.selection = None       # (anchor, focus)
        self._sel_image = None
        self._overflow_strategy = "shrink"
        self.zoom = 1.0
        self.page_no = 0

        self.undo_stack = UndoStack(on_change=self._update_undo_actions)
        self.ctx = None

        self._build_ui()

    # ================================================== UI 构建
    def _build_ui(self):
        # 画布
        self.canvas = PageCanvas(self)
        self.setCentralWidget(self.canvas)
        self.canvas.hover_pos.connect(self._on_hover)
        self.canvas.image_transform_committed.connect(self.commit_image_transform)
        self.canvas.image_delete_requested.connect(self.delete_selected_image)
        self.canvas.image_replace_requested.connect(self.replace_selected_image)

        # 工具栏
        tb = QToolBar("主工具栏")
        tb.setMovable(False)
        self.addToolBar(tb)
        self.act_open = QAction("打开", self)
        self.act_open.setShortcut("Ctrl+O")
        self.act_open.triggered.connect(lambda: self.open_file())
        tb.addAction(self.act_open)

        self.act_save = QAction("保存", self)
        self.act_save.setShortcut("Ctrl+S")
        self.act_save.triggered.connect(self.save)
        tb.addAction(self.act_save)

        self.act_verify = QAction("验证报告", self)
        self.act_verify.triggered.connect(self.reverify)
        tb.addAction(self.act_verify)
        tb.addSeparator()

        self.act_undo = QAction("撤销", self)
        self.act_undo.setShortcut("Ctrl+Z")
        self.act_undo.triggered.connect(self.undo)
        tb.addAction(self.act_undo)
        self.act_redo = QAction("重做", self)
        self.act_redo.setShortcut("Ctrl+Y")
        self.act_redo.triggered.connect(self.redo)
        tb.addAction(self.act_redo)
        tb.addSeparator()

        self.act_zoom_out = QAction("缩小", self)
        self.act_zoom_out.setShortcut("Ctrl+-")
        self.act_zoom_out.triggered.connect(lambda: self.set_zoom(self.zoom / 1.15))
        tb.addAction(self.act_zoom_out)
        self.lb_zoom = QLabel("100%")
        self.lb_zoom.setMinimumWidth(52)
        tb.addWidget(self.lb_zoom)
        self.act_zoom_in = QAction("放大", self)
        self.act_zoom_in.setShortcut("Ctrl+=")
        self.act_zoom_in.triggered.connect(lambda: self.set_zoom(self.zoom * 1.15))
        tb.addAction(self.act_zoom_in)
        self.act_fit = QAction("适应宽度", self)
        self.act_fit.triggered.connect(self.fit_width)
        tb.addAction(self.act_fit)
        tb.addSeparator()

        self.act_prev = QAction("上一页", self)
        self.act_prev.triggered.connect(lambda: self.set_page(self.page_no - 1))
        tb.addAction(self.act_prev)
        self.lb_page = QLabel("- / -")
        tb.addWidget(self.lb_page)
        self.act_next = QAction("下一页", self)
        self.act_next.triggered.connect(lambda: self.set_page(self.page_no + 1))
        tb.addAction(self.act_next)
        tb.addSeparator()

        self.cmb_mode = QComboBox()
        self.cmb_mode.addItem("模式：原位替换（固定版式）")
        self.cmb_mode.addItem("段落重排（第二阶段）")
        self.cmb_mode.addItem("自由布局（第二阶段）")
        self.cmb_mode.model().item(1).setEnabled(False)
        self.cmb_mode.model().item(2).setEnabled(False)
        self.cmb_mode.setToolTip("第一阶段仅支持原位替换模式")
        tb.addWidget(self.cmb_mode)

        self.act_sample = QAction("生成示例", self)
        self.act_sample.triggered.connect(self.make_sample)
        tb.addAction(self.act_sample)

        # 左侧缩略图
        self.thumb_list = QListWidget()
        self.thumb_list.setIconSize(QPixmap(120, 170).size())
        self.thumb_list.setFixedWidth(150)
        self.thumb_list.itemClicked.connect(
            lambda it: self.set_page(self.thumb_list.row(it)))
        dock_t = QDockWidget("页面缩略图", self)
        dock_t.setWidget(self.thumb_list)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock_t)

        # 右侧属性面板
        self.panel = PropertyPanel(self)
        self.panel.apply_style.connect(self.apply_style_to_selection)
        self.panel.overflow_changed.connect(self._set_overflow)
        dock_p = QDockWidget("属性", self)
        dock_p.setWidget(self.panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock_p)

        # 菜单
        m_file = self.menuBar().addMenu("文件(&F)")
        m_file.addAction(self.act_open)
        m_file.addAction(self.act_save)
        act_saveas = QAction("另存为…", self)
        act_saveas.setShortcut("Ctrl+Shift+S")
        act_saveas.triggered.connect(self.save_as)
        m_file.addAction(act_saveas)
        act_opt = QAction("导出优化副本（字体子集化）…", self)
        act_opt.triggered.connect(self.save_optimized)
        m_file.addAction(act_opt)
        m_file.addSeparator()
        act_quit = QAction("退出", self)
        act_quit.triggered.connect(self.close)
        m_file.addAction(act_quit)

        m_edit = self.menuBar().addMenu("编辑(&E)")
        m_edit.addAction(self.act_undo)
        m_edit.addAction(self.act_redo)
        m_edit.addSeparator()
        act_selall = QAction("全选（本页文本）", self)
        act_selall.setShortcut("Ctrl+A")
        act_selall.triggered.connect(self.select_all)
        m_edit.addAction(act_selall)
        act_copy = QAction("复制", self)
        act_copy.setShortcut("Ctrl+C")
        act_copy.triggered.connect(self.copy_selection)
        m_edit.addAction(act_copy)
        act_cut = QAction("剪切", self)
        act_cut.setShortcut("Ctrl+X")
        act_cut.triggered.connect(self.cut_selection)
        m_edit.addAction(act_cut)
        act_paste = QAction("粘贴", self)
        act_paste.setShortcut("Ctrl+V")
        act_paste.triggered.connect(self.paste)
        m_edit.addAction(act_paste)

        m_view = self.menuBar().addMenu("视图(&V)")
        m_view.addAction(self.act_zoom_in)
        m_view.addAction(self.act_zoom_out)
        m_view.addAction(self.act_fit)
        m_view.addAction(dock_t.toggleViewAction())
        m_view.addAction(dock_p.toggleViewAction())

        m_help = self.menuBar().addMenu("帮助(&H)")
        act_fid = QAction("保真等级说明", self)
        act_fid.triggered.connect(self.show_fidelity_help)
        m_help.addAction(act_fid)
        act_about = QAction("关于", self)
        act_about.triggered.connect(self.show_about)
        m_help.addAction(act_about)

        # 状态栏
        self.lb_pos = QLabel("—")
        self.lb_mode = QLabel("原位替换模式")
        self.lb_fid = QLabel("未打开")
        self.lb_fid.setStyleSheet("color:#fff; background:#888; border-radius:3px; padding:2 8px;")
        sb = self.statusBar()
        sb.addWidget(self.lb_pos)
        sb.addPermanentWidget(self.lb_mode)
        sb.addPermanentWidget(self.lb_fid)

        self.canvas.set_hint("请打开 PDF 文件，或点击工具栏「生成示例」")
        self._update_undo_actions()

    # ================================================== 文档管理
    def open_file(self, path=None):
        if path is None:
            path, _ = QFileDialog.getOpenFileName(
                self, "打开 PDF", "", "PDF 文件 (*.pdf)")
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
        self.cursor = None
        self.selection = None
        self._sel_image = None
        perm = getattr(fitz, "PDF_PERM_MODIFY", 4)
        self._can_modify = (not doc.is_encrypted) or bool(doc.permissions & perm)
        self.setWindowTitle(f"{os.path.basename(path)} — {APP_TITLE}")
        self.set_page(0)
        self._build_thumbs()
        self.status_hint("文档已打开：编辑将直接修改内容流（真删除，非遮盖）")

    def current_page(self):
        if self.doc is None:
            return None
        return self.doc[self.page_no]

    def _model(self):
        return self.models.get(self.page_no)

    def set_page(self, i):
        if self.doc is None:
            return
        i = max(0, min(i, self.doc.page_count - 1))
        self.page_no = i
        self.cursor = None
        self.selection = None
        self._sel_image = None
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
        self.canvas.update_cursor_item()
        self.canvas.update_selection_items()
        self.canvas._refresh_handles()
        self.lb_page.setText(f"{i + 1} / {self.doc.page_count}")
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
        w = 120
        for i in range(self.doc.page_count):
            page = self.doc[i]
            zw = w / max(page.rect.width, 1)
            pix = page.get_pixmap(matrix=fitz.Matrix(zw, zw), alpha=False)
            img = QImage(pix.samples, pix.width, pix.height, pix.stride,
                         QImage.Format.Format_RGB888).copy()
            it = QListWidgetItem(QPixmap.fromImage(img), f"{i + 1}")
            self.thumb_list.addItem(it)
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

    # ================================================== 光标/选区 API（画布调用）
    def cursor_state(self):
        model = self._model()
        if model is None or self.cursor is None:
            return None
        li, gi = self.cursor
        if 0 <= li < len(model.lines):
            return model.lines[li], gi
        return None

    def hit_test(self, x, y):
        model = self._model()
        if model is None:
            return None
        line = find_line(model, x, y)
        if line is None or not line.horizontal:
            return None
        return line.index, cursor_index_at(line, x)

    def set_cursor(self, li, gi, extend=False):
        if not self._editable_page():
            return
        model = self._model()
        if model is None or not (0 <= li < len(model.lines)):
            return
        line = model.lines[li]
        gi = max(0, min(gi, len(line.glyphs)))
        if extend and (self.selection or self.cursor is not None):
            anchor = self.selection[0] if self.selection else self.cursor
            self.selection = (anchor, (li, gi))
            self.cursor = (li, gi)
        else:
            self.cursor = (li, gi)
            self.selection = None
        self._update_canvas_overlays()

    def extend_selection(self, li, gi):
        model = self._model()
        if model is None or not (0 <= li < len(model.lines)):
            return
        if self.selection is None:
            if self.cursor is None:
                return
            self.selection = (self.cursor, (li, gi))
        else:
            self.selection = (self.selection[0], (li, gi))
        self.cursor = (li, gi)
        self._update_canvas_overlays()

    def select_word(self, li, gi):
        model = self._model()
        if model is None or not (0 <= li < len(model.lines)):
            return
        line = model.lines[li]
        a, b = select_word_at(line, gi)
        self.selection = ((li, a), (li, b))
        self.cursor = (li, b)
        self._update_canvas_overlays()

    def select_line(self, li):
        model = self._model()
        if model is None or not (0 <= li < len(model.lines)):
            return
        line = model.lines[li]
        self.selection = ((li, 0), (li, len(line.glyphs)))
        self.cursor = (li, len(line.glyphs))
        self._update_canvas_overlays()

    def select_all(self):
        model = self._model()
        if model is None or not model.lines:
            return
        last = len(model.lines) - 1
        self.selection = ((0, 0), (last, len(model.lines[last].glyphs)))
        self.cursor = (last, len(model.lines[last].glyphs))
        self._update_canvas_overlays()

    def clear_selection(self):
        self.selection = None
        self.canvas.update_selection_items()
        self._update_sel_stats()

    def selection_segments(self):
        model = self._model()
        if model is None or self.selection is None or not model.lines:
            return []
        (la, ia), (lf, iff) = self.selection
        la = max(0, min(la, len(model.lines) - 1))
        lf = max(0, min(lf, len(model.lines) - 1))
        if la == lf:
            if ia == iff:
                return []
            return [(model.lines[la], min(ia, iff), max(ia, iff))]
        segs = []
        if la < lf:
            segs.append((la, ia, len(model.lines[la].glyphs)))
            for l in range(la + 1, lf):
                segs.append((l, 0, len(model.lines[l].glyphs)))
            segs.append((lf, 0, iff))
        else:
            segs.append((la, 0, ia))
            for l in range(la - 1, lf, -1):
                segs.append((l, 0, len(model.lines[l].glyphs)))
            segs.append((lf, iff, len(model.lines[lf].glyphs)))
        return [(model.lines[l], s, e) for (l, s, e) in segs]

    def selection_text(self):
        segs = self.selection_segments()
        if not segs:
            return ""
        return "\n".join("".join(g.char for g in line.glyphs[s:e])
                         for (line, s, e) in segs)

    def move_cursor(self, delta, word=False, extend=False):
        model = self._model()
        if model is None or self.cursor is None:
            return
        li, gi = self.cursor
        if not (0 <= li < len(model.lines)):
            return
        line = model.lines[li]
        if word:
            ngi = word_step(line, gi, delta > 0)
        else:
            ngi = gi + delta
        ngi = max(0, min(ngi, len(line.glyphs)))
        self.set_cursor(li, ngi, extend)

    def move_cursor_vertical(self, dy, extend=False):
        model = self._model()
        if model is None or self.cursor is None or not model.lines:
            return
        li, gi = self.cursor
        nl = max(0, min(li + dy, len(model.lines) - 1))
        x = boundary_x(model.lines[li], gi)
        ngi = cursor_index_at(model.lines[nl], x)
        self.set_cursor(nl, ngi, extend)

    def move_cursor_edge(self, head, page=False, extend=False):
        model = self._model()
        if model is None or self.cursor is None or not model.lines:
            return
        li, gi = self.cursor
        if page:
            li = 0 if head else len(model.lines) - 1
            gi = 0 if head else len(model.lines[li].glyphs)
        else:
            line = model.lines[li]
            gi = 0 if head else len(line.glyphs)
        self.set_cursor(li, gi, extend)

    def current_style(self):
        model = self._model()
        if model is None:
            return None
        segs = self.selection_segments()
        if segs:
            line, s, _e = segs[0]
            if line.glyphs:
                return inherited_style(line, s)
        if self.cursor is not None:
            li, gi = self.cursor
            if 0 <= li < len(model.lines):
                return inherited_style(model.lines[li], gi)
        return None

    # ================================================== 文本编辑
    def _editable(self):
        if self.doc is None:
            self.status_hint("请先打开 PDF 文件")
            return False
        if not self._can_modify:
            self.status_hint("文档权限禁止修改（受保护 PDF）")
            return False
        return True

    def _editable_page(self):
        if not self._editable():
            return False
        model = self._model()
        if model is not None and model.rotation % 360 != 0:
            self.status_hint("旋转页面编辑可能产生偏移，请谨慎操作")
        return True

    def insert_text(self, text):
        if not self._editable_page():
            return
        text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
        if not text:
            return
        if self.selection:
            self._replace_selection(text)
            return
        if "\n" in text:
            self._paste_multiline(text)
            return
        if self.cursor is None:
            self.status_hint("请先在页面文本处单击以定位光标")
            return
        model = self._model()
        li, gi = self.cursor
        line = model.lines[li]
        rb = executor.make_rebuild(self.page_no, line, gi, gi, text,
                                   self._overflow_strategy)
        self._push_text_cmd("输入文本", [rb],
                            (li, gi, line.bbox),
                            (line.bbox, gi + n_grapheme_clusters(text)))

    def backspace(self):
        if not self._editable_page():
            return
        if self.selection:
            self._delete_selection()
            return
        if self.cursor is None:
            return
        model = self._model()
        li, gi = self.cursor
        line = model.lines[li]
        if gi <= 0:
            self.status_hint("行首退格需要段落重排（第二阶段），请在行内删除")
            return
        rb = executor.make_rebuild(self.page_no, line, gi - 1, gi, "",
                                   self._overflow_strategy)
        self._push_text_cmd("删除文本", [rb], (li, gi, line.bbox), (line.bbox, gi - 1))

    def delete_forward(self):
        if not self._editable_page():
            return
        if self.selection:
            self._delete_selection()
            return
        if self.cursor is None:
            return
        model = self._model()
        li, gi = self.cursor
        line = model.lines[li]
        if gi >= len(line.glyphs):
            self.status_hint("行尾删除需要段落重排（第二阶段）")
            return
        rb = executor.make_rebuild(self.page_no, line, gi, gi + 1, "",
                                   self._overflow_strategy)
        self._push_text_cmd("删除文本", [rb], (li, gi, line.bbox), (line.bbox, gi))

    def _delete_selection(self):
        segs = self.selection_segments()
        if not segs:
            return
        rbs = []
        if len(segs) == 1:
            line, s, e = segs[0]
            rbs.append(executor.make_rebuild(self.page_no, line, s, e, "",
                                             self._overflow_strategy))
        else:
            first_line, s, _ = segs[0]
            rbs.append(executor.make_rebuild(self.page_no, first_line, s,
                                             len(first_line.glyphs), "",
                                             self._overflow_strategy))
            for line, _s, _e in segs[1:-1]:
                rbs.append(executor.make_rebuild(self.page_no, line, 0,
                                                 len(line.glyphs), "",
                                                 self._overflow_strategy))
            last_line, _s2, e2 = segs[-1]
            rbs.append(executor.make_rebuild(self.page_no, last_line, 0, e2, "",
                                             self._overflow_strategy))
        line0, s0, _e0 = segs[0]
        self._push_text_cmd("删除文本", rbs,
                            (line0.index, s0, line0.bbox),
                            (line0.bbox, s0))

    def _replace_selection(self, text):
        segs = self.selection_segments()
        if not segs:
            return
        rbs = []
        for k, (line, s, e) in enumerate(segs):
            rbs.append(executor.make_rebuild(self.page_no, line, s, e,
                                             text if k == 0 else "",
                                             self._overflow_strategy))
        self._push_text_cmd("替换文本", rbs,
                            (segs[0][0].index, segs[0][1], segs[0][0].bbox),
                            (segs[0][0].bbox, segs[0][1] + n_grapheme_clusters(text)))

    def copy_selection(self):
        t = self.selection_text()
        if t:
            QApplication.clipboard().setText(t)
            self.status_hint("已复制到剪贴板")

    def cut_selection(self):
        if not self._editable_page():
            return
        if self.selection_text():
            QApplication.clipboard().setText(self.selection_text())
            self._delete_selection()

    def paste(self):
        if not self._editable_page():
            return
        text = QApplication.clipboard().text()
        if not text:
            return
        if "\n" in text:
            self._paste_multiline(text)
        elif self.selection is not None:
            self._replace_selection(text)
        else:
            self.insert_text(text)

    def _paste_multiline(self, text):
        model = self._model()
        if model is None or self.cursor is None:
            self.status_hint("请先在页面文本处单击以定位光标")
            return
        lines = text.split("\n")
        rbs = []
        if self.selection is not None:
            segs = self.selection_segments()
            for k, (line, s, e) in enumerate(segs):
                rbs.append(executor.make_rebuild(self.page_no, line, s, e,
                                                 lines[0] if k == 0 else "",
                                                 self._overflow_strategy))
            base = segs[-1][0]
            cur_after = (segs[0][0].bbox, segs[0][1] + n_grapheme_clusters(lines[0]))
        else:
            li, gi = self.cursor
            line = model.lines[li]
            rbs.append(executor.make_rebuild(self.page_no, line, gi, gi,
                                             lines[0], self._overflow_strategy))
            base = line
            cur_after = (line.bbox, gi + n_grapheme_clusters(lines[0]))
        for extra in lines[1:]:
            nxt = base.index + 1
            if nxt < len(model.lines):
                line2 = model.lines[nxt]
                rbs.append(executor.make_rebuild(
                    self.page_no, line2, len(line2.glyphs), len(line2.glyphs),
                    extra, self._overflow_strategy))
                base = line2
            else:
                rbs[0].new_text += " " + extra
                cur_after = (cur_after[0], cur_after[1] + 1 + n_grapheme_clusters(extra))
        li0, gi0 = self.cursor
        self._push_text_cmd("粘贴文本", rbs,
                            (li0, gi0, model.lines[li0].bbox), cur_after)
        self.status_hint("多行粘贴：已按行原位追加（段落重排属第二阶段）")

    def apply_style_to_selection(self, size, color):
        if not self._editable_page():
            return
        segs = self.selection_segments()
        if not segs:
            self.status_hint("请先选择要修改样式的文本")
            return
        rbs = []
        for line, s, e in segs:
            rb = executor.make_rebuild(self.page_no, line, s, e, "",
                                       self._overflow_strategy)
            rb.keep_positions = True
            rb.style_delta = {"size": size, "color": color}
            rbs.append(rb)
        cmd = TextStyleCommand(self.page_no, rbs)
        cmd.edit_rects = [rb.redact_rect for rb in rbs]
        cmd.cursor_after = (segs[0][0].bbox, segs[0][1])
        self._exec_cmd(cmd)

    def _push_text_cmd(self, title, rebuilds, cursor_before, cursor_after):
        cmd = TextEditCommand(title, self.page_no, rebuilds,
                              cursor_before, cursor_after)
        cmd.edit_rects = [rb.redact_rect for rb in rebuilds]
        self._exec_cmd(cmd)

    # ================================================== 图片编辑
    def image_at(self, x, y):
        model = self._model()
        if model is None:
            return None
        for img in reversed(model.images):
            if img.rect[0] <= x <= img.rect[2] and img.rect[1] <= y <= img.rect[3]:
                return img
        return None

    def selected_image(self):
        return self._sel_image

    def select_image(self, img):
        self._sel_image = img
        self.canvas._refresh_handles()
        self.status_hint(f"已选择图片（可拖动/缩放/旋转；Delete 删除）")

    def deselect_image(self):
        self._sel_image = None
        self.canvas._refresh_handles()

    def commit_image_transform(self, d):
        if not self._editable_page() or self._sel_image is None:
            return
        img = self._sel_image
        blob = executor.image_blob(self.doc, img.xref)
        if not blob:
            self.status_hint("无法提取图片数据")
            return
        before = ImageState(tuple(img.rect), d.get("old_deg", 0.0), blob)
        after = ImageState(tuple(d["new_rect"]), d.get("new_deg", 0.0), blob)
        cmd = ImageTransformCommand(d.get("title", "调整图片"),
                                    self.page_no, before, after)
        cmd.edit_rects = [tuple(img.rect), tuple(d["new_rect"])]
        self._exec_cmd(cmd, reselect_rect=d["new_rect"])

    def delete_selected_image(self):
        if not self._editable_page() or self._sel_image is None:
            return
        img = self._sel_image
        blob = executor.image_blob(self.doc, img.xref)
        cmd = ImageDeleteCommand(self.page_no,
                                 ImageState(tuple(img.rect), img.deg, blob))
        cmd.edit_rects = [tuple(img.rect)]
        self._exec_cmd(cmd)
        self.status_hint("图片已删除（内容流移除，可撤销）")

    def replace_selected_image(self):
        if not self._editable_page() or self._sel_image is None:
            return
        img = self._sel_image
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
        cmd.edit_rects = [tuple(img.rect)]
        self._exec_cmd(cmd, reselect_rect=tuple(img.rect))
        self.status_hint("图片内容已替换（位置与变换保持不变）")

    # ================================================== 命令执行与刷新
    def _exec_cmd(self, cmd, reselect_rect=None):
        results = self.undo_stack.push(cmd, self.ctx)
        self._register_results(cmd, results)
        self._refresh_page(cmd)
        if reselect_rect is not None:
            model = self._model()
            best, best_v = None, 0.0
            for im in (model.images if model else []):
                ix0, iy0 = max(im.rect[0], reselect_rect[0]), max(im.rect[1], reselect_rect[1])
                ix1, iy1 = min(im.rect[2], reselect_rect[2]), min(im.rect[3], reselect_rect[3])
                if ix1 > ix0 and iy1 > iy0:
                    inter = (ix1 - ix0) * (iy1 - iy0)
                    a1 = (im.rect[2] - im.rect[0]) * (im.rect[3] - im.rect[1])
                    a2 = (reselect_rect[2] - reselect_rect[0]) * (reselect_rect[3] - reselect_rect[1])
                    v = inter / max(a1 + a2 - inter, 1e-6)
                    if v > best_v:
                        best_v, best = v, im
            if best is not None and best_v > 0.7:
                self._sel_image = best
                self.canvas._refresh_handles()
        self._update_panels()

    def _register_results(self, cmd, results):
        fid = self.fidelities.setdefault(self.page_no, PageFidelity())
        model = self._model()
        if model is not None and model.scanned:
            fid.level = "red"
            if model.scanned_note not in fid.reasons:
                fid.reasons.append(model.scanned_note)
        if results:
            fid.update(results)
        for r in getattr(cmd, "edit_rects", []) or []:
            self.edit_regions.setdefault(self.page_no, []).append(tuple(r))

    def _refresh_page(self, cmd=None):
        if self.doc is None:
            return
        page = self.doc[self.page_no]
        model = extract_page(page, self.page_no)
        self.models[self.page_no] = model
        self.selection = None
        spec = getattr(cmd, "cursor_after", None) if cmd is not None else None
        if spec is not None:
            bbox, gi = spec
            li = find_line_by_bbox(model, bbox)
            if li >= 0:
                self.cursor = (li, max(0, min(gi, len(model.lines[li].glyphs))))
        else:
            self.cursor = None
        pm = self._render_qpixmap(page)
        self.canvas.zoom = self.zoom
        self.canvas.set_page_content(pm, model, model.rect[2], model.rect[3])
        self.canvas.apply_zoom()
        self.canvas.update_cursor_item()
        self.canvas.update_selection_items()
        if self._sel_image is None:
            self.canvas._refresh_handles()
        self._update_thumb(self.page_no)

    def undo(self):
        if self.doc is None:
            return
        r = self.undo_stack.undo(self.ctx)
        if not r:
            return
        cmd, results = r
        self._register_results(cmd, results)
        self._refresh_page()
        cb = getattr(cmd, "cursor_before", None)
        if cb is not None:
            li0, gi0, bbox = cb
            model = self._model()
            li = find_line_by_bbox(model, bbox)
            if li >= 0:
                self.cursor = (li, max(0, min(gi0, len(model.lines[li].glyphs))))
                self.canvas.update_cursor_item()
        self._update_panels()

    def redo(self):
        if self.doc is None:
            return
        r = self.undo_stack.redo(self.ctx)
        if not r:
            return
        cmd, results = r
        self._register_results(cmd, results)
        self._refresh_page(cmd)
        self._update_panels()

    # ================================================== 保存与验证
    def save(self):
        if self.doc is None:
            return
        self._do_save(self.doc_path)

    def save_as(self):
        if self.doc is None:
            return
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
        """导出字体子集化优化副本（不影响当前可撤销文档）。"""
        if self.doc is None:
            return
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
        """对当前状态做临时保存 + 验证（不写盘）。"""
        if self.doc is None:
            return
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
        if self.doc is None:
            return
        page = self.doc[self.page_no]
        pm = self._render_qpixmap(page)
        self.canvas.zoom = z
        self.canvas.set_page_content(pm, self._model(),
                                     page.rect.width, page.rect.height)
        self.canvas.apply_zoom()

    def fit_width(self):
        if self.doc is None:
            return
        page = self.doc[self.page_no]
        z = (self.canvas.viewport().width() - 20) / max(page.rect.width, 1)
        self.set_zoom(z)

    def _on_hover(self, x, y):
        self.lb_pos.setText(f"({x:.1f}, {y:.1f})")

    # ================================================== 状态与面板
    def _set_overflow(self, s):
        self._overflow_strategy = s

    def status_hint(self, msg):
        self.statusBar().showMessage(msg, 6000)

    def _update_canvas_overlays(self):
        self.canvas.update_cursor_item()
        self.canvas.update_selection_items()
        self._update_sel_stats()
        self._update_panels()

    def _update_sel_stats(self):
        segs = self.selection_segments()
        n_chars = sum(e - s for (_l, s, e) in segs)
        self.panel.update_selection(n_chars, len(segs))

    def _update_panels(self):
        self.panel.update_style(self.current_style())
        fid = self.fidelities.get(self.page_no)
        if fid is not None:
            self.lb_fid.setText(LABELS.get(fid.level, fid.level))
            self.lb_fid.setStyleSheet(
                f"color:#fff; background:{COLORS.get(fid.level, '#888')};"
                "border-radius:3px; padding:2 8px;")
            self.panel.update_fidelity(fid.level, fid.reasons)
        else:
            self.lb_fid.setText("未打开")

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
    def make_sample(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "示例文档.pdf")
        create_sample_pdf(path)
        self.open_file(path)

    def show_fidelity_help(self):
        QMessageBox.information(
            self, "保真等级说明",
            "绿色 原生编辑：原字体、原布局完整保留。\n\n"
            "黄色 局部重建：相同或近似字体重建，可能有细微差异（如字号适配、字体替代）。\n\n"
            "橙色 覆盖编辑：文本超出原区域或覆盖实现。\n\n"
            "红色 图像/OCR 编辑：页面为扫描件图像。\n\n"
            "文本溢出时可选：自动缩小字号 / 保持字号并提示。")

    def show_about(self):
        QMessageBox.about(
            self, "关于",
            "<b>PDF 无感编辑器（第一阶段 MVP）</b><br><br>"
            "架构：PDF 解析重建 + 视觉编辑层 + 局部内容流重写。<br>"
            "编辑直接修改内容流（真删除，非遮盖），保存后自动执行"
            "结构校验与视觉回归验证。<br><br>"
            "已支持：字符级提取、光标/选区/双击选词、输入法、原样式继承、"
            "原位文本编辑、图片移动/缩放/旋转/替换/删除、撤销重做、字体嵌入、"
            "保真等级指示。<br><br>"
            "第二阶段（段落重排、表格语义、竖排/BiDi）与第三阶段（扫描件 OCR 编辑）"
            "见技术方案路线图。")

    def closeEvent(self, e):
        if self.doc is not None and self.undo_stack.can_undo:
            ret = QMessageBox.question(
                self, "未保存的修改",
                "存在未保存的修改，确定退出吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ret != QMessageBox.StandardButton.Yes:
                e.ignore()
                return
        super().closeEvent(e)
