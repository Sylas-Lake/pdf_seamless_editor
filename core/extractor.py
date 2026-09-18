"""字符级 PDF 逆向解析（v2：按文本框分组）。"""
import math
import unicodedata

from .fonts import font_display_label, lookup_font_info, page_font_catalog
from .models import (GlyphNode, ImageObject, PageModel, TextBlock,
                     TextLine, TextStyle)
from .types import PdfRect


def _int_to_rgb(v: int) -> tuple[float, float, float]:
    v = int(v) & 0xFFFFFF
    return ((v >> 16) & 255) / 255.0, ((v >> 8) & 255) / 255.0, (v & 255) / 255.0


def _is_mark(ch: str) -> bool:
    return unicodedata.combining(ch) != 0 or ch in ("\u200d", "\u200b", "\ufe0f")


def n_grapheme_clusters(text: str) -> int:
    if not text:
        return 0
    return sum(0 if _is_mark(c) else 1 for c in text)


def char_kind(ch: str) -> str:
    """词边界分类（双击选词 / 词导航）。"""
    if ch.isspace():
        return "space"
    o = ord(ch)
    if (0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF
            or 0x3040 <= o <= 0x30FF or 0xAC00 <= o <= 0xD7AF
            or 0xF900 <= o <= 0xFAFF):
        return "cjk"
    if ch.isalnum() or ch in "_-'’":
        return "latin"
    return "punct"


def union_bbox(boxes: list[PdfRect]) -> PdfRect:
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def iou(a: PdfRect, b: PdfRect) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    ua = ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)
    return inter / ua if ua > 0 else 0.0


def extract_page(page, page_index: int) -> PageModel:
    """解析一页 → PageModel（文本框分组 + 图片）。"""
    model = PageModel(page_index=page_index,
                      rect=tuple(page.rect), rotation=page.rotation)
    try:
        raw = page.get_text("rawdict", sort=True)
    except Exception:
        raw = {"blocks": []}

    catalog = page_font_catalog(page)

    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        lines = []
        for ln in block.get("lines", []):
            glyphs = []
            for span in ln.get("spans", []):
                flags = int(span.get("flags", 0) or 0)
                font_name = span.get("font", "") or ""
                info = lookup_font_info(catalog, font_name)
                flags |= int(info.get("flags", 0) or 0)
                display = info.get("display") or font_display_label(font_name)
                size = float(span.get("size", 11.0) or 11.0)
                style = TextStyle(
                    font_name=font_name,
                    display_name=display,
                    size=size,
                    color=_int_to_rgb(span.get("color", 0) or 0),
                    flags=flags,
                )
                # 粗体标记但字体名不含 Bold：按填+描假粗体重建，避免落下变细
                n = font_name.lower()
                if (flags & 16) and not any(
                        w in n for w in ("bold", "black", "heavy", "semibold")):
                    style.render_mode = 2
                    style.border_width = max(0.15, size * 0.035)
                for ch in span.get("chars", []):
                    c = ch.get("c", "")
                    if not c or c in ("\ufffe", "\uffff"):
                        continue
                    if glyphs and _is_mark(c):
                        g = glyphs[-1]
                        nb = ch["bbox"]
                        g.bbox = (min(g.bbox[0], nb[0]), min(g.bbox[1], nb[1]),
                                  max(g.bbox[2], nb[2]), max(g.bbox[3], nb[3]))
                        g.char += c
                        continue
                    glyphs.append(GlyphNode(c, tuple(ch["bbox"]),
                                            tuple(ch["origin"]), style))
            if not glyphs:
                continue
            bbox = union_bbox([g.bbox for g in glyphs])
            ys = {}
            for g in glyphs:
                ys[g.origin[1]] = ys.get(g.origin[1], 0) + 1
            baseline = max(ys.items(), key=lambda kv: kv[1])[0] if ys else bbox[3]
            d = tuple(ln.get("dir", (1, 0)) or (1, 0))
            lines.append(TextLine(len(lines), bbox, baseline, glyphs, d))
        if not lines:
            continue
        bb = union_bbox([l.bbox for l in lines])
        model.blocks.append(TextBlock(len(model.blocks), bb, lines))

    # 图片
    try:
        for info in page.get_image_info(xrefs=True):
            xref = info.get("xref", 0)
            bbox = tuple(info.get("bbox", (0, 0, 0, 0)))
            t = info.get("transform", (1, 0, 0, 1, 0, 0))
            deg = math.degrees(math.atan2(t[1], t[0])) % 360
            if deg > 180:
                deg -= 360
            model.images.append(ImageObject(xref, bbox,
                                            int(info.get("width", 0) or 0),
                                            int(info.get("height", 0) or 0),
                                            deg))
    except Exception:
        pass

    # 扫描件检测
    page_area = (model.rect[2] - model.rect[0]) * (model.rect[3] - model.rect[1])
    for img in model.images:
        area = max(0.0, (img.rect[2] - img.rect[0]) * (img.rect[3] - img.rect[1]))
        if page_area > 0 and area / page_area >= 0.80:
            model.scanned = True
            model.scanned_note = "页面为整页图像（扫描件）：文本编辑属图像/OCR 模式（红色保真）"
            break
    return model


def line_redact_rects(block: "TextBlock", pad: float = 0.25) -> list[PdfRect]:
    """本框各行字形墨水盒（小膨胀）。按行挖空，少伤行间/邻列的字。"""
    rects = []
    for ln in getattr(block, "lines", []) or []:
        glyphs = getattr(ln, "glyphs", None) or []
        if not glyphs:
            continue
        r = union_bbox([g.bbox for g in glyphs])
        rects.append((r[0] - pad, r[1] - pad, r[2] + pad, r[3] + pad))
    if not rects:
        b = getattr(block, "bbox", None)
        if b:
            rects.append((b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad))
    return rects


def inherited_style(block: "TextBlock", line: "TextLine", idx: int) -> TextStyle:
    """光标处样式继承：左 Run → 右 Run → 行主样式 → 框主样式。"""
    g = line.glyphs
    if g and 0 < idx <= len(g):
        return g[idx - 1].style.copy()
    if g and idx < len(g):
        return g[idx].style.copy()
    if g:
        stat = {}
        for x in g:
            stat[x.style.key] = stat.get(x.style.key, 0) + 1
        best = max(stat.items(), key=lambda kv: kv[1])[0]
        for x in g:
            if x.style.key == best:
                return x.style.copy()
    return block.dominant_style()
