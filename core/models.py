"""核心数据模型（对应方案文档第三章）。

字符级模型：GlyphNode —— 最小编辑粒度落实到每一个可见字形；
层级语义模型：TextLine —— 基线、方向接近的一组字符（TextRun/Paragraph/BTextBlock
在 MVP 中以“同线同样式 Run + 行”近似表达）。
"""
from dataclasses import dataclass, field, replace


@dataclass
class TextStyle:
    """文本样式（继承自原 PDF span，用于新输入文本的样式继承）。"""
    font_name: str = ""          # 原始 PDF 字体名（可能带子集前缀，如 ABCDEF+SimSun）
    size: float = 11.0           # 字号（PDF 点）
    color: tuple = (0.0, 0.0, 0.0)  # RGB，0..1 浮点
    flags: int = 0               # rawdict span flags（bit1 斜体、bit4 加粗等）

    def copy(self) -> "TextStyle":
        return replace(self)

    @property
    def is_bold(self) -> bool:
        return bool(self.flags & 16)

    @property
    def is_italic(self) -> bool:
        return bool(self.flags & 2)


@dataclass
class GlyphNode:
    """可见字形（grapheme 粒度，组合字符并入前一字符）。"""
    char: str                    # 完整字素（可含组合符号）
    bbox: tuple                  # (x0, y0, x1, y1)
    origin: tuple                # (x, y) 基线起始点
    style: TextStyle = None      # 所属 span 样式（共享引用）


@dataclass
class TextLine:
    """文本行：基线、方向接近的一组字形。"""
    index: int                   # 页内读取顺序索引
    bbox: tuple                  # 行包围盒
    baseline: float              # 主基线 y
    glyphs: list = field(default_factory=list)
    direction: tuple = (1.0, 0.0)  # 行方向 (cos, sin)，非水平行 MVP 不可编辑

    @property
    def horizontal(self) -> bool:
        return abs(self.direction[0]) > 0.98

    def text(self) -> str:
        return "".join(g.char for g in self.glyphs)


@dataclass
class ImageObject:
    """页面图片对象（含实例位置与放置角度）。"""
    xref: int
    rect: tuple                  # (x0, y0, x1, y1)
    width: int = 0
    height: int = 0
    deg: float = 0.0             # 由放置矩阵推断的旋转角

    def copy(self) -> "ImageObject":
        return ImageObject(self.xref, self.rect, self.width, self.height, self.deg)


@dataclass
class PageModel:
    """页面视觉语义模型（可编辑中间文档模型）。"""
    page_index: int
    rect: tuple                  # 页面矩形
    rotation: int = 0
    lines: list = field(default_factory=list)
    images: list = field(default_factory=list)
    scanned: bool = False        # 整页图像（扫描件）
    scanned_note: str = ""
