"""核心层共用类型别名。"""
from __future__ import annotations

from typing import Any

# PDF 轴对齐矩形：(x0, y0, x1, y1)
PdfRect = tuple[float, float, float, float]
Point = tuple[float, float]
Rgb = tuple[float, float, float]
# 页面快照（见 snapshot.capture_page_state）
PageState = dict[str, Any]
