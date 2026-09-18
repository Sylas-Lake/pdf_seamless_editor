"""GUI 冒烟：走窗口公开 API，不依赖下划线私有属性。"""
from __future__ import annotations

import io
import os
import tempfile

import pytest

pytestmark = pytest.mark.gui


def check(name, cond, extra=""):
    assert cond, f"{name} {extra}".strip()


@pytest.fixture
def window():
    from PySide6.QtWidgets import QApplication
    from ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    win.resize(1280, 860)
    win.show()
    app.processEvents()

    fd, sample_path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    from core.sample import create_sample_pdf
    create_sample_pdf(sample_path)
    win.open_file(sample_path)
    app.processEvents()
    yield app, win, sample_path
    if win.session is not None:
        win.cancel_session()
    win.undo_stack.clear()
    if win.doc is not None:
        try:
            win.doc.close()
        except Exception:
            pass
        win.doc = None
    win.close()
    try:
        os.remove(sample_path)
    except OSError:
        pass


def test_gui_smoke(window):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QKeyEvent, QMouseEvent
    from PySide6.QtWidgets import QApplication

    app, win, _sample = window

    check("打开文档", win.doc is not None and win.doc.page_count == 2)
    check("文本框模型", win.page_model() is not None and len(win.page_model().blocks) > 5)
    check("缩略图", win.thumb_list.count() == 2)
    check("保真初始为绿", win.fidelities[0].level == "green")

    bar_names = win.chrome.tool_action_names()
    check("图标栏无生成示例", "生成示例" not in bar_names)
    check("图标栏无剪切复制粘贴全选",
          not any(t in bar_names for t in ("剪切", "复制", "粘贴", "全选")))
    check("图标栏有粗体斜体", "粗体" in bar_names and "斜体" in bar_names)
    check("剪贴板快捷键仍在",
          "Ctrl+X" in win.act_cut.shortcut().toString().replace(" ", ""))

    win.act_thumbs.setChecked(True)
    app.processEvents()
    check("开目录后画布右移", win.canvas.x() == win.stage.LEFT_W)
    check("开目录后画布让位",
          win.canvas.width() == win.stage.width() - win.stage.LEFT_W)
    win.act_thumbs.setChecked(False)
    app.processEvents()
    win.act_props.setChecked(True)
    app.processEvents()
    check("开属性后画布让位",
          win.canvas.width() == win.stage.width() - win.stage.RIGHT_W)
    win.act_props.setChecked(False)
    app.processEvents()

    check("页边翻到下一页", win.canvas.try_turn_page(1, "top") and win.page_no == 1)
    check("页边翻回上一页", win.canvas.try_turn_page(-1, "bottom") and win.page_no == 0)

    def send_mouse(etype, sx, sy):
        vp = win.canvas.mapFromScene(QPointF(sx, sy))
        glob = win.canvas.viewport().mapToGlobal(vp)
        ev = QMouseEvent(etype, QPointF(vp), QPointF(glob),
                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(win.canvas.viewport(), ev)
        app.processEvents()

    model = win.page_model()
    blk = next((b for b in model.blocks if "HT-2026-0917" in b.text()), None)
    check("定位目标框", blk is not None)
    if blk:
        cx = (blk.bbox[0] + blk.bbox[2]) / 2
        cy = (blk.bbox[1] + blk.bbox[3]) / 2
        win.canvas.setFocus()
        send_mouse(QEvent.Type.MouseButtonPress, cx, cy)
        send_mouse(QEvent.Type.MouseButtonRelease, cx, cy)
        send_mouse(QEvent.Type.MouseButtonDblClick, cx, cy)
        send_mouse(QEvent.Type.MouseButtonRelease, cx, cy)
        check("双击进入会话", win.session is not None)
        check("编辑覆盖层显示", win.canvas.box_editor.isVisible())
        if win.session is not None:
            ke = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Z,
                           Qt.KeyboardModifier.NoModifier, "Z")
            win.canvas.keyPressEvent(ke)
            app.processEvents()
            check("双击后可输入", "Z" in win.session.buffer.text(),
                  win.session.buffer.text()[:40])
            bb = win.session.buffer.bbox()
            check("编辑框随文本有宽高",
                  (bb[2] - bb[0]) > 40 and (bb[3] - bb[1]) > 8, str(bb))
        if win.session is not None:
            win.cancel_session()
            check("双击会话可取消", win.session is None)

        win.start_session(blk)
        check("会话建立", win.session is not None)
        check("输入法已启用",
              bool(win.canvas.inputMethodQuery(Qt.InputMethodQuery.ImEnabled)))
        surr = win.canvas.inputMethodQuery(Qt.InputMethodQuery.ImSurroundingText)
        check("输入法周围文本", isinstance(surr, str) and len(surr) > 0, str(surr)[:40])
        from core.snapshot import page_state_equal
        from core import verifier
        import numpy as np
        from PIL import Image
        check("进入会话不写页",
              page_state_equal(win.doc, win.doc[win.page_no], win.session.before_state))
        now = verifier.render_page_png(win.doc[0])
        a1 = np.asarray(Image.open(io.BytesIO(win.orig_renders[0])).convert("RGB"))
        a2 = np.asarray(Image.open(io.BytesIO(now)).convert("RGB"))
        d0 = np.abs(a1.astype(np.int16) - a2.astype(np.int16)).sum(axis=2)
        check("进入会话渲染不变", (d0 > 18).mean() < 0.0005, f"{(d0 > 18).mean():.5f}")
        before = win.session.before_state
        win.commit_session()
        check("未改提交零写入", page_state_equal(win.doc, win.doc[0], before))
        win.start_session(blk)
        check("再次进入会话", win.session is not None)
        line0 = blk.lines[0].text()
        gi = line0.find("HT-2026-0917")
        win.session.cursor = (0, gi)
        win.session_insert("X")
        check("输入后缓冲文本", "XHT-2026-0917" in win.session.buffer.text())
        cur = win.session.cursor
        check("光标随输入推进", cur == (0, gi + 1), str(cur))
        win.session_insert("YZ")
        t = win.session.buffer.text()
        check("连续输入追加", "XYZ" in t, t[:40])
        win.session_backspace()
        win.session_backspace()
        win.session_backspace()
        check("退格恢复", win.session.buffer.text() == blk.text())
        win.session.cursor = (0, 2)
        win.session_split_line()
        check("Enter 硬换行", len(win.session.buffer.hard_lines) == 2)
        win.cancel_session()
        check("会话取消", win.session is None)
        check("取消后原文本", "HT-2026-0917" in win.doc[0].get_text())
        now = verifier.render_page_png(win.doc[0])
        a1 = np.asarray(Image.open(io.BytesIO(win.orig_renders[0])).convert("RGB"))
        a2 = np.asarray(Image.open(io.BytesIO(now)).convert("RGB"))
        d = np.abs(a1.astype(np.int16) - a2.astype(np.int16)).sum(axis=2)
        check("取消后渲染零差异", (d > 18).mean() < 0.0005, f"{(d > 18).mean():.5f}")

    model = win.page_model()
    blk = next((b for b in model.blocks if "HT-2026-0917" in b.text()), None)
    if blk:
        win.start_session(blk)
        win.session.cursor = (0, blk.lines[0].text().find("HT"))
        win.session_insert("NO-")
        from core.snapshot import capture_page_state
        after_preview = capture_page_state(win.doc, win.doc[0])
        win.commit_session()
        after_commit = capture_page_state(win.doc, win.doc[0])
        check("提交后文本", "NO-HT-2026-0917" in win.doc[0].get_text())
        check("预览即终态",
              after_preview["contents"] == after_commit["contents"]
              and after_preview["page_obj"] == after_commit["page_obj"])
        check("命令入栈", win.undo_stack.can_undo)
        win.undo()
        check("撤销回到原版", "HT-2026-0917" in win.doc[0].get_text()
              and "NO-" not in win.doc[0].get_text())
        win.redo()
        check("重做生效", "NO-HT-2026-0917" in win.doc[0].get_text())

    model = win.page_model()
    blk = next((b for b in model.blocks if "供货合同" in b.text()), None)
    if blk:
        win.select_block(blk)
        check("框选中手柄", win.canvas.handle_layer.isVisible()
              and win.canvas.handle_layer.mode == "box")
        hr = win.canvas.handle_layer.rect
        check("文本框手柄宽高匹配内容",
              abs(hr.width() - (blk.bbox[2] - blk.bbox[0])) < 0.6
              and abs(hr.height() - (blk.bbox[3] - blk.bbox[1])) < 0.6)
        win.commit_block_transform(blk, 30, 0, 0, "移动文本框")
        check("框移动提交", win.undo_stack.can_undo)
        check("移动后文本完好", "供货合同" in win.doc[0].get_text())

    win.set_page(1)
    app.processEvents()
    model1 = win.page_model()
    check("图片模型", len(model1.images) >= 1)
    if model1.images:
        img = model1.images[0]
        cx = (img.rect[0] + img.rect[2]) / 2
        cy = (img.rect[1] + img.rect[3]) / 2
        got = win.image_at(cx, cy)
        check("图片命中", got is not None)
        if got:
            win.select_image(got)
            check("图片手柄", win.canvas.handle_layer.isVisible()
                  and win.canvas.handle_layer.mode == "image")
            hr = win.canvas.handle_layer.rect
            ow, oh = got.rect[2] - got.rect[0], got.rect[3] - got.rect[1]
            check("图片手柄宽高匹配",
                  abs(hr.width() - ow) < 0.6 and abs(hr.height() - oh) < 0.6)
            npos = win.canvas.handle_layer.corner_points()["n"]
            win.canvas.begin_handle_drag("n", QPointF(*npos))
            win.canvas.update_handle_drag(QPointF(npos[0], npos[1] - 24))
            nr = win.canvas.drag_rect()
            check("上边手柄改变高度且保持宽度",
                  nr is not None and abs(nr.width() - ow) < 1.0 and nr.height() > oh + 10)
            se = win.canvas.handle_layer.corner_points()["se"]
            win.canvas.begin_handle_drag("se", QPointF(*se))
            win.canvas.update_handle_drag(QPointF(se[0] + 50, se[1] + 20))
            sr = win.canvas.drag_rect()
            check("右下角手柄放大",
                  sr is not None and sr.width() > ow + 8 and sr.height() > oh + 5)
            win.canvas.cancel_handle_drag()
            old = tuple(got.rect)
            new = (old[0] + 40, old[1], old[2] + 70, old[3] + 30)
            win.commit_image_transform(old, got.deg, new, got.deg, "移动图片")
            m1b = win.page_model()
            done = any(abs(im.rect[2] - im.rect[0] - (new[2] - new[0])) < 1
                       and abs(im.rect[3] - im.rect[1] - (new[3] - new[1])) < 1
                       for im in m1b.images)
            check("图片缩放所见即所得", done)
            win.undo()
            m1c = win.page_model()
            back = any(abs(im.rect[2] - im.rect[0] - (old[2] - old[0])) < 1
                       for im in m1c.images)
            check("图片撤销（字节级）", back)

    win.status_hint("溢出策略已切换")
    check("提示条可见", win.canvas.hint_text() == "溢出策略已切换")
    win.set_overflow_strategy("keep")
    check("溢出策略 keep", win.overflow_strategy() == "keep")
    win.set_overflow_strategy("shrink")
    check("溢出策略 shrink", win.overflow_strategy() == "shrink")
    win.set_page(0)
    app.processEvents()
    model = win.page_model()
    blk = next((b for b in model.blocks if "HT-2026-0917" in b.text()), None)
    check("样式测试定位文本框", blk is not None)
    if blk:
        win.start_session(blk)
        win.apply_session_style(20.0, (0.1, 0.2, 0.3))
        st = win.current_style()
        check("会话应用字号", st is not None and abs(st.size - 20.0) < 0.05)
        check("应用样式后缓冲已改", win.session.buffer.changed)
        win.apply_emphasis(bold=True)
        st = win.current_style()
        check("会话可加粗", st is not None and st.is_bold)
        win.apply_emphasis(italic=True)
        st = win.current_style()
        check("会话可倾斜", st is not None and st.is_italic)
        win.cancel_session()
        check("样式取消恢复", win.session is None)

    fd, tmp = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    try:
        win.doc.save(tmp, garbage=3, deflate=True)
        from core import verifier
        rep = verifier.verify(tmp, win.orig_renders, win.edit_regions,
                              page_count=win.doc.page_count)
        check("保存验证通过", rep["ok"], str(rep["structure"])[:200])
        worst = max((p.get("outside", 0) for p in rep["pages"]), default=1)
        check("非编辑区≈零差异", worst < 0.005, f"{worst:.4f}")
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _one_page_pdf(text: str) -> str:
    from core.compat import fitz

    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 120), text, fontname="china-s", fontsize=16)
    doc.save(path)
    doc.close()
    return path


