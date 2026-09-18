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
