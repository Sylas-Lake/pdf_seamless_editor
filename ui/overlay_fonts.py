"""覆盖层字体：仅用于输入法预编辑串。正文由 MuPDF 画在页面位图上。"""
import hashlib

from PySide6.QtGui import QFont, QFontDatabase

from core.compat import fitz

from core.fonts import ResolvedFont

_family_cache = {}      # hash(source) -> family or None
_default_cjk = None
_default_latin = None


def _register_bytes(buf: bytes) -> str:
    fid = QFontDatabase.addApplicationFontFromData(buf)
    if fid < 0:
        return ""
    fams = QFontDatabase.applicationFontFamilies(fid)
    return fams[0] if fams else ""


def _register_file(path: str) -> str:
    fid = QFontDatabase.addApplicationFont(path)
    if fid < 0:
        return ""
    fams = QFontDatabase.applicationFontFamilies(fid)
    return fams[0] if fams else ""


def _builtin_buffer(key: str):
    """内置 CJK 字体的二进制（fitz.Font.buffer）。"""
    try:
        f = fitz.Font(key)
        return getattr(f, "buffer", None)
    except Exception:
        return None


def family_for_rf(rf: ResolvedFont) -> str:
    """ResolvedFont → Qt 字体族名（无法获取时返回空串）。"""
    if rf is None:
        return ""
    if getattr(rf, "key", "") in ("helv",):
        return "Arial"
    if getattr(rf, "key", "") in ("tiro",):
        return "Times New Roman"
    if getattr(rf, "key", "") in ("cour",):
        return "Courier New"
    # 来源标记：内置 / 缓冲 / 系统文件
    src = getattr(rf, "source", "")
    try:
        if rf.font is not None and hasattr(rf.font, "buffer"):
            buf = rf.font.buffer
            if buf:
                h = hashlib.md5(buf).hexdigest()
                if h not in _family_cache:
                    _family_cache[h] = _register_bytes(buf) or None
                if _family_cache[h]:
                    return _family_cache[h]
    except Exception:
        pass
    if "内置" in src:
        for key in ("china-s", "china-ss"):
            buf = _builtin_buffer(key)
            if buf:
                h = "builtin:" + key
                if h not in _family_cache:
                    _family_cache[h] = _register_bytes(buf) or None
                if _family_cache[h]:
                    return _family_cache[h]
    return ""


def fallback_family(style) -> str:
    """兜底字体族（CJK 优先微软雅黑，其他 Arial）。"""
    name = " ".join([
        (style.font_name or "") if style else "",
        getattr(style, "display_name", "") or "",
    ])
    low = name.lower()
    if any(k in low for k in ("hei", "yahei", "sans", "simhei", "gothic")):
        return "Microsoft YaHei"
    return ""


def qfont_for_style(rf: ResolvedFont, style, zoom: float = 1.0) -> QFont:
    """构造覆盖层 QFont（像素尺寸 = PDF 字号 × 缩放，场景单位 1:1）。"""
    size = max(4.0, float(style.size if style else 12.0) * max(zoom, 0.01))
    fam = family_for_rf(rf)
    if not fam:
        fam = fallback_family(style) or "Microsoft YaHei"
    f = QFont(fam)
    f.setPixelSize(max(1, int(round(size))))
    if style is not None and style.is_bold:
        f.setBold(True)
    if style is not None and style.is_italic:
        f.setItalic(True)
    return f
