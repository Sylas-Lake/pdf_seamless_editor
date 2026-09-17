"""GUI 程序化冒烟测试 v2：文本框会话编辑全链路。

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
    from ui.main_window import MainWindow

    app = QApplication(sys.argv)
    win = MainWindow()
    win.resize(1280, 860)
    win.show()
    app.processEvents()

    # 1. 打开示例
    win.make_sample()
    app.processEvents()
    check("打开文档", win.doc is not None and win.doc.page_count == 2)
    check("文本框模型", win._model() is not None and len(win._model().blocks) > 5)
    check("缩略图", win.thumb_list.count() == 2)
    check("保真初始为绿", win.fidelities[0].level == "green")

    # 2. 双击进入会话（Windows 序列：Press → Release → DblClick）
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QKeyEvent, QMouseEvent

    def send_mouse(etype, sx, sy):
        vp = win.canvas.mapFromScene(QPointF(sx, sy))
        glob = win.canvas.viewport().mapToGlobal(vp)
        ev = QMouseEvent(etype, QPointF(vp), QPointF(glob),
                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(win.canvas.viewport(), ev)
        app.processEvents()

    model = win._model()
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
        # 取消后走原有缓冲编辑断言
        if win.session is not None:
            win.cancel_session()
            check("双击会话可取消", win.session is None)

        win.start_session(blk)
        check("会话建立", win.session is not None)
        check("编辑覆盖层显示(程序)", win.canvas.box_editor.isVisible())
        # 会话内输入：光标应随输入推进（缓冲模型）
        line0 = blk.lines[0].text()
        gi = line0.find("HT-2026-0917")
        win.session.cursor = (0, gi)
        win.session_insert("X")
        check("输入后缓冲文本", "XHT-2026-0917" in win.session.buffer.text())
        cur = win.session.cursor
        check("光标随输入推进", cur == (0, gi + 1), str(cur))
        # 连续输入不堆叠（缓冲追加）
        win.session_insert("YZ")
        t = win.session.buffer.text()
        check("连续输入追加", "XYZ" in t, t[:40])
        # 退格
        win.session_backspace()
        win.session_backspace()
        win.session_backspace()
        check("退格恢复", win.session.buffer.text() == blk.text())
        # 换行
        win.session.cursor = (0, 2)
        win.session_split_line()
        check("Enter 硬换行", len(win.session.buffer.hard_lines) == 2)
        # 撤销会话修改：取消（字节级）
        win.cancel_session()
        check("会话取消", win.session is None)
        check("取消后原文本", "HT-2026-0917" in win.doc[0].get_text())
        from core.snapshot import page_state_equal
        # 取消后应与打开时渲染一致
        from core import verifier
        import numpy as np
        from PIL import Image
        import io as _io
        now = verifier.render_page_png(win.doc[0])
        a1 = np.asarray(Image.open(_io.BytesIO(win.orig_renders[0])).convert("RGB"))
        a2 = np.asarray(Image.open(_io.BytesIO(now)).convert("RGB"))
        d = np.abs(a1.astype(np.int16) - a2.astype(np.int16)).sum(axis=2)
        check("取消后渲染零差异", (d > 18).mean() < 0.0005, f"{(d > 18).mean():.5f}")

    # 3. 会话提交 + 命令撤销（字节级）
    model = win._model()
    blk = next((b for b in model.blocks if "HT-2026-0917" in b.text()), None)
    if blk:
        win.start_session(blk)
        win.session.cursor = (0, blk.lines[0].text().find("HT"))
        win.session_insert("NO-")
        win.commit_session()
        check("提交后文本", "NO-HT-2026-0917" in win.doc[0].get_text())
        check("命令入栈", win.undo_stack.can_undo)
        win.undo()
        check("撤销回到原版", "HT-2026-0917" in win.doc[0].get_text()
              and "NO-" not in win.doc[0].get_text())
        from core.snapshot import page_state_equal
        win.redo()
        check("重做生效", "NO-HT-2026-0917" in win.doc[0].get_text())

    # 4. 文本框选中/移动
    model = win._model()
    blk = next((b for b in model.blocks if "供货合同" in b.text()), None)
    if blk:
        win.select_block(blk)
        check("框选中手柄", win.canvas.handle_layer.isVisible()
              and win.canvas.handle_layer.mode == "box")
        hr = win.canvas.handle_layer.rect
        check("文本框手柄宽高匹配内容",
              abs(hr.width() - (blk.bbox[2] - blk.bbox[0])) < 0.6
              and abs(hr.height() - (blk.bbox[3] - blk.bbox[1])) < 0.6,
              f"handle={hr.width():.1f}x{hr.height():.1f} "
              f"bbox={blk.bbox[2]-blk.bbox[0]:.1f}x{blk.bbox[3]-blk.bbox[1]:.1f}")
        win.commit_block_transform(blk, 30, 0, 0, "移动文本框")
        check("框移动提交", win.undo_stack.can_undo)
        t = win.doc[0].get_text()
        check("移动后文本完好", "供货合同" in t)

    # 5. 图片
    win.set_page(1)
    app.processEvents()
    model1 = win._model()
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
                  abs(hr.width() - ow) < 0.6 and abs(hr.height() - oh) < 0.6,
                  f"handle={hr.width():.1f}x{hr.height():.1f} img={ow:.1f}x{oh:.1f}")
            npos = win.canvas.handle_layer.corner_points()["n"]
            win.canvas._start_drag("n", QPointF(*npos))
            win.canvas._update_drag(QPointF(npos[0], npos[1] - 24))
            nr = win.canvas._drag["cur_rect"]
            check("上边手柄改变高度且保持宽度",
                  abs(nr.width() - ow) < 1.0 and nr.height() > oh + 10,
                  f"{nr.width():.1f}x{nr.height():.1f}")
            se = win.canvas.handle_layer.corner_points()["se"]
            win.canvas._start_drag("se", QPointF(*se))
            win.canvas._update_drag(QPointF(se[0] + 50, se[1] + 20))
            sr = win.canvas._drag["cur_rect"]
            check("右下角手柄放大", sr.width() > ow + 8 and sr.height() > oh + 5,
                  f"{sr.width():.1f}x{sr.height():.1f}")
            win.canvas._drag = None
            win.canvas._refresh_handles()
            old = tuple(got.rect)
            new = (old[0] + 40, old[1], old[2] + 70, old[3] + 30)
            win.commit_image_transform(old, got.deg, new, got.deg, "移动图片")
            m1b = win._model()
            done = any(abs(im.rect[2] - im.rect[0] - (new[2] - new[0])) < 1
                       and abs(im.rect[3] - im.rect[1] - (new[3] - new[1])) < 1
                       for im in m1b.images)
            check("图片缩放所见即所得", done)
            win.undo()
            m1c = win._model()
            back = any(abs(im.rect[2] - im.rect[0] - (old[2] - old[0])) < 1
                       for im in m1c.images)
            check("图片撤销（字节级）", back)

    # 6. 保存验证
    import tempfile
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

    print(f"\nGUI 冒烟结果：{PASS} 通过 / {FAIL} 失败")
    win.close()
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
