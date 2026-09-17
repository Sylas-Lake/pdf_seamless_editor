"""PDF 内容流局部重写执行器（对应方案第七章）。

优先方案：结构化重写——通过 Redaction 真正从内容流删除原文本对象
（fill=False 不绘制遮盖背景，保持底图/纹理/图形完整），再于原基线、
原 Z 序（内容流末尾=视觉顶层）以原样式重插重建文本。
"""
import io
import math
from dataclasses import dataclass, field, replace

try:
    import pymupdf as fitz
except ImportError:
    import fitz

try:
    from PIL import Image
    HAS_PIL = True
except Exception:
    HAS_PIL = False

from .extractor import (boundary_x, inherited_style, runs_of, union_bbox)
from .models import TextStyle

# ---- Redaction 兼容常量（不同 PyMuPDF 版本） ----
IMG_NONE = getattr(fitz, "PDF_REDACT_IMAGE_NONE", 0)
IMG_REMOVE = getattr(fitz, "PDF_REDACT_IMAGE_REMOVE", 1)
ART_NONE = getattr(fitz, "PDF_REDACT_LINE_ART_NONE", 0)
TEXT_NONE = getattr(fitz, "PDF_REDACT_TEXT_NONE", 1)
TEXT_REMOVE = getattr(fitz, "PDF_REDACT_TEXT_REMOVE", 0)


def _redact_params(page):
    """探测 apply_redactions 支持的参数（graphics/text 为较新版本特性）。"""
    import inspect
    try:
        sig = inspect.signature(page.apply_redactions)
        return "graphics" in sig.parameters, "text" in sig.parameters
    except (TypeError, ValueError):
        return False, False


def apply_redact(page, rects, *, images, graphics=ART_NONE, text=None):
    """添加并应用一批 redaction（真正的删除，不绘制遮盖）。"""
    for r in rects:
        page.add_redact_annot(fitz.Rect(r))
    has_g, has_t = _redact_params(page)
    kwargs = {"images": images}
    if has_g:
        kwargs["graphics"] = graphics
    if has_t and text is not None:
        kwargs["text"] = text
    page.apply_redactions(**kwargs)


def image_blob(doc, xref: int):
    """提取图片原始字节。"""
    info = doc.extract_image(xref)
    if isinstance(info, dict):
        return info.get("image")
    if isinstance(info, (tuple, list)) and len(info) >= 4:
        return info[3]
    return None


# 内置 CJK 字体（PyMuPDF 以全宽渲染 ASCII，fitz.Font 度量为比例宽度，
# 二者不一致——必须按全宽估算，否则撤销范围过窄留下残骸、尾部重叠）
BUILTIN_CJK_KEYS = {"china-t", "china-s", "china-ts", "china-ss",
                    "japan", "japan-s", "korea", "korea-s"}


def measure_advance(rf, text: str, size: float) -> float:
    """估算文本渲染 advance（与内容流实际渲染一致）。"""
    if not text:
        return 0.0
    if rf is None or getattr(rf, "font", None) is None:
        return len(text) * size
    if getattr(rf, "key", "") in BUILTIN_CJK_KEYS:
        return len(text) * size
    try:
        return rf.font.text_length(text, size)
    except Exception:
        return len(text) * size


# ---------------------------------------------------------------- 行重建

@dataclass
class LineRebuild:
    """一次行内局部重建任务（原位替换模式）。

    - 顺序重建：从 anchor 起插入 new_text + tail_text（tail 保持原样式）；
    - 原位重插（keep_positions=True）：按 old_runs 的原始坐标恢复
      （撤销 / 样式修改用）。
    - applied_extent：本次实际写入的文本范围（撤销时需一并清除，
      防止超出原区域的新文本残骸——MuPDF 按字形完全包含判定删除）。
    """
    page_index: int
    line_bbox: tuple
    anchor_x: float
    baseline: float
    redact_rect: tuple
    base_style: TextStyle = None
    new_text: str = ""
    tail_text: str = ""
    tail_style: TextStyle = None
    old_runs: list = field(default_factory=list)   # [(text, style, x, baseline)]
    keep_positions: bool = False
    style_delta: dict = None                       # {"size":.., "color":..}
    overflow_strategy: str = "shrink"              # shrink | keep
    applied_extent: tuple = None                   # apply 时填充


@dataclass
class ApplyResult:
    """一次重建的保真结果（供保真引擎与状态栏展示）。"""
    level: str = "green"
    reasons: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def _worse(a: str, b: str) -> str:
    order = {"green": 0, "yellow": 1, "orange": 2, "red": 3}
    return a if order[a] >= order[b] else b


