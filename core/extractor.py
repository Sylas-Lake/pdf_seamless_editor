"""字符级 PDF 逆向解析与视觉语义重建（对应方案第四章）。

从 PyMuPDF rawdict 提取每一个字形（unicode、bbox、基线、样式），
构建 TextLine 层级；并提供点击定位、双击选词、样式继承等编辑交互
所需的几何查询。
"""
import math
import unicodedata

try:
    import pymupdf as fitz
except ImportError:  # 兼容旧包名
    import fitz

from .models import GlyphNode, ImageObject, PageModel, TextLine, TextStyle


# ---------------------------------------------------------------- 基础工具

def _int_to_rgb(v: int) -> tuple:
    """sRGB 整数 → (r, g, b) 0..1。"""
    v = int(v) & 0xFFFFFF
    return ((v >> 16) & 255) / 255.0, ((v >> 8) & 255) / 255.0, (v & 255) / 255.0


def _is_mark(ch: str) -> bool:
    """组合符号 / 零宽连接符（grapheme 的一部分，不算独立字符）。"""
    return unicodedata.combining(ch) != 0 or ch in ("\u200d", "\u200b", "\ufe0f")


def n_grapheme_clusters(text: str) -> int:
    """用户感知字符数（组合符号并入前字符）。"""
    if not text:
        return 0
    return sum(0 if _is_mark(c) else 1 for c in text)


def char_kind(ch: str) -> str:
    """双击选词 / 词边界分类（简化 ICU 分词规则）。"""
    if ch.isspace():
        return "space"
    o = ord(ch)
    # CJK 统一表意 / 扩展A / 平假名 片假名 / 谚文
    if (0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF
            or 0x3040 <= o <= 0x30FF or 0xAC00 <= o <= 0xD7AF
            or 0xF900 <= o <= 0xFAFF or 0x3000 <= o <= 0x303F and o >= 0x3020):
        return "cjk"
    if ch.isalnum() or ch in "_-'’":
        return "latin"
    return "punct"


def union_bbox(boxes) -> tuple:
    xs0 = min(b[0] for b in boxes)
    ys0 = min(b[1] for b in boxes)
    xs1 = max(b[2] for b in boxes)
    ys1 = max(b[3] for b in boxes)
    return (xs0, ys0, xs1, ys1)


def iou(a: tuple, b: tuple) -> float:
    """两矩形交并比（编辑后行定位用）。"""
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


# ---------------------------------------------------------------- 页面提取

def extract_page(page, page_index: int) -> PageModel:
    """解析一页 → PageModel（字符级 + 行级 + 图片）。"""
    model = PageModel(page_index=page_index,
                      rect=tuple(page.rect),
                      rotation=page.rotation)
    try:
        raw = page.get_text("rawdict", sort=True)  # sort=True：阅读顺序
    except Exception:
        raw = {"blocks": []}

    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        for ln in block.get("lines", []):
            glyphs = []
            for span in ln.get("spans", []):
                style = TextStyle(
                    font_name=span.get("font", "") or "",
                    size=float(span.get("size", 11.0) or 11.0),
                    color=_int_to_rgb(span.get("color", 0) or 0),
                    flags=int(span.get("flags", 0) or 0),
                )
                for ch in span.get("chars", []):
                    c = ch.get("c", "")
                    if c in ("\ufffe", "\uffff"):  # 不可见占位字符
                        continue
                    if not c:
                        continue
                    if glyphs and _is_mark(c):
                        # 组合符号并入前一字素，bbox 合并
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
            # 主基线：多数 origin.y
            ys = {}
            for g in glyphs:
                ys[g.origin[1]] = ys.get(g.origin[1], 0) + 1
            baseline = max(ys.items(), key=lambda kv: kv[1])[0] if ys else bbox[3]
            d = tuple(ln.get("dir", (1, 0)) or (1, 0))
            model.lines.append(TextLine(len(model.lines), bbox, baseline,
                                        glyphs, d))

    # 图片对象（含放置变换角）
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

    # 扫描件检测：整页图像覆盖
    page_area = (model.rect[2] - model.rect[0]) * (model.rect[3] - model.rect[1])
    for img in model.images:
        area = max(0.0, (img.rect[2] - img.rect[0]) * (img.rect[3] - img.rect[1]))
        if page_area > 0 and area / page_area >= 0.80:
            model.scanned = True
            model.scanned_note = "页面为整页图像（扫描件）：文本编辑属图像/OCR 模式（红色保真）"
            break
    return model


# ---------------------------------------------------------------- 命中测试

