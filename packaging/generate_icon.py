"""生成 Windows 图标：深色工作台里一张纸 + 蓝色文本框选中。"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_ICO = os.path.join(HERE, "app.ico")
OUT_PNG = os.path.join(HERE, "app.png")

BG = (44, 47, 52, 255)
PAGE = (236, 232, 224, 255)
FOLD = (214, 208, 196, 255)
LINE = (90, 86, 80, 255)
BOX = (76, 158, 235, 255)
BOX_FILL = (76, 158, 235, 36)
CHROME = (50, 53, 58, 255)


def _draw(size: int) -> Image.Image:
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    m = max(1, size // 16)
    # 圆角底板（任务栏小尺寸时仍能认出深色块）
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=size // 6, fill=BG)

    px, py = m * 3, m * 2
    pw, ph = size - px * 2, size - py * 2 - m
    d.rounded_rectangle((px, py, px + pw, py + ph), radius=max(1, size // 18), fill=CHROME)

    # 纸
    left, top = px + m, py + m
    right, bottom = px + pw - m, py + ph - m
    d.rectangle((left, top, right, bottom), fill=PAGE)

    fold = max(3, size // 7)
    d.polygon(
        [(right - fold, top), (right, top + fold), (right - fold, top + fold)],
        fill=FOLD,
    )
    d.line([(right - fold, top), (right - fold, top + fold), (right, top + fold)],
           fill=LINE, width=max(1, size // 64))

    # 文本行
    lx, rx = left + m * 2, right - fold - m
    gap = max(2, size // 18)
    ly = top + fold + m
    lw = max(1, size // 28)
    for i, frac in enumerate((1.0, 0.82, 0.64)):
        y = ly + i * (lw + gap)
        d.rectangle((lx, y, lx + int((rx - lx) * frac), y + lw), fill=LINE)

    # 选中框（产品本身的交互）
    bx0, by0 = lx - m // 2, ly - m // 2
    bx1, by1 = rx + m // 2, ly + 3 * (lw + gap)
    d.rectangle((bx0, by0, bx1, by1), fill=BOX_FILL, outline=BOX, width=max(2, size // 32))
    return im


def main():
    master = _draw(256)
    master.save(OUT_PNG)
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    master.save(OUT_ICO, format="ICO", sizes=sizes)
    print(OUT_ICO)


if __name__ == "__main__":
    main()
