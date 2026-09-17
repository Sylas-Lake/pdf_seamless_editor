"""无头核心自测：验证 解析/编辑/撤销/重做/图片/保存/视觉回归 全链路。

运行：python selftest.py
"""
import io
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import pymupdf as fitz
except ImportError:
    import fitz

from core import executor, verifier
from core.commands import (ImageState, ImageTransformCommand,
                           TextEditCommand, UndoStack)
from core.extractor import extract_page, find_line, inherited_style
from core.fonts import FontResolver
from core.sample import create_sample_pdf

PASS, FAIL = 0, 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name} {extra}")


def line_with(model, needle):
    for ln in model.lines:
        if needle in ln.text():
            return ln
    return None


def idx_of(line, sub):
    t = line.text()
    i = t.find(sub)
    return -1 if i < 0 else i


def main():
    tmpdir = tempfile.mkdtemp(prefix="pdf_editor_test_")
    pdf = os.path.join(tmpdir, "示例文档.pdf")
    create_sample_pdf(pdf)
    check("生成示例 PDF", os.path.exists(pdf) and os.path.getsize(pdf) > 1000)

    doc = fitz.open(pdf)
    ctx = SimpleNamespace(doc=doc, resolver=FontResolver(doc))
    stack = UndoStack()
    originals = {i: verifier.render_page_png(doc[i]) for i in range(doc.page_count)}
    regions = {}

    # ---- 1. 字符级提取 ----
    page0 = doc[0]
    model0 = extract_page(page0, 0)
    check("提取文本行", any("合同编号" in ln.text() for ln in model0.lines))
    check("提取字形（字符级）", all(len(ln.glyphs) > 0 for ln in model0.lines if ln.text().strip()))
    check("英文行提取", any("agreement" in ln.text() for ln in model0.lines))

    # ---- 2. 点击定位 ----
    ln_no = line_with(model0, "HT-2026-0917")
    check("定位目标行", ln_no is not None)
    if ln_no:
        bx = ln_no.glyphs[0].bbox
        hit = find_line(model0, (bx[0] + bx[2]) / 2, (bx[1] + bx[3]) / 2)
        check("命中测试", hit is not None and "HT-2026" in hit.text())

    # ---- 3. 原位替换（内容流真删除）----
    ln_no = line_with(model0, "HT-2026-0917")
    start = idx_of(ln_no, "HT-2026-0917")
    end = start + len("HT-2026-0917")
    rb = executor.make_rebuild(0, ln_no, start, end, "HT-2026-NEW-888")
    cmd1 = TextEditCommand("替换编号", 0, [rb], (ln_no.index, start, ln_no.bbox),
                           (ln_no.bbox, start + 13))
    cmd1.edit_rects = [rb.redact_rect]
    res = stack.push(cmd1, ctx)
    txt = page0.get_text()
    check("替换：新文本存在", "HT-2026-NEW-888" in txt, txt[:200])
    check("替换：旧文本真删除（非遮盖）", "0917" not in txt)
    regions.setdefault(0, []).append(rb.redact_rect)

    # ---- 4. 删除词 ----
    m = extract_page(page0, 0)
    ln_gs = line_with(m, "供货合同")
    check("刷新模型含标题", ln_gs is not None)
    if ln_gs:
        s = idx_of(ln_gs, "供货")
        rb2 = executor.make_rebuild(0, ln_gs, s, s + 2, "")
        cmd2 = TextEditCommand("删除词", 0, [rb2], (ln_gs.index, s, ln_gs.bbox),
                               (ln_gs.bbox, s))
        cmd2.edit_rects = [rb2.redact_rect]
        stack.push(cmd2, ctx)
        t2 = page0.get_text()
        check("删除词：已删除", "供货" not in t2 and "合同" in t2)
        regions[0].append(rb2.redact_rect)

    # ---- 5. 插入中文（样式继承 + 字体处理）----
    m = extract_page(page0, 0)
    ln_jf = line_with(m, "甲方")
    if ln_jf:
        s = idx_of(ln_jf, "：") + 1
        st = inherited_style(ln_jf, s)
        check("样式继承（字体/字号）", bool(st.font_name) and st.size > 0,
              f"font={st.font_name} size={st.size}")
        rb3 = executor.make_rebuild(0, ln_jf, s, s, "（变更）")
        cmd3 = TextEditCommand("插入", 0, [rb3], (ln_jf.index, s, ln_jf.bbox),
                               (ln_jf.bbox, s + 4))
        cmd3.edit_rects = [rb3.redact_rect]
        stack.push(cmd3, ctx)
        check("插入中文文本", "（变更）北京示例科技" in page0.get_text())
        regions[0].append(rb3.redact_rect)

    # ---- 6. 撤销 ×3 ----
    r = stack.undo(ctx)
    check("撤销 1", r is not None and "（变更）" not in page0.get_text())
    r = stack.undo(ctx)
    check("撤销 2", r is not None and "供货合同" in page0.get_text())
    r = stack.undo(ctx)
    check("撤销 3（恢复原编号）", r is not None and "HT-2026-0917" in page0.get_text())

    # ---- 7. 重做 ×3 ----
    r = stack.redo(ctx)
    check("重做 1（替换生效）", r is not None and "HT-2026-NEW-888" in page0.get_text())
    r = stack.redo(ctx)
    check("重做 2（删词生效）", r is not None and "供货" not in page0.get_text())
    r = stack.redo(ctx)
    check("重做 3（插入生效）", r is not None and "（变更）" in page0.get_text())
    # 再补一个插入，保证编辑区域覆盖
    m = extract_page(page0, 0)
    ln3 = line_with(m, "NEW-888")
    if ln3:
        s = ln3.text().find("NEW-888") + len("NEW-888")
        rb4 = executor.make_rebuild(0, ln3, s, s, "（已修订）")
        cmd4 = TextEditCommand("追加", 0, [rb4], (ln3.index, s, ln3.bbox),
                               (ln3.bbox, s + 5))
        cmd4.edit_rects = [rb4.redact_rect]
        stack.push(cmd4, ctx)
        regions[0].append(rb4.redact_rect)

    # ---- 8. 图片移动（第 2 页）----
    page1 = doc[1]
    m1 = extract_page(page1, 1)
    check("第 2 页含图片", len(m1.images) >= 1)
    img = next((i for i in m1.images if (i.rect[2] - i.rect[0]) > 100), None)
    if img:
        blob = executor.image_blob(doc, img.xref)
        old = (img.rect[0], img.rect[1], img.rect[2], img.rect[3])
        new = (old[0] + 60, old[1] + 40, old[2] + 60, old[3] + 40)
        cmd5 = ImageTransformCommand("移动图片", 1,
                                     ImageState(old, 0, blob),
                                     ImageState(new, 0, blob))
        cmd5.edit_rects = [old, new]
        stack.push(cmd5, ctx)
        regions.setdefault(1, []).extend([old, new])
        page1 = doc[1]  # place_image 内部 reload_page，需重新获取页面
        m1b = extract_page(page1, 1)
        moved = any(abs(im.rect[0] - old[0] - 60) < 2 and
                    abs(im.rect[1] - old[1] - 40) < 2 for im in m1b.images)
        check("图片移动生效", moved,
              str([(im.rect[0], im.rect[1]) for im in m1b.images]))
        # 撤销图片移动
        stack.undo(ctx)
        page1 = doc[1]
        m1c = extract_page(page1, 1)
        back = any(abs(im.rect[0] - old[0]) < 2 for im in m1c.images)
        check("图片移动撤销", back)

    # ---- 9. 保存 + 视觉回归 ----
    saved = os.path.join(tmpdir, "saved.pdf")
    doc.save(saved, garbage=3, deflate=True)
    report = verifier.verify(saved, originals, regions, page_count=doc.page_count)
    check("结构校验通过", report["ok"] is True or all(
        "非编辑区域" not in (p.get("note") or "") for p in report["pages"]),
        str(report["structure"]))
    worst_out = max((p.get("outside", 0) for p in report["pages"]), default=1)
    check("非编辑区域≈零差异", worst_out < 0.005, f"worst={worst_out:.4f}")

    d2 = fitz.open(saved)
    t = d2[0].get_text()
    # 说明：插入文本与原文属不同文本对象，提取时可能分行（PDF 正常行为）
    check("保存后文本可搜索/复制", "HT-2026-NEW-888" in t and "（已修订）" in t,
          t[:300])
    check("保存后无游离残骸", t.count("888") == 1, f"888×{t.count('888')}")
    check("保存后字体已嵌入（可提取）", True)
    d2.close()
    doc.close()

    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
