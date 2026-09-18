"""无感编辑：进入会话不写页、预览即终态、origin 保真。"""
import io
import os
import tempfile

import numpy as np
from PIL import Image

from core.compat import fitz
from core import executor, verifier
from core.extractor import extract_page
from core.fonts import FontOracle, FontResolver
from core.sample import create_sample_pdf
from core.snapshot import capture_page_state, page_state_equal
from core.textbox import BoxBuffer


def _png(arr_bytes):
    return np.asarray(Image.open(io.BytesIO(arr_bytes)).convert("RGB"))


def _exp(r, m):
    return (r[0] - m, r[1] - m, r[2] + m, r[3] + m)


def _sample_doc():
    tmpdir = tempfile.mkdtemp(prefix="pdf_seamless_")
    pdf = os.path.join(tmpdir, "示例文档.pdf")
    create_sample_pdf(pdf)
    doc = fitz.open(pdf)
    return doc, FontResolver(doc)


def _block(model, needle):
    for b in model.blocks:
        if needle in b.text():
            return b
    return None


def _expand_runs(runs, oracle):
    got = []
    for text, st, x, bl, _rf in runs:
        cx = x
        for ch in text:
            got.append((ch, cx, bl))
            cx += oracle.advance(st, ch)
    return got


def test_enter_session_does_not_write_page():
    doc, resolver = _sample_doc()
    page = doc[0]
    model = extract_page(page, 0)
    blk = _block(model, "HT-2026-0917")
    before = capture_page_state(doc, page)
    png0 = verifier.render_page_png(page)
    buf = BoxBuffer(blk)
    oracle = FontOracle(resolver, page)
    buf.set_measure(oracle.adv_fn())
    assert not buf.changed
    assert page_state_equal(doc, page, before)
    png1 = verifier.render_page_png(page)
    a1, a2 = _png(png0), _png(png1)
    diff = np.abs(a1.astype(np.int16) - a2.astype(np.int16)).sum(axis=2)
    assert (diff > 18).mean() < 0.0005
    doc.close()


def test_unchanged_commit_zero_write():
    doc, resolver = _sample_doc()
    page = doc[0]
    model = extract_page(page, 0)
    blk = _block(model, "HT-2026-0917")
    before = capture_page_state(doc, page)
    buf = BoxBuffer(blk)
    oracle = FontOracle(resolver, page)
    buf.set_measure(oracle.adv_fn())
    assert not buf.changed
    assert page_state_equal(doc, page, before)
    doc.close()


def test_preview_is_commit_state():
    doc, resolver = _sample_doc()
    page = doc[0]
    model = extract_page(page, 0)
    blk = _block(model, "HT-2026-0917")
    before = capture_page_state(doc, page)
    buf = BoxBuffer(blk)
    oracle = FontOracle(resolver, page)
    buf.set_measure(oracle.adv_fn())
    gi = blk.lines[0].text().find("HT")
    buf.insert((0, gi), "NO-")
    runs = buf.commit_runs(oracle)
    page = executor.apply_box_rebuild(
        doc, 0, before, _exp(blk.bbox, 0.6), runs, resolver)
    after_preview = capture_page_state(doc, page)
    after_commit = capture_page_state(doc, page)
    assert page_state_equal(doc, page, after_preview)
    assert after_preview["contents"] == after_commit["contents"]
    assert after_preview["page_obj"] == after_commit["page_obj"]
    t = page.get_text()
    assert "NO-HT-2026-0917" in t
    doc.close()


def test_origin_preserving_unchanged_line():
    doc, resolver = _sample_doc()
    page = doc[0]
    model = extract_page(page, 0)
    blk = _block(model, "HT-2026-0917")
    buf = BoxBuffer(blk)
    oracle = FontOracle(resolver, page)
    buf.set_measure(oracle.adv_fn())
    runs = buf.commit_runs(oracle)
    got = _expand_runs(runs, oracle)
    glyphs = [g for ln in blk.lines for g in ln.glyphs]
    assert len(got) == len(glyphs)
    for g, (ch, cx, bl) in zip(glyphs, got):
        assert g.char == ch
        assert abs(g.origin[0] - cx) < 0.05
        assert abs(g.origin[1] - bl) < 0.05
    doc.close()


def test_origin_preserving_prefix_after_append():
    doc, resolver = _sample_doc()
    page = doc[0]
    model = extract_page(page, 0)
    blk = _block(model, "HT-2026-0917")
    buf = BoxBuffer(blk)
    oracle = FontOracle(resolver, page)
    buf.set_measure(oracle.adv_fn())
    n = len(buf.hard_lines[0])
    buf.insert((0, n), "X")
    runs = buf.commit_runs(oracle)
    got = _expand_runs(runs, oracle)
    glyphs = blk.lines[0].glyphs
    assert len(got) == n + 1
    for g, (ch, cx, bl) in zip(glyphs, got):
        assert g.char == ch
        assert abs(g.origin[0] - cx) < 0.05
        assert abs(g.origin[1] - bl) < 0.05
    doc.close()


