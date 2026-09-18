"""缺字显示：嵌入子集有码位、无轮廓时补绘。"""
from __future__ import annotations

import os
import tempfile

import numpy as np
import pytest

from core.compat import fitz
from core.extractor import extract_page
from core.fonts import FontOracle, FontResolver
from core.models import GlyphNode, PageModel, TextBlock, TextLine, TextStyle
from core.render import fill_blank_cjk_glyphs


def _ink_ratio(pix, bbox, zoom):
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
        pix.height, pix.stride)[:, :pix.width * pix.n]
    rgb = arr.reshape(pix.height, pix.width, pix.n)
    x0, y0, x1, y1 = [int(round(v * zoom)) for v in bbox]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(pix.width, x1), min(pix.height, y1)
    patch = rgb[y0:y1, x0:x1, :3]
    if patch.size == 0:
        return 0.0
    return float((patch.min(axis=2) < 250).mean())


def test_fill_blank_cjk_on_white_pixmap():
    doc = fitz.open()
    page = doc.new_page(width=300, height=200)
    z = 2.0
    pix = page.get_pixmap(matrix=fitz.Matrix(z, z), alpha=False)
    style = TextStyle(font_name="SimSun", size=16.0, color=(0.0, 0.0, 0.0))
    g = GlyphNode("颇", (80.0, 90.0, 96.0, 108.0), (80.0, 104.0), style)
    line = TextLine(0, g.bbox, g.origin[1], [g])
    block = TextBlock(0, g.bbox, [line])
    model = PageModel(0, tuple(page.rect), 0, [block])
    assert _ink_ratio(pix, g.bbox, z) < 0.02
    n = fill_blank_cjk_glyphs(pix, z, model)
    assert n == 1
    assert g.missing_ink
    assert _ink_ratio(pix, g.bbox, z) > 0.08
    doc.close()


def _subset_font_without_outline(text, blank, dest):
    pytest.importorskip("fontTools")
    src = r"C:\Windows\Fonts\simsun.ttc"
    if not os.path.isfile(src):
        pytest.skip("no SimSun")
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.subset import Options, Subsetter
    from fontTools.ttLib import TTFont

    font = TTFont(src, fontNumber=0)
    ss = Subsetter(options=Options())
    ss.populate(text=text)
    ss.subset(font)
    cmap = font.getBestCmap()
    gname = cmap.get(ord(blank))
    if not gname:
        font.close()
        pytest.skip("cmap missing blank char")
    font["glyf"][gname] = TTGlyphPen(font.getGlyphSet()).glyph()
    font.save(dest)
    font.close()


def test_empty_po_glyph_is_filled_and_not_original_font():
    text = "从而也颇有名气"
    fd, ttf = tempfile.mkstemp(suffix=".ttf")
    os.close(fd)
    fd, pdf = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    try:
        _subset_font_without_outline(text, "颇", ttf)
        doc = fitz.open()
        page = doc.new_page()
        page.insert_font(fontname="F0", fontfile=ttf)
        page.insert_text((72, 120), text, fontname="F0", fontsize=16)
        model = extract_page(page, 0)
        assert any("颇" in b.text() for b in model.blocks)
        z = 2.0
        pix = page.get_pixmap(matrix=fitz.Matrix(z, z), alpha=False)
        glyph = next(g for b in model.blocks for ln in b.lines for g in ln.glyphs
                     if g.char == "颇")
        neighbor = next(g for b in model.blocks for ln in b.lines for g in ln.glyphs
                        if g.char == "也")
        assert _ink_ratio(pix, glyph.bbox, z) < 0.02
        before_n = _ink_ratio(pix, neighbor.bbox, z)
        n = fill_blank_cjk_glyphs(pix, z, model)
        assert n >= 1
        assert glyph.missing_ink
        assert _ink_ratio(pix, glyph.bbox, z) > 0.08
        assert _ink_ratio(pix, neighbor.bbox, z) > before_n * 0.8

        resolver = FontResolver(doc)
        blk = next(b for b in model.blocks if "颇" in b.text())
        oracle = FontOracle.from_block(resolver, page, blk)
        rf = oracle.char_font(glyph.style, "颇")
        assert not rf.is_original
        doc.close()
    finally:
        for p in (ttf, pdf):
            try:
                os.remove(p)
            except OSError:
                pass
