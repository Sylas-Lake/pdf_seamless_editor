"""核心数据模型（v2：文本框编辑架构）。

页面 → 文本框（TextBlock，对应 PDF 文本块）→ 行 → 字形。
编辑以"文本框"为单元：框内编辑缓冲，提交时整体重建；
撤销/重做通过页面状态快照字节级恢复。
"""
from dataclasses import dataclass, field, replace
from typing import Optional

from .types import PdfRect, Point, Rgb


@dataclass
class TextStyle:
    """文本样式（继承自原 PDF span，用于样式继承）。"""
    font_name: str = ""
    display_name: str = ""
    size: float = 11.0
    color: Rgb = (0.0, 0.0, 0.0)
    flags: int = 0
    render_mode: int = 0          # PDF Tr：0 填充，2 填+描（假粗体）
    border_width: float = 0.05

    def copy(self) -> "TextStyle":
        return replace(self)

    @property
    def key(self) -> tuple:
        return (self.font_name, round(self.size, 3), self.color, self.flags,
                self.render_mode)

    @property
    def is_bold(self) -> bool:
        if self.flags & 16 or self.render_mode == 2:
            return True
        n = f"{self.font_name} {self.display_name}".lower()
        return any(w in n for w in ("bold", "black", "heavy", "semibold", "粗体"))

    @property
    def is_italic(self) -> bool:
        if self.flags & 2:
            return True
        n = f"{self.font_name} {self.display_name}".lower()
        return any(w in n for w in ("italic", "oblique", "斜体"))

    @property
    def name_says_bold(self) -> bool:
        n = f"{self.font_name} {self.display_name}".lower()
        return any(w in n for w in ("bold", "black", "heavy", "semibold", "粗体"))


@dataclass
class GlyphNode:
    """可见字形（grapheme 粒度，组合字符并入前一字符）。"""
    char: str
    bbox: PdfRect
    origin: Point
    style: Optional[TextStyle] = None
    missing_ink: bool = False


@dataclass
class TextLine:
    """文本行（框内）。"""
    index: int
    bbox: PdfRect
    baseline: float
    glyphs: list[GlyphNode] = field(default_factory=list)
    direction: Point = (1.0, 0.0)

    @property
    def horizontal(self) -> bool:
        return abs(self.direction[0]) > 0.98

    def text(self) -> str:
        return "".join(g.char for g in self.glyphs)


@dataclass
class TextBlock:
    """文本框：视觉上的一个文本区域（PDF block 推断）。"""
    index: int
    bbox: PdfRect
    lines: list[TextLine] = field(default_factory=list)

    @property
    def horizontal(self) -> bool:
        return all(l.horizontal for l in self.lines) if self.lines else True

    @property
    def first_baseline(self) -> float:
        return self.lines[0].baseline if self.lines else self.bbox[3]

    def text(self) -> str:
        return "\n".join(l.text() for l in self.lines)

    def dominant_style(self) -> TextStyle:
        """框主样式（最多数字形的样式）。"""
        stat = {}
        for l in self.lines:
            for g in l.glyphs:
                stat[g.style.key] = stat.get(g.style.key, 0) + 1
        if not stat:
            return TextStyle()
        best = max(stat.items(), key=lambda kv: kv[1])[0]
        for l in self.lines:
            for g in l.glyphs:
                if g.style.key == best:
                    return g.style.copy()
        return TextStyle()


@dataclass
class ImageObject:
    """页面图片对象。"""
    xref: int
    rect: PdfRect
    width: int = 0
    height: int = 0
    deg: float = 0.0


@dataclass
class PageModel:
    """页面视觉语义模型。"""
    page_index: int
    rect: PdfRect
    rotation: int = 0
    blocks: list[TextBlock] = field(default_factory=list)
    images: list[ImageObject] = field(default_factory=list)
    scanned: bool = False
    scanned_note: str = ""

    def block_at(self, x: float, y: float) -> Optional[TextBlock]:
        """命中测试：返回包含点的最小文本框（无则 None）。"""
        best, best_area = None, None
        for b in self.blocks:
            r = b.bbox
            if r[0] <= x <= r[2] and r[1] <= y <= r[3]:
                area = (r[2] - r[0]) * (r[3] - r[1])
                if best_area is None or area < best_area:
                    best, best_area = b, area
        return best

    def image_at(self, x: float, y: float) -> Optional[ImageObject]:
        """命中测试：返回包含点的最上层图片（后绘制者优先）。"""
        for img in reversed(self.images):
            r = img.rect
            if r[0] <= x <= r[2] and r[1] <= y <= r[3]:
                return img
        return None