def test_empty_canvas_and_open_second_pdf():
    from PySide6.QtCore import QMimeData, QPointF, QUrl, Qt
    from PySide6.QtGui import QDropEvent
    from PySide6.QtWidgets import QApplication
    from ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    win.resize(960, 640)
    win.show()
    app.processEvents()
    check("空白工作台", win.doc is None)
    check("空白提示可见", win.canvas.empty_prompt_visible())

    p1 = _one_page_pdf("文档甲甲甲专属标记")
    p2 = _one_page_pdf("文档乙乙乙另一份")
    try:
        win.open_file(p1)
        app.processEvents()
        check("第一份已打开", win.doc is not None)
        check("画布是第一份",
              any("甲甲甲" in b.text() for b in win.page_model().blocks))
        check("打开后提示隐藏", not win.canvas.empty_prompt_visible())
        title1 = win.windowTitle()
        check("标题含第一份文件名", os.path.basename(p1) in title1)

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(os.path.abspath(p2))])
        ev = QDropEvent(QPointF(80, 80), Qt.DropAction.CopyAction, mime,
                        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        win.canvas.dropEvent(ev)
        app.processEvents()
        check("拖放打开第二份", win.doc is not None)
        check("标题已换成第二份", os.path.basename(p2) in win.windowTitle())
        check("画布已换成第二份",
              any("乙乙乙" in b.text() for b in win.page_model().blocks))
        check("画布不再是第一份",
              not any("甲甲甲" in b.text() for b in win.page_model().blocks))
        check("画布模型对应当前页", win.canvas.model is win.page_model())
        check("目录页数为一", win.thumb_list.count() == 1)
    finally:
        if win.session is not None:
            win.cancel_session()
        win.undo_stack.clear()
        if win.doc is not None:
            try:
                win.doc.close()
            except Exception:
                pass
            win.doc = None
        win.close()
        for p in (p1, p2):
            try:
                os.remove(p)
            except OSError:
                pass

