"""GUI 程序化冒烟测试：驱动真实窗口验证编辑全链路。

运行：python smoke_gui.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS, FAIL = 0, 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name} {extra}")


def main():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer

    from ui.main_window import MainWindow
    from core.extractor import extract_page

    app = QApplication(sys.argv)
    win = MainWindow()
    win.resize(1280, 860)
    win.show()
    app.processEvents()

    # 1. 打开示例
    win.make_sample()
    app.processEvents()
    check("打开文档", win.doc is not None and win.doc.page_count == 2)
    check("画布模型加载", win._model() is not None and len(win._model().lines) > 5)
    check("缩略图生成", win.thumb_list.count() == 2)
    check("页面渲染", win.canvas.pixmap_item is not None)
    check("保真初始为绿", win.fidelities[0].level == "green")
    check("属性面板初始占位", win.panel.ed_font.text() in ("", "—"))

    # 2. 光标定位与样式继承
    model = win._model()
    ln = next((l for l in model.lines if "HT-2026-0917" in l.text()), None)
    check("定位到目标行", ln is not None)
    if ln:
        li = ln.index
        gi = ln.text().find("HT-2026-0917")
        win.set_cursor(li, gi)
        check("光标状态", win.cursor == (li, gi))
        check("光标项显示", win.canvas.cursor_item.isVisible())
        st = win.current_style()
        check("样式继承显示", st is not None and st.size > 0)

    # 3. 输入文本（原位替换链路）
    if ln:
        li = win.cursor[0]
        gi = win.cursor[1]
        line = win._model().lines[li]
        win._push_text_cmd("输入测试", [
            __import__("core.executor", fromlist=["x"]).make_rebuild(
                win.page_no, line, gi, gi + 12, "HT-2026-EDIT-001")],
            (li, gi, line.bbox), (line.bbox, gi + 15))
        app.processEvents()
        t = win.doc[0].get_text()
        check("GUI 输入替换生效", "HT-2026-EDIT-001" in t)
        check("保真状态更新", win.fidelities[0].level in ("green", "yellow"))

    # 4. 撤销 / 重做
    win.undo()
    app.processEvents()
    check("GUI 撤销", "HT-2026-0917" in win.doc[0].get_text())
    win.redo()
    app.processEvents()
    check("GUI 重做", "HT-2026-EDIT-001" in win.doc[0].get_text())

    # 5. 选区 API
    model = win._model()
    ln = next((l for l in model.lines if "供货" in l.text()), None)
    if ln:
        win.set_cursor(ln.index, ln.text().find("供货"))
        win.extend_selection(ln.index, ln.text().find("供货") + 2)
        segs = win.selection_segments()
        check("选区段生成", len(segs) == 1 and segs[0][2] - segs[0][1] == 2)
        check("选区文本", win.selection_text().strip() == "供货")
        check("选区覆盖层", len(win.canvas.sel_items) >= 1)
        win.copy_selection()
        from PySide6.QtWidgets import QApplication as QA
        check("复制到剪贴板", QA.clipboard().text().strip() == "供货")

    # 6. 图片选择与缩略图刷新
    win.set_page(1)
    app.processEvents()
    model1 = win._model()
    check("第 2 页图片模型", len(model1.images) >= 1)
    if model1.images:
        img = model1.images[0]
        cx = (img.rect[0] + img.rect[2]) / 2
        cy = (img.rect[1] + img.rect[3]) / 2
        got = win.image_at(cx, cy)
        check("图片命中", got is not None)
        if got:
            win.select_image(got)
            check("图片手柄显示", win.canvas.handle_layer.isVisible())
            win.deselect_image()
            check("图片取消选择", not win.canvas.handle_layer.isVisible())

    # 7. 保存到临时文件 + 验证报告数据
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    try:
        win.doc.save(tmp, garbage=3, deflate=True)
        from core import verifier
        rep = verifier.verify(tmp, win.orig_renders, win.edit_regions,
                              page_count=win.doc.page_count)
        check("GUI 保存验证通过", rep["ok"] is True)
        worst = max((p.get("outside", 0) for p in rep["pages"]), default=1)
        check("GUI 非编辑区≈零差异", worst < 0.005, f"{worst:.4f}")
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass

    win.grab().save("smoke2.png")
    print(f"\nGUI 冒烟结果：{PASS} 通过 / {FAIL} 失败")
    win.close()
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
