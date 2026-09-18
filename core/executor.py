"""PDF 内容流局部重写执行器（v2：文本框整体重建）。

- remove_text_region：Redaction 真删除（fill=False 不遮盖底图）；
- insert_runs：按可视行 Run 原位插入（提交时一次完成）；
- apply_box_rebuild：恢复快照后 redact + 插入（预览与提交共用）；
- place_image：图片移动/缩放/旋转（keep_proportion=False 保证所见即所得）。
"""
import io
from collections.abc import Iterable, Sequence

from .compat import fitz
from .types import PdfRect

try:
    from PIL import Image
    HAS_PIL = True
except Exception:
    HAS_PIL = False

# ---- Redaction 兼容常量 ----
IMG_NONE = getattr(fitz, "PDF_REDACT_IMAGE_NONE", 0)
IMG_REMOVE = getattr(fitz, "PDF_REDACT_IMAGE_REMOVE", 1)
ART_NONE = getattr(fitz, "PDF_REDACT_LINE_ART_NONE", 0)
TEXT_NONE = getattr(fitz, "PDF_REDACT_TEXT_NONE", 1)


def _redact_params(page):
    import inspect
    try:
        sig = inspect.signature(page.apply_redactions)
        return "graphics" in sig.parameters, "text" in sig.parameters
    except (TypeError, ValueError):
        return False, False


def apply_redact(page, rects: Iterable[PdfRect], *, images, graphics=ART_NONE, text=None):
    """添加并应用一批 redaction（真正的删除，不绘制遮盖）。"""
    for r in rects:
        try:
            page.add_redact_annot(fitz.Rect(r), fill=False, cross_out=False)
        except TypeError:
            page.add_redact_annot(fitz.Rect(r))
    has_g, has_t = _redact_params(page)
    kwargs = {"images": images}
    if has_g:
        kwargs["graphics"] = graphics
    if has_t and text is not None:
        kwargs["text"] = text
    page.apply_redactions(**kwargs)


def _as_rects(rect) -> list:
    """单个 PdfRect 或一组 PdfRect。"""
    if rect is None:
        return []
    if isinstance(rect, (list, tuple)) and rect and isinstance(rect[0], (int, float)):
        return [tuple(rect)]
    return [tuple(r) for r in rect]


def remove_text_region(page, rect):
    """清除一个或一批文本区域的全部文字（保留图片与矢量图形）。"""
    apply_redact(page, _as_rects(rect), images=IMG_NONE, graphics=ART_NONE)


def insert_runs(page, runs: Sequence, resolver):
    """按提交序列插入文本：[(text, style, x, baseline, rf), ...]"""
    for text, style, x, baseline, rf in runs:
        if not text:
            continue
        key = resolver.insert_fontname(page, rf, text, style)
        kwargs = {
            "fontname": key,
            "fontsize": style.size,
            "color": style.color,
        }
        rm = getattr(style, "render_mode", 0) or 0
        if rm:
            kwargs["render_mode"] = rm
            kwargs["border_width"] = getattr(style, "border_width", 0.05) or 0.05
        name = (getattr(style, "font_name", "") or "").lower()
        if (getattr(style, "is_italic", False)
                and "italic" not in name and "oblique" not in name):
            kwargs["morph"] = (fitz.Point(x, baseline),
                               fitz.Matrix(1, 0, 0.22, 1, 0, 0))
        page.insert_text(fitz.Point(x, baseline), text, **kwargs)


def apply_box_rebuild(doc, page_index: int, before_state, rect,
                      runs: Sequence, resolver):
    """预览 = 提交：恢复原页 → redact 原字形区域 → insert_runs。返回当前页对象。"""
    from .snapshot import restore_page_state
    page = doc[page_index]
    restore_page_state(doc, page, before_state)
    page = doc[page_index]
    resolver.invalidate_page(page_index)
    remove_text_region(page, rect)
    insert_runs(page, runs, resolver)
    return doc[page_index]


def image_blob(doc, xref: int) -> bytes | None:
    """提取图片原始字节。"""
    info = doc.extract_image(xref)
    if isinstance(info, dict):
        return info.get("image")
    if isinstance(info, (tuple, list)) and len(info) >= 4:
        return info[3]
    return None


def place_image(doc, page, remove_rects: Sequence[PdfRect] | None,
                new_rect: PdfRect | None, deg: float, blob: bytes):
    """图片移动/缩放/旋转：移除原实例（仅图片），于新位置精确重插。
    keep_proportion=False → 手柄所见即所得。"""
    if remove_rects:
        apply_redact(page, remove_rects, images=IMG_REMOVE,
                     graphics=ART_NONE, text=TEXT_NONE)
    if new_rect is not None:
        stream = blob
        rot = 0
        d = deg % 360
        if abs(d) > 0.01:
            if abs(d - round(d / 90.0) * 90) < 0.75:
                rot = int(round(d / 90.0) * 90) % 360
            elif HAS_PIL:
                im = Image.open(io.BytesIO(blob)).convert("RGBA")
                im = im.rotate(-d, expand=True, resample=Image.BICUBIC)
                buf = io.BytesIO()
                im.save(buf, format="PNG")
                stream = buf.getvalue()
                r = fitz.Rect(new_rect)
                cx, cy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
                w = r.width
                h = w * im.height / im.width if im.width else r.height
                new_rect = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
            else:
                rot = 0
        page.insert_image(fitz.Rect(new_rect), stream=stream, rotate=rot,
                          keep_proportion=False)
    try:
        doc.reload_page(page)
    except Exception:
        pass


def replace_image(page, xref: int, blob: bytes):
    """替换图片内容（保持原位置与变换）。"""
    page.replace_image(xref, stream=blob)
