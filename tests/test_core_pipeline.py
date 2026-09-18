"""核心全链路回归（无头）。"""
import os
import tempfile
from types import SimpleNamespace

from core.compat import fitz

from core import executor, verifier
from core.commands import PageStateCommand, UndoStack
from core.extractor import extract_page
from core.fonts import FontOracle, FontResolver
from core.sample import create_sample_pdf
from core.snapshot import capture_page_state, page_state_equal
from core.textbox import BoxBuffer

def check(name, cond, extra=""):
    assert cond, f"{name} {extra}".strip()


def block_with(model, needle):
    for b in model.blocks:
        if needle in b.text():
            return b
    return None


def edit_block(doc, page, block, mutate, title, resolver, page_index=0):
    """模拟一次会话提交：快照 → 清除 → 变换缓冲 → 插入 → 快照命令。"""
    before = capture_page_state(doc, page)
    executor.remove_text_region(page, _exp(block.bbox, 0.6))
    buffer = BoxBuffer(block)
    oracle = FontOracle(resolver, page)
    mutate(buffer)
    runs = buffer.commit_runs(oracle)
    executor.insert_runs(page, runs, resolver)
    after = capture_page_state(doc, page)
    cmd = PageStateCommand(title, page_index, before, after)
    cmd.edit_rects = [_exp(block.bbox, 1.0), buffer.bbox()]
    return cmd, buffer, runs


def _exp(r, m):
    return (r[0] - m, r[1] - m, r[2] + m, r[3] + m)