def make_rebuild(page_index, line, start, end, new_text,
                 overflow_strategy="shrink") -> LineRebuild:
    """构造行内 [start, end) → new_text 的重建任务（尾部保留）。"""
    g = line.glyphs
    n = len(g)
    start = max(0, min(start, n))
    end = max(start, min(end, n))
    seg = g[start:]
    if seg:
        redact = union_bbox([x.bbox for x in seg])
        redact = (redact[0] - 0.3, redact[1] - 0.3, redact[2] + 0.3, redact[3] + 0.3)
        anchor_x = seg[0].origin[0]
        baseline = seg[0].origin[1]
    else:
        bx = boundary_x(line, start)
        redact = (bx - 0.5, line.bbox[1], bx + 0.5, line.bbox[3])
        anchor_x = bx
        baseline = line.baseline if line.baseline else line.bbox[3]
    tail = g[end:]
    return LineRebuild(
        page_index=page_index, line_bbox=line.bbox, anchor_x=anchor_x,
        baseline=baseline, redact_rect=redact,
        base_style=inherited_style(line, start), new_text=new_text,
        tail_text="".join(x.char for x in tail),
        tail_style=(tail[0].style.copy() if tail else None),
        old_runs=runs_of(seg), overflow_strategy=overflow_strategy)


def _insert_run(page, resolver, text, style, x, baseline, size=None):
    """解析字体→注册页面资源→原位插入一段文本，返回 ResolvedFont。"""
    rf = resolver.resolve(page, style, text)
    key = resolver.ensure_page_font(page, rf)
    page.insert_text(fitz.Point(x, baseline), text, fontname=key,
                     fontsize=(size if size is not None else style.size),
                     color=style.color)
    return rf


def _clear_rb(page, rb: LineRebuild):
    """清除一个重建区域（原矩形 ∪ 上次实际写入范围，防残骸）。"""
    rects = [rb.redact_rect]
    if rb.applied_extent is not None:
        rects.append(rb.applied_extent)
    apply_redact(page, rects, images=IMG_NONE, graphics=ART_NONE)


def rebuild_lines(page, rbs: list, resolver) -> list:
    """两阶段执行：先全部清除（真删除），再逐行重建（避免行间互删）。"""
    results = []
    for rb in rbs:
        _clear_rb(page, rb)
    for rb in rbs:
        results.append(_insert_content(page, rb, resolver))
    return results


def restore_lines(page, rbs: list, resolver) -> list:
    """撤销：清除重建内容（含上次实际写入范围），按原始坐标恢复原始 Run。"""
    for rb in rbs:
        _clear_rb(page, rb)
    results = []
    for rb in rbs:
        res = ApplyResult()
        x0 = None
        x1 = None
        for text, style, x, baseline in rb.old_runs:
            try:
                rf = _insert_run(page, resolver, text, style, x, baseline)
                if not rf.is_original:
                    res.level = _worse(res.level, "yellow")
                    res.reasons.append("恢复时使用替代字体")
                w = measure_advance(rf, text, style.size)
                rx0, rx1 = x - 0.5, x + w + 1.0
                x0 = rx0 if x0 is None else min(x0, rx0)
                x1 = rx1 if x1 is None else max(x1, rx1)
            except Exception as e:
                res.warnings.append(f"恢复文本失败：{e}")
        if x0 is not None:
            rb.applied_extent = (x0, rb.redact_rect[1], x1, rb.redact_rect[3])
        results.append(res)
    return results


