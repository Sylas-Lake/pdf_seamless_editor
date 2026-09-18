"""字体身份对齐：嵌入字体必须复用，不能整框掉进 china-ss/helv。"""
import io
import os
import tempfile

import numpy as np
from PIL import Image

from core.compat import fitz
from core import executor
from core.extractor import extract_page
from core.fonts import (FontOracle, FontResolver, font_identity_key,
                        fonts_same_face, system_font_candidates)
from core.sample import create_sample_pdf
from core.snapshot import capture_page_state
from core.textbox import BoxBuffer


def test_font_identity_key_aligns_span_and_basefont():
    assert fonts_same_face("MicrosoftYaHei", "Microsoft YaHei Regular")
    assert fonts_same_face("SimSun", "SimSun Regular")
    assert fonts_same_face("ABCDEF+SimSun", "SimSun Regular")
    assert fonts_same_face("ArialMT", "Arial Regular")
    assert fonts_same_face("Arial-BoldMT", "Arial Bold")
    assert fonts_same_face("TimesNewRomanPSMT", "Times New Roman")
    assert fonts_same_face("Calibri", "Calibri Regular")
    assert font_identity_key("NSimSun") != font_identity_key("SimSun")
    assert font_identity_key("SimHei") != font_identity_key("SimSun")
    assert font_identity_key("Heiti") == "heiti"


def test_system_candidates_resolve_postscript_names():
    yahei = system_font_candidates("MicrosoftYaHei")
    arial = system_font_candidates("ArialMT")
    times = system_font_candidates("TimesNewRomanPSMT")
    if os.path.isfile(r"C:\Windows\Fonts\msyh.ttc"):
        assert any(p.lower().endswith("msyh.ttc") for p in yahei)
    if os.path.isfile(r"C:\Windows\Fonts\arial.ttf"):
        assert any(p.lower().endswith("arial.ttf") for p in arial)
    if os.path.isfile(r"C:\Windows\Fonts\times.ttf"):
        assert any("times" in os.path.basename(p).lower() for p in times)


def _save_embedded(fontfile, text, fontsize=16):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_font(fontname="F0", fontfile=fontfile)
    page.insert_text((72, 120), text, fontname="F0", fontsize=fontsize)
    path = os.path.join(tempfile.mkdtemp(prefix="pdf_font_"), "t.pdf")
    doc.save(path, garbage=4, deflate=True)
    doc.close()
    return fitz.open(path)


def _png(page, z=4.0):
    pix = page.get_pixmap(matrix=fitz.Matrix(z, z), alpha=False)
    return np.asarray(Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB"))


def _crop(arr, rect, z):
    x0, y0, x1, y1 = rect
    return arr[int(y0 * z):int(y1 * z) + 1, int(x0 * z):int(x1 * z) + 1]


def _assert_reuse_and_stable(fontfile, text, needle=None):
    if not os.path.isfile(fontfile):
        import pytest
        pytest.skip("系统字体不存在: " + fontfile)
    doc = _save_embedded(fontfile, text)
    page = doc[0]
    model = extract_page(page, 0)
    blk = model.blocks[0]
    if needle:
        assert needle in blk.text()
    resolver = FontResolver(doc)
    oracle = FontOracle(resolver, page)
    st = blk.dominant_style()
    of = oracle.original_font(st)
    ch = blk.lines[0].glyphs[0].char
    cf = oracle.char_font(st, ch)
    assert of.is_original, (st.font_name, of.source, of.key)
    assert cf.is_original, (ch, cf.source, cf.key)
    assert of.source.startswith("复用原始嵌入")
    buf = BoxBuffer(blk)
    buf.set_measure(oracle.adv_fn())
    n = len(buf.hard_lines[0])
    buf.insert((0, n), "X")
    runs = buf.commit_runs(oracle)
    assert all(rf.is_original for _t, _s, _x, _b, rf in runs)
    orig_png = _png(page)
    before = capture_page_state(doc, page)
    page1 = executor.apply_box_rebuild(
        doc, 0, before,
        (blk.bbox[0] - 0.6, blk.bbox[1] - 0.6, blk.bbox[2] + 0.6, blk.bbox[3] + 0.6),
        runs, resolver)
    new_png = _png(page1)
    size = st.size
    pre = (blk.bbox[0], blk.bbox[1],
           max(blk.bbox[0] + 2, blk.bbox[2] - size * 0.3), blk.bbox[3])
    p0, p1 = _crop(orig_png, pre, 4), _crop(new_png, pre, 4)
    h = min(p0.shape[0], p1.shape[0])
    w = min(p0.shape[1], p1.shape[1])
    d = np.abs(p0[:h, :w].astype(np.int16) - p1[:h, :w].astype(np.int16)).sum(axis=2)
    ratio = float((d > 12).mean())
    assert ratio < 0.002, (st.font_name, of.source, ratio)
    doc.close()


def test_yahei_rebuild_reuses_embedded_and_keeps_prefix():
    _assert_reuse_and_stable(
        r"C:\Windows\Fonts\msyh.ttc", "供货合同编号条款")


def test_simsun_rebuild_reuses_embedded_and_keeps_prefix():
    _assert_reuse_and_stable(
        r"C:\Windows\Fonts\simsun.ttc", "供货合同编号")


def test_arialmt_rebuild_reuses_embedded():
    _assert_reuse_and_stable(
        r"C:\Windows\Fonts\arial.ttf", "Contract Title")


def test_sample_china_s_still_original():
    tmp = os.path.join(tempfile.mkdtemp(prefix="pdf_font_"), "s.pdf")
    create_sample_pdf(tmp)
    doc = fitz.open(tmp)
    page = doc[0]
    model = extract_page(page, 0)
    blk = None
    for b in model.blocks:
        if "HT-2026-0917" in b.text():
            blk = b
            break
    resolver = FontResolver(doc)
    oracle = FontOracle(resolver, page)
    rf = oracle.char_font(blk.dominant_style(), "合")
    assert rf.is_original
    assert rf.key == "china-s"
    doc.close()
