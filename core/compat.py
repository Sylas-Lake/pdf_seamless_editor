"""PyMuPDF 导入兼容。全仓库只在这里 try/except。"""
from __future__ import annotations

try:
    import pymupdf as fitz
except ImportError:  # 旧包名
    import fitz  # type: ignore

__all__ = ["fitz"]
