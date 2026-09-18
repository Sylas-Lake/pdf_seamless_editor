"""页面栅格化：补绘嵌入子集里有码位、无轮廓的汉字。"""
from __future__ import annotations

import numpy as np

from .compat import fitz
from .fonts import _has_cjk, cjk_builtin_key

_INK = 250
_BLANK = 0.03


def _pix_rgb(pix):
    """可写 RGB 视图；旧版 PyMuPDF 没有 samples_mv 时返回 None。"""
    mv = getattr(pix, "samples_mv", None)
    if mv is None or pix.n < 3:
        return None
    return np.ndarray(
        (pix.height, pix.width, pix.n),
        dtype=np.uint8,
        buffer=mv,
        strides=(pix.stride, pix.n, 1),
    )


def _box_px(bbox, zoom, w, h, pad=0):
    x0 = int(round(bbox[0] * zoom)) - pad
    y0 = int(round(bbox[1] * zoom)) - pad
    x1 = int(round(bbox[2] * zoom)) + pad
    y1 = int(round(bbox[3] * zoom)) + pad
    return max(0, x0), max(0, y0), min(w, x1), min(h, y1)


def _ink_ratio(rgb, bbox, zoom) -> float:
    h, w = rgb.shape[:2]
    x0, y0, x1, y1 = _box_px(bbox, zoom, w, h)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return 1.0
    patch = rgb[y0:y1, x0:x1, :3]
    if patch.size == 0:
        return 1.0
    return float((patch.min(axis=2) < _INK).mean())


def fill_blank_cjk_glyphs(pix, zoom: float, model) -> int:
    """把提取到、但位图里没有墨水的汉字用内置 CJK 补到 pixmap 上。

    不改 PDF 内容流。轮廓被掏空的 CID 子集（Acrobat 会找系统字体，MuPDF 画空白）
    走这条显示修复；命中的字形打上 missing_ink，提交时改用完整字体。
    返回补绘的字形数。
    """
    if pix is None or model is None or zoom <= 0:
        return 0
    rgb = _pix_rgb(pix)
    if rgb is None:
        return 0
    missing = []
    for block in getattr(model, "blocks", []) or []:
        for ln in getattr(block, "lines", []) or []:
            if not getattr(ln, "horizontal", True):
                continue
            for g in getattr(ln, "glyphs", []) or []:
                ch = getattr(g, "char", "") or ""
                if not _has_cjk(ch):
                    continue
                if _ink_ratio(rgb, g.bbox, zoom) >= _BLANK:
                    continue
                g.missing_ink = True
                missing.append(g)
    if not missing:
        return 0
    rect = getattr(model, "rect", None) or (0, 0, pix.width / zoom, pix.height / zoom)
    width = max(1.0, float(rect[2] - rect[0]))
    height = max(1.0, float(rect[3] - rect[1]))
    tmp = fitz.open()
    try:
        page = tmp.new_page(width=width, height=height)
        for g in missing:
            st = g.style
            fn = cjk_builtin_key(st) if st is not None else "china-s"
            color = getattr(st, "color", (0.0, 0.0, 0.0)) if st else (0.0, 0.0, 0.0)
            size = float(getattr(st, "size", 11.0) or 11.0) if st else 11.0
            try:
                page.insert_text(
                    fitz.Point(g.origin[0], g.origin[1]),
                    g.char,
                    fontname=fn,
                    fontsize=size,
                    color=color,
                )
            except Exception:
                try:
                    page.insert_text(
                        fitz.Point(g.origin[0], g.origin[1]),
                        g.char,
                        fontname="china-ss",
                        fontsize=size,
                        color=color,
                    )
                except Exception:
                    continue
        overlay = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    finally:
        tmp.close()
    ov = _pix_rgb(overlay)
    if ov is None:
        return 0
    hh = min(rgb.shape[0], ov.shape[0])
    ww = min(rgb.shape[1], ov.shape[1])
    if hh <= 0 or ww <= 0:
        return 0
    sel = np.zeros((hh, ww), dtype=bool)
    for g in missing:
        x0, y0, x1, y1 = _box_px(g.bbox, zoom, ww, hh, pad=2)
        sel[y0:y1, x0:x1] = True
    ink = ov[:hh, :ww, :3].min(axis=2) < _INK
    use = sel & ink
    if not np.any(use):
        return 0
    rgb[:hh, :ww, :3][use] = ov[:hh, :ww, :3][use]
    return len(missing)