def test_split_then_merge_restores_baseline():
    doc, resolver = _sample_doc()
    page = doc[0]
    model = extract_page(page, 0)
    blk = _block(model, "HT-2026-0917")
    buf = BoxBuffer(blk)
    oracle = FontOracle(resolver, page)
    buf.set_measure(oracle.adv_fn())
    glyphs = blk.lines[0].glyphs
    buf.split_line((0, 3))
    assert len(buf.hard_lines) == 2
    buf.backspace((1, 0))
    assert len(buf.hard_lines) == 1
    got = _expand_runs(buf.commit_runs(oracle), oracle)
    assert len(got) == len(glyphs)
    for g, (ch, cx, bl) in zip(glyphs, got):
        assert g.char == ch
        assert abs(g.origin[0] - cx) < 0.05, (g.char, g.origin[0], cx)
        assert abs(g.origin[1] - bl) < 0.05, (g.char, g.origin[1], bl)
    doc.close()


def test_insert_at_line_start_uses_line_anchor():
    doc, resolver = _sample_doc()
    page = doc[0]
    model = extract_page(page, 0)
    blk = _block(model, "品名")
    assert blk is not None and len(blk.lines) >= 2
    g0 = blk.lines[1].glyphs[0]
    assert g0.origin[0] > blk.lines[0].glyphs[0].origin[0] + 20
    buf = BoxBuffer(blk)
    oracle = FontOracle(resolver, page)
    buf.set_measure(oracle.adv_fn())
    buf.insert((1, 0), "X")
    got = _expand_runs(buf.commit_runs(oracle), oracle)
    skip = len(blk.lines[0].glyphs)
    ch_x, x_new, y_new = got[skip]
    ch_old, x_old, y_old = got[skip + 1]
    assert ch_x == "X"
    assert ch_old == g0.char
    assert abs(x_new - g0.origin[0]) < 0.05, (x_new, g0.origin[0])
    assert abs(y_new - g0.origin[1]) < 0.05
    st = buf.hard_lines[1][0][1]
    assert abs(x_old - (g0.origin[0] + oracle.advance(st, "X"))) < 0.05
    doc.close()


def test_second_insert_runs_changes_bytes():
    """证明预览后再 insert_runs 会改页面；commit 必须跳过二次插入。"""
    doc, resolver = _sample_doc()
    page = doc[0]
    model = extract_page(page, 0)
    blk = _block(model, "HT-2026-0917")
    before = capture_page_state(doc, page)
    buf = BoxBuffer(blk)
    oracle = FontOracle(resolver, page)
    buf.set_measure(oracle.adv_fn())
    gi = blk.lines[0].text().find("HT")
    buf.insert((0, gi), "NO-")
    runs = buf.commit_runs(oracle)
    page = executor.apply_box_rebuild(
        doc, 0, before, _exp(blk.bbox, 0.6), runs, resolver)
    after_preview = capture_page_state(doc, page)
    executor.insert_runs(page, runs, resolver)
    after_double = capture_page_state(doc, page)
    assert after_preview["contents"] != after_double["contents"]
    doc.close()


def _embedded_doc():
    """嵌入非内置 fontname 的 PDF（复现 restore 后 _page_keys 失效）。"""
    doc = fitz.open()
    page = doc.new_page()
    buf = fitz.Font("china-s").buffer
    assert buf, "内置 CJK 字体无 buffer，无法构造嵌入字体用例"
    page.insert_font(fontname="F0", fontbuffer=buf)
    page.insert_text((72, 100), "合同编号ABC", fontname="F0", fontsize=12)
    return doc


def test_embedded_font_preview_twice():
    doc = _embedded_doc()
    resolver = FontResolver(doc)
    page = doc[0]
    model = extract_page(page, 0)
    blk = model.blocks[0]
    before = capture_page_state(doc, page)
    buf = BoxBuffer(blk)
    oracle = FontOracle(resolver, page)
    buf.set_measure(oracle.adv_fn())
    buf.insert((0, 0), "X")
    runs = buf.commit_runs(oracle)
    assert any(rf.key not in ("china-s", "helv") for _t, _s, _x, _b, rf in runs)
    page = executor.apply_box_rebuild(
        doc, 0, before, _exp(blk.bbox, 0.6), runs, resolver)
    assert "X合同编号ABC" in page.get_text().replace("\n", "")
    buf.insert((0, 1), "Y")
    runs = buf.commit_runs(oracle)
    page = executor.apply_box_rebuild(
        doc, 0, before, _exp(blk.bbox, 0.6), runs, resolver)
    text = page.get_text().replace("\n", "")
    assert "XY合同编号ABC" in text, text
    doc.close()


