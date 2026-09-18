"""几何工具：PDF (x0, y0, x1, y1) 与图片手柄缩放。

QRectF 的四参数构造是 (x, y, width, height)，不能直接 * 解包 PDF bbox。
"""


from __future__ import annotations

from .types import PdfRect


def qrect_args(rect: PdfRect) -> tuple[float, float, float, float]:
    """PDF xyxy → QRectF(x, y, w, h) 四元组。"""
    return (rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1])


def resize_rect(rect: PdfRect, mode: str, x: float, y: float, *,
                keep_aspect: bool = False, min_size: float = 8.0) -> PdfRect:
    """按手柄模式缩放轴对齐矩形。

    rect: (x0, y0, x1, y1)
    mode: n/s/e/w/nw/ne/se/sw
    返回新的 (x0, y0, x1, y1)。拖过对边时钳制为 min_size，不翻转矩形。
    """
    x0, y0, x1, y1 = rect
    w0 = max(x1 - x0, 1e-6)
    h0 = max(y1 - y0, 1e-6)
    aspect = w0 / h0
    min_size = max(float(min_size), 1.0)

    if mode in ("e", "w"):
        if mode == "e":
            x1 = max(x, x0 + min_size)
        else:
            x0 = min(x, x1 - min_size)
        if keep_aspect:
            h = (x1 - x0) / aspect
            cy = (y0 + y1) / 2
            y0, y1 = cy - h / 2, cy + h / 2
        return (x0, y0, x1, y1)

    if mode in ("n", "s"):
        if mode == "s":
            y1 = max(y, y0 + min_size)
        else:
            y0 = min(y, y1 - min_size)
        if keep_aspect:
            w = (y1 - y0) * aspect
            cx = (x0 + x1) / 2
            x0, x1 = cx - w / 2, cx + w / 2
        return (x0, y0, x1, y1)

    # 角点：锚在对角
    if mode == "se":
        ax, ay, sx, sy = x0, y0, 1.0, 1.0
    elif mode == "sw":
        ax, ay, sx, sy = x1, y0, -1.0, 1.0
    elif mode == "ne":
        ax, ay, sx, sy = x0, y1, 1.0, -1.0
    elif mode == "nw":
        ax, ay, sx, sy = x1, y1, -1.0, -1.0
    else:
        return rect

    w = max((x - ax) * sx, min_size)
    h = max((y - ay) * sy, min_size)
    if keep_aspect:
        h_from_w = w / aspect
        w_from_h = h * aspect
        if h_from_w >= h:
            h = max(h_from_w, min_size)
            w = h * aspect
        else:
            w = max(w_from_h, min_size)
            h = w / aspect
        if w < min_size:
            w = min_size
            h = w / aspect
        if h < min_size:
            h = min_size
            w = h * aspect

    if sx > 0:
        x0, x1 = ax, ax + w
    else:
        x0, x1 = ax - w, ax
    if sy > 0:
        y0, y1 = ay, ay + h
    else:
        y0, y1 = ay - h, ay
    return (x0, y0, x1, y1)