def find_line(model: PageModel, x: float, y: float, max_dist: float = 30.0):
    """查找坐标附近最近的文本行（用于单击定位）。"""
    best, best_d = None, None
    for ln in model.lines:
        b = ln.bbox
        if b[0] <= x <= b[2] and b[1] <= y <= b[3]:
            d = 0.0
        else:
            dx = 0.0 if b[0] <= x <= b[2] else min(abs(x - b[0]), abs(x - b[2]))
            dy = 0.0 if b[1] <= y <= b[3] else min(abs(y - b[1]), abs(y - b[3]))
            d = dy * 3.0 + dx  # 垂直距离权重更高
        if best_d is None or d < best_d:
            best, best_d = ln, d
    if best is not None and best_d <= max_dist:
        return best
    return None


def cursor_index_at(line: TextLine, x: float) -> int:
    """把 x 坐标投影到行基线上，返回最接近的字符间隙索引。"""
    g = line.glyphs
    if not g:
        return 0
    if x <= g[0].bbox[0]:
        return 0
    if x >= g[-1].bbox[2]:
        return len(g)
    idx = 0
    for i in range(len(g) - 1):
        if x >= (g[i].bbox[2] + g[i + 1].bbox[0]) / 2:
            idx = i + 1
        else:
            break
    return idx


def boundary_x(line: TextLine, idx: int) -> float:
    """字符间隙索引 → 边界 x 坐标（光标绘制位置）。"""
    g = line.glyphs
    if not g:
        return line.bbox[0]
    if idx <= 0:
        return g[0].bbox[0]
    if idx >= len(g):
        return g[-1].bbox[2]
    return (g[idx - 1].bbox[2] + g[idx].bbox[0]) / 2


def select_word_at(line: TextLine, idx: int) -> tuple:
    """双击选词：以 idx 为中心的词边界 (start, end)。"""
    g = line.glyphs
    n = len(g)
    if n == 0:
        return (0, 0)
    i = min(idx, n - 1)
    if idx >= n:
        i = n - 1
    k = char_kind(g[i].char)
    if k == "space":
        a = i
        while a > 0 and char_kind(g[a - 1].char) == "space":
            a -= 1
        b = i + 1
        while b < n and char_kind(g[b].char) == "space":
            b += 1
        return (a, b)
    if k == "punct":
        return (i, i + 1)
    a = i
    while a > 0 and char_kind(g[a - 1].char) == k:
        a -= 1
    b = i + 1
    while b < n and char_kind(g[b].char) == k:
        b += 1
    return (a, b)


def word_step(line: TextLine, idx: int, forward: bool) -> int:
    """Ctrl+左右：移动到相邻词边界。"""
    g = line.glyphs
    n = len(g)
    if forward:
        i = idx
        if i < n:
            k = char_kind(g[i].char)
            while i < n and char_kind(g[i].char) == k:
                i += 1
        while i < n and char_kind(g[i].char) == "space":
            i += 1
        return i
    i = idx
    if i > 0:
        k = char_kind(g[i - 1].char)
        while i > 0 and char_kind(g[i - 1].char) == k:
            i -= 1
    while i > 0 and char_kind(g[i - 1].char) == "space":
        i -= 1
    return i


# ---------------------------------------------------------------- 样式继承

def inherited_style(line: TextLine, idx: int) -> TextStyle:
    """样式自动继承（方案第五章优先级）：
    左侧同 Run → 右侧 Run → 行主样式 → 默认。"""
    g = line.glyphs
    if g and 0 < idx <= len(g):
        return g[idx - 1].style.copy()
    if g and idx < len(g):
        return g[idx].style.copy()
    if g:  # 行主样式（最多数）
        stat = {}
        for x in g:
            key = (x.style.font_name, x.style.size, x.style.color)
            stat[key] = stat.get(key, 0) + 1
        key = max(stat.items(), key=lambda kv: kv[1])[0]
        for x in g:
            if (x.style.font_name, x.style.size, x.style.color) == key:
                return x.style.copy()
    return TextStyle()


def runs_of(glyphs) -> list:
    """把字形序列按样式连续性聚合为 Run：
    [(text, style_copy, x, baseline), ...]（局部对象重建的原始数据）。"""
    runs = []
    for g in glyphs:
        key = (g.style.font_name, g.style.size, g.style.color, g.style.flags)
        if runs and runs[-1][4] == key:
            runs[-1][0] += g.char
        else:
            runs.append([g.char, g.style.copy(), g.origin[0], g.origin[1], key])
    return [(t, s, x, y) for (t, s, x, y, _k) in runs]


def find_line_by_bbox(model: PageModel, bbox: tuple, fallback_index: int = 0) -> int:
    """编辑后按几何位置重新定位行（IoU 最大优先）。"""
    if not model.lines:
        return -1
    best_i, best_s = 0, -1.0
    for ln in model.lines:
        s = iou(ln.bbox, bbox)
        if s > best_s:
            best_s, best_i = s, ln.index
    if best_s < 0.15 and 0 <= fallback_index < len(model.lines):
        return fallback_index
    return best_i