def _insert_content(page, rb: LineRebuild, resolver) -> ApplyResult:
    res = ApplyResult()

    # ---- 模式一：原位重插（样式修改）----
    if rb.keep_positions:
        x0 = x1 = None
        for text, style, x, baseline in rb.old_runs:
            st = style
            if rb.style_delta:
                st = replace(style,
                             size=rb.style_delta.get("size", style.size),
                             color=rb.style_delta.get("color", style.color))
            try:
                rf = _insert_run(page, resolver, text, st, x, baseline)
                if not rf.is_original:
                    res.level = _worse(res.level, "yellow")
                    res.reasons.append(f"样式重建使用：{rf.source}")
                w = measure_advance(rf, text, st.size)
                rx0, rx1 = x - 0.5, x + w + 1.0
                x0 = rx0 if x0 is None else min(x0, rx0)
                x1 = rx1 if x1 is None else max(x1, rx1)
            except Exception as e:
                res.warnings.append(f"样式重建失败：{e}")
        if x0 is not None:
            rb.applied_extent = (x0, rb.redact_rect[1], x1, rb.redact_rect[3])
        return res

    # ---- 模式二：顺序重建（输入/删除/替换）----
    new_text = rb.new_text or ""
    tail = rb.tail_text or ""
    base = rb.base_style or TextStyle()
    tail_style = rb.tail_style or base.copy()
    if not new_text and not tail:
        rb.applied_extent = None  # 纯删除
        return res

    rf_new = resolver.resolve(page, base, new_text) if new_text else None
    rf_tail = resolver.resolve(page, tail_style, tail) if tail else None

    # 可用宽度（原位约束）
    page_x1 = page.rect.x1
    if tail:
        region_w = rb.line_bbox[2] - rb.anchor_x + 1.0
    else:
        region_w = max(20.0, page_x1 - 36 - rb.anchor_x)

    size_new = base.size
    size_tail = tail_style.size
    w_new = measure_advance(rf_new, new_text, size_new) if new_text else 0.0
    w_tail = measure_advance(rf_tail, tail, size_tail) if tail else 0.0
    total = w_new + w_tail

    if total > region_w + 0.5:
        if rb.overflow_strategy == "shrink":
            scale = region_w / total if total > 0 else 1.0
            floor_hit = scale < 0.6
            if floor_hit:
                scale = 0.6
                res.warnings.append("文本过长：已按 60% 字号下限缩放，仍可能超出原区域")
                res.level = "orange"
            else:
                res.level = _worse(res.level, "yellow")
            res.reasons.append(f"字号自动适配 {base.size:.1f} → {base.size * scale:.1f}")
            size_new *= scale
            size_tail *= scale
        else:
            res.level = _worse(res.level, "orange")
            res.reasons.append("文本超出原区域宽度（保持字号策略）")
            res.warnings.append("新文本已超出原区域宽度，请检查右侧版面")

    x = rb.anchor_x
    wrote_end = x
    try:
        if new_text:
            _insert_run(page, resolver, new_text, base, x, rb.baseline, size_new)
            w = measure_advance(rf_new, new_text, size_new)
            x += w
            wrote_end = x
        if tail:
            _insert_run(page, resolver, tail, tail_style, x, rb.baseline, size_tail)
            wrote_end = x + measure_advance(rf_tail, tail, size_tail)
    except Exception as e:
        res.warnings.append(f"文本写入失败：{e}")
        res.level = _worse(res.level, "orange")

    # 记录本次实际写入范围（撤销/重做时需一并清除）
    rb.applied_extent = (rb.anchor_x - 0.5, rb.redact_rect[1],
                         wrote_end + 3.0, rb.redact_rect[3])

    if rf_new is not None:
        if not rf_new.is_original:
            res.level = _worse(res.level, "yellow")
            res.reasons.append(f"新增文本使用{rf_new.source}")
        if not rf_new.coverage_ok:
            res.level = _worse(res.level, "yellow")
            res.warnings.append("替代字体缺少部分字形，显示可能异常")
    return res


# ---------------------------------------------------------------- 图片操作

def place_image(doc, page, remove_rects, new_rect, deg: float, blob: bytes):
    """图片移动/缩放/旋转：移除原实例（仅图片，保留文字与矢量），
    于新位置重插。deg 非 90° 整数倍时用 PIL 预旋转。
    注意：结尾 reload_page 使 PyMuPDF 页面语法树缓存失效，
    调用方需重新获取页面对象（doc[page_index]）。"""
    if remove_rects:
        apply_redact(page, remove_rects, images=IMG_REMOVE,
                     graphics=ART_NONE, text=TEXT_NONE)
    if new_rect is not None:
        stream = blob
        rot = 0
        d = deg % 360
        if abs(d) > 0.01:
            if abs(d - round(d / 90.0) * 90) < 0.75:  # 90° 整数倍
                rot = int(round(d / 90.0) * 90) % 360
            elif HAS_PIL:
                im = Image.open(io.BytesIO(blob)).convert("RGBA")
                im = im.rotate(-d, expand=True, resample=Image.BICUBIC)
                buf = io.BytesIO()
                im.save(buf, format="PNG")
                stream = buf.getvalue()
                # 保持视觉中心与旋转后纵横比
                r = fitz.Rect(new_rect)
                cx, cy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
                w = r.width
                h = w * im.height / im.width if im.width else r.height
                new_rect = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
            else:
                d = 0  # 无 PIL：放弃任意角度
        page.insert_image(fitz.Rect(new_rect), stream=stream, rotate=rot)
    try:
        doc.reload_page(page)  # 缓存失效（图片信息同步）
    except Exception:
        pass


def replace_image(page, xref: int, blob: bytes):
    """替换图片内容（保持原位置与变换）。"""
    page.replace_image(xref, stream=blob)