def test_core_pipeline():
    tmpdir = tempfile.mkdtemp(prefix="pdf_editor_test_")
    pdf = os.path.join(tmpdir, "示例文档.pdf")
    create_sample_pdf(pdf)
    doc = fitz.open(pdf)
    resolver = FontResolver(doc)
    ctx = SimpleNamespace(doc=doc, resolver=resolver)
    stack = UndoStack()
    originals = {i: verifier.render_page_png(doc[i]) for i in range(doc.page_count)}
    regions = {}
    page0 = doc[0]

    # ---- 1. 提取（文本框分组） ----
    model0 = extract_page(page0, 0)
    check("文本框分组", any("合同编号" in b.text() for b in model0.blocks))
    check("框内行结构", all(b.lines for b in model0.blocks if b.text().strip()))
    check("框命中测试", model0.block_at(100, 100) is not None)

    # ---- 2. BoxBuffer 编辑单元 ----
    blk = block_with(model0, "HT-2026-0917")
    check("定位目标框", blk is not None)
    if blk:
        buf = BoxBuffer(blk)
        check("缓冲文本一致", "HT-2026-0917" in buf.text())
        oracle = FontOracle(resolver, page0)
        # 插入：光标推进
        cur = (0, blk.lines[0].text().find("HT"))
        n_before = buf.text()
        cur2 = buf.insert(cur, "X")
        check("插入后光标推进", buf.text()[cur[1]:cur[1] + 1] == "X" and cur2 == (cur[0], cur[1] + 1))
        # 退格
        buf.backspace(cur2)
        check("退格恢复", buf.text() == n_before)
        # 换行
        buf.split_line((0, 3))
        check("硬换行", len(buf.hard_lines) == 2)
        # 命中测试往返
        adv = oracle.adv_fn()
        pos = buf.hit_test(buf.box_left + 5, buf.first_baseline, adv)
        check("缓冲命中", pos is not None)
        # advance 一致性（内置 CJK 全宽）
        st = blk.lines[0].glyphs[0].style
        a1 = oracle.advance(st, "合")
        a2 = oracle.advance(st, "A")
        check("CJK advance 全宽", abs(a1 - st.size) < 0.01)
        check("内置 CJK 字体下 ASCII 全宽", abs(a2 - st.size) < 0.01)
        # 自适应宽度：追加长文本后面框应变宽（不再卡在默认 wrap 宽）
        buf2 = BoxBuffer(blk)
        buf2.set_measure(oracle.adv_fn())
        w0 = buf2.bbox()[2] - buf2.bbox()[0]
        buf2.insert((0, len(buf2.hard_lines[0])), "——自适应宽度测试附加内容——")
        w1 = buf2.bbox()[2] - buf2.bbox()[0]
        check("文本框随内容变宽", w1 > w0 + 30, f"{w0:.1f}->{w1:.1f}")

    from core.geom import qrect_args, resize_rect
    qa = qrect_args((72, 120, 352, 300))
    check("QRect 参数是宽高不是 x1y1", abs(qa[2] - 280) < 0.01 and abs(qa[3] - 180) < 0.01)
    r0 = (10.0, 20.0, 50.0, 80.0)  # 40×60
    rn = resize_rect(r0, "n", 30, 5)
    check("上边缩放保持宽度", abs((rn[2] - rn[0]) - 40) < 1e-6 and abs(rn[1] - 5) < 1e-6)
    rs = resize_rect(r0, "s", 30, 110)
    check("下边缩放保持宽度", abs((rs[2] - rs[0]) - 40) < 1e-6 and abs(rs[3] - 110) < 1e-6)
    re = resize_rect(r0, "e", 90, 50)
    check("右边缩放保持高度", abs((re[3] - re[1]) - 60) < 1e-6 and abs(re[2] - 90) < 1e-6)
    rse = resize_rect(r0, "se", 90, 140, keep_aspect=True)
    check("右下角等比缩放", abs((rse[2] - rse[0]) / max(rse[3] - rse[1], 1e-6) - 40 / 60) < 0.02,
          f"{rse}")

    # ---- 3. 会话提交 + 字节级撤销（核心） ----
    blk = block_with(model0, "HT-2026-0917")
    page0 = doc[0]

    def mutate_replace(b: BoxBuffer):
        # 把 HT-2026-0917 换成 HT-2026-NEW-888
        t = b.hard_lines[0]
        s = "".join(c for c, _ in t)
        i = s.find("HT-2026-0917")
        del t[i:i + 12]
        st = t[i - 1][1] if i > 0 and t else blk.lines[0].glyphs[0].style
        for j, ch in enumerate("HT-2026-NEW-888"):
            t.insert(i + j, (ch, st.copy()))
        b.layout()

    cmd1, buf1, runs1 = edit_block(doc, page0, blk, mutate_replace,
                                   "替换编号", resolver)
    stack.push(cmd1, ctx)
    regions.setdefault(0, []).extend(cmd1.edit_rects)
    t = page0.get_text()
    check("替换生效（真删除）", "HT-2026-NEW-888" in t and "0917" not in t)

    # 撤销 → 字节级原版
    r = stack.undo(ctx)
    check("撤销命令", r is not None)
    page0 = doc[0]
    check("字节级恢复原版", page_state_equal(doc, page0, cmd1.before))
    check("恢复后文本", "HT-2026-0917" in page0.get_text())
    # 恢复后渲染与原始一致
    import numpy as np
    from PIL import Image
    import io as _io
    now = verifier.render_page_png(page0)
    a1 = np.asarray(Image.open(_io.BytesIO(originals[0])).convert("RGB"))
    a2 = np.asarray(Image.open(_io.BytesIO(now)).convert("RGB"))
    diff = np.abs(a1.astype(np.int16) - a2.astype(np.int16)).sum(axis=2)
    check("撤销后渲染零差异", (diff > 18).mean() < 0.0005,
          f"{(diff > 18).mean():.5f}")

    # 重做
    r = stack.redo(ctx)
    page0 = doc[0]
    check("重做生效", r is not None and "HT-2026-NEW-888" in page0.get_text())

    # ---- 4. 连续输入（模拟逐字符输入不堆叠） ----
    m2 = extract_page(page0, 0)
    blk2 = block_with(m2, "NEW-888")
    page0 = doc[0]

    def mutate_type(b: BoxBuffer):
        # 在编号后追加 "-REV"，模拟逐字符输入
        hl = b.hard_lines[0]
        s = "".join(c for c, _ in hl)
        i = s.find("NEW-888") + len("NEW-888")
        st = hl[i - 1][1]
        for ch in "-REV":
            hl.insert(i, (ch, st.copy()))
            i += 1
        b.layout()

    cmd2, _, _ = edit_block(doc, page0, blk2, mutate_type, "追加", resolver)
    stack.push(cmd2, ctx)
    regions[0].extend(cmd2.edit_rects)
    t2 = page0.get_text()
    check("追加文本存在", "NEW-888-REV" in t2 or ("NEW-888" in t2 and "REV" in t2))
    r = stack.undo(ctx)
    page0 = doc[0]
    check("二次撤销字节级恢复", r is not None and page_state_equal(doc, page0, cmd2.before))
    r = stack.redo(ctx)

    # ---- 5. 文本框移动（类 PPT 拖动） ----
    page0 = doc[0]
    m3 = extract_page(page0, 0)
    blk3 = block_with(m3, "供货合同") or m3.blocks[0]

    def mutate_move(b: BoxBuffer):
        b.translate(40, 25)

    cmd3, _, _ = edit_block(doc, page0, blk3, mutate_move, "移动文本框", resolver)
    stack.push(cmd3, ctx)
    regions[0].extend(cmd3.edit_rects)
    m3b = extract_page(page0, 0)
    # 移动后可能与相邻行在提取时合并为一块（视觉重叠），按字形位置断言
    glyph = None
    for b in m3b.blocks:
        for l in b.lines:
            if "供货合同" in l.text():
                glyph = l.glyphs[0]
                break
        if glyph:
            break
    check("文本框移动生效",
          glyph is not None and abs(glyph.origin[0] - (blk3.bbox[0] + 40)) < 2
          and abs(glyph.origin[1] - (blk3.lines[0].baseline + 25)) < 2,
          f"glyph={None if glyph is None else (round(glyph.origin[0], 1), round(glyph.origin[1], 1))}")
    r = stack.undo(ctx)
    page0 = doc[0]
    check("文本框移动撤销（字节级）", r is not None and
          page_state_equal(doc, page0, cmd3.before))

    # ---- 6. 图片移动 + 撤销 ----
    page1 = doc[1]
    m1 = extract_page(page1, 1)
    check("第 2 页含图片", len(m1.images) >= 1)
    img = next((i for i in m1.images if (i.rect[2] - i.rect[0]) > 100), None)
    if img:
        blob = executor.image_blob(doc, img.xref)
        old = tuple(img.rect)
        new = (old[0] + 60, old[1] + 40, old[2] + 60, old[3] + 40)
        before = capture_page_state(doc, page1)
        executor.place_image(doc, page1, [old], new, 0, blob)
        page1 = doc[1]  # place_image 内部 reload，重新获取
        after = capture_page_state(doc, page1)
        cmd5 = PageStateCommand("移动图片", 1, before, after)
        cmd5.edit_rects = [old, new]
        stack.push(cmd5, ctx)
        regions.setdefault(1, []).extend([old, new])
        page1 = doc[1]
        m1b = extract_page(page1, 1)
        moved = any(abs(im.rect[0] - old[0] - 60) < 2 for im in m1b.images)
        check("图片移动生效", moved)
        # 缩放所见即所得：拉伸为非原始宽高比
        m1c = extract_page(page1, 1)
        img2 = next((i for i in m1c.images if abs(i.rect[0] - old[0] - 60) < 2), None)
        if img2:
            r0 = tuple(img2.rect)
            stretched = (r0[0], r0[1], r0[0] + (r0[2] - r0[0]) * 1.5, r0[1] + (r0[3] - r0[1]) * 0.8)
            blob2 = executor.image_blob(doc, img2.xref)
            executor.place_image(doc, page1, [r0], stretched, 0, blob2)
            page1 = doc[1]
            m1d = extract_page(page1, 1)
            done = any(abs(im.rect[2] - im.rect[0] - (stretched[2] - stretched[0])) < 1
                       and abs(im.rect[3] - im.rect[1] - (stretched[3] - stretched[1])) < 1
                       for im in m1d.images)
            check("图片缩放所见即所得", done)
        r = stack.undo(ctx)
        page1 = doc[1]
        check("图片移动撤销（字节级）", r is not None and
              page_state_equal(doc, page1, cmd5.before))
        m1e = extract_page(page1, 1)
        back = any(abs(im.rect[0] - old[0]) < 2 for im in m1e.images)
        check("图片撤销后位置复原", back)

    # ---- 7. 保存 + 视觉回归 ----
    saved = os.path.join(tmpdir, "saved.pdf")
    doc.save(saved, garbage=3, deflate=True)
    report = verifier.verify(saved, originals, regions, page_count=doc.page_count)
    check("结构校验通过", report["ok"], str(report["structure"]))
    worst_out = max((p.get("outside", 0) for p in report["pages"]), default=1)
    check("非编辑区域≈零差异", worst_out < 0.005, f"worst={worst_out:.4f}")

    d2 = fitz.open(saved)
    t = d2[0].get_text()
    check("保存后文本可搜索", "NEW-888" in t and "（变更）" not in t[:50], t[:200])
    check("保存后无游离残骸", t.count("888") <= 2, f"888×{t.count('888')}")
    d2.close()
    doc.close()