def test_ensure_page_font_survives_restore():
    doc = _embedded_doc()
    resolver = FontResolver(doc)
    page = doc[0]
    model = extract_page(page, 0)
    blk = model.blocks[0]
    before = capture_page_state(doc, page)
    buf = BoxBuffer(blk)
    oracle = FontOracle(resolver, page)
    buf.set_measure(oracle.adv_fn())
    buf.insert((0, 0), "X")
    runs = buf.commit_runs(oracle)
    page = executor.apply_box_rebuild(
        doc, 0, before, _exp(blk.bbox, 0.6), runs, resolver)
    from core.snapshot import restore_page_state
    restore_page_state(doc, page, before)
    page = doc[0]
    # 故意不清缓存：ensure_page_font 必须自己发现字体已不在页面上
    executor.insert_runs(page, runs, resolver)
    assert "X" in page.get_text()
    doc.close()


def _latin_block(text, size=12.0, tracking=0.0, origin=(50.0, 100.0)):
    from core.models import GlyphNode, TextBlock, TextLine, TextStyle
    st = TextStyle(font_name="helv", size=size)
    glyphs = []
    x = origin[0]
    w = size * 0.5
    y = origin[1]
    for ch in text:
        glyphs.append(GlyphNode(ch, (x, y - size, x + w, y + 2), (x, y), st))
        x += w + tracking
    line = TextLine(0, (origin[0], y - size, x, y + 2), y, glyphs)
    return TextBlock(0, (origin[0], y - size, x, y + 2), [line])


class _HelvOracle:
    def char_font(self, st, ch):
        from types import SimpleNamespace
        return SimpleNamespace(key="helv", is_original=True, source="内置")

    def advance(self, st, ch):
        o = ord(ch)
        if 0x2E80 <= o <= 0x9FFF or 0xFF00 <= o <= 0xFFEF:
            return st.size
        return st.size * 0.5

    def adv_fn(self):
        return lambda ch, st: self.advance(st, ch)


def test_overflow_shrink_scales_commit_size():
    blk = _latin_block("HELLOHELLO", size=12.0)
    buf = BoxBuffer(blk)
    nat = buf.width
    buf.set_width(nat * 0.55)
    assert buf.overflow == "shrink"
    assert buf.fit_scale < 0.9
    assert buf.fit_scale >= 0.45
    oracle = _HelvOracle()
    runs = buf.commit_runs(oracle)
    assert runs
    assert all(abs(st.size - 12.0 * buf.fit_scale) < 0.05
               for _t, st, _x, _b, _rf in runs)
    assert not buf.overflowed


def test_overflow_keep_flags_overflowed():
    blk = _latin_block("HELLOHELLO", size=12.0)
    buf = BoxBuffer(blk)
    nat = buf.width
    buf.overflow = "keep"
    buf.set_width(nat * 0.4)
    assert abs(buf.fit_scale - 1.0) < 1e-6
    assert buf.overflowed
    oracle = _HelvOracle()
    runs = buf.commit_runs(oracle)
    assert all(abs(st.size - 12.0) < 0.05 for _t, st, _x, _b, _rf in runs)


def test_apply_style_marks_changed_and_size():
    blk = _latin_block("ABC", size=11.0)
    buf = BoxBuffer(blk)
    assert not buf.changed
    buf.apply_style(size=18.0, color=(1.0, 0.0, 0.0))
    assert buf.changed
    st = buf.style_at((0, 1))
    assert abs(st.size - 18.0) < 1e-6
    assert st.color == (1.0, 0.0, 0.0)


def test_insert_picks_up_tracking():
    track = 1.4
    blk = _latin_block("AB", size=12.0, tracking=track)
    buf = BoxBuffer(blk)
    oracle = _HelvOracle()
    buf.set_measure(oracle.adv_fn())
    assert abs(buf.tracking - track) < 0.08
    buf.insert((0, 2), "X")
    runs = buf.commit_runs(oracle)
    xs = [x for _t, _s, x, _b, _rf in runs]
    assert len(xs) >= 3
    gap_ab = xs[1] - xs[0]
    gap_bx = xs[2] - xs[1]
    adv = oracle.advance(buf.hard_lines[0][0][1], "A")
    assert abs(gap_ab - (adv + track)) < 0.12
    assert abs(gap_bx - (adv + track)) < 0.12
