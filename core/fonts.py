"""字体恢复与塑形支撑（对应方案第五章）。

处理策略：
  A. 复用原 PDF 嵌入字体子集（新字符全部在子集内 → 绿色原生编辑）；
  A' 原字体子集缺字 → 从系统查找同名完整字体（扩展原字体）；
  C. 字体替代 → PyMuPDF 内置 CJK / Base14 字体（近似字重与衬线匹配）。

对名必须归一：span.font 是 MicrosoftYaHei / ArialMT，
get_fonts 却是 Microsoft YaHei Regular / Arial Regular。对不上就会整框掉到 china-ss/helv。
"""
import os
import re
import unicodedata

from .compat import fitz

BUILTIN_KEYS = {"helv", "tiro", "cour", "symb", "zadb",
                "china-t", "china-s", "china-ts", "china-ss",
                "japan", "japan-s", "korea", "korea-s"}


def _is_mark(ch: str) -> bool:
    return unicodedata.combining(ch) != 0 or ch in ("\u200d", "\u200b", "\ufe0f")


def strip_subset_prefix(name: str) -> str:
    """去掉字体子集前缀：ABCDEF+SimSun → SimSun。"""
    if not name:
        return ""
    return name.split("+", 1)[-1] if "+" in name else name


def unescape_pdf_name(name: str) -> str:
    """还原 PDF 名里的 #XX 转义。"""
    if not name:
        return ""
    return re.sub(r"#([0-9A-Fa-f]{2})",
                  lambda m: chr(int(m.group(1), 16)), name)


def repair_font_name(name: str) -> str:
    """修复错误解码的字体名（GBK/UTF-8 被当成 Latin-1 等）。"""
    s = unescape_pdf_name(name or "").strip()
    if not s:
        return ""
    if any("\u4e00" <= c <= "\u9fff" for c in s):
        return s
    high = [c for c in s if ord(c) >= 0x80]
    if not high:
        return s
    try:
        raw = s.encode("latin-1")
    except UnicodeEncodeError:
        return s
    for enc in ("utf-8", "gb18030", "gbk", "big5"):
        try:
            got = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        if got != s and any("\u4e00" <= c <= "\u9fff" for c in got):
            return got.strip()
    return s


# PostScript/TrueType 后缀：ArialMT、TimesNewRomanPSMT、Arial-BoldMT
_PS_SUFFIX_RE = re.compile(
    r"(?i)(?:psmt|ps-?bolditalicmt|ps-?boldmt|ps-?italicmt|"
    r"bolditalicmt|boldmt|italicmt|mt)$"
)
_STYLE_WORDS = {
    "regular", "normal", "italic", "oblique", "bold", "light",
    "medium", "demibold", "semibold", "black", "thin", "condensed",
    "extended", "narrow", "ultralight", "ultrabold", "heavy",
}


def font_identity_key(name: str) -> str:
    """跨命名体系的字体身份：MicrosoftYaHei ≡ Microsoft YaHei Regular。"""
    s = strip_subset_prefix(name or "")
    if not s:
        return ""
    s = s.split(",", 1)[0].strip()
    s = _PS_SUFFIX_RE.sub("", s)
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s)
    parts = re.split(r"[^A-Za-z0-9\u4e00-\u9fff]+", s)
    out = []
    for p in parts:
        if not p:
            continue
        pl = p.lower()
        if pl in _STYLE_WORDS:
            continue
        out.append(pl)
    return "".join(out)


def fonts_same_face(a: str, b: str) -> bool:
    ka, kb = font_identity_key(a), font_identity_key(b)
    return bool(ka) and ka == kb


def _face_style(name: str, flags: int = 0) -> tuple[bool, bool, bool]:
    n = (name or "").lower()
    bold = bool(flags & 16) or any(
        w in n for w in ("bold", "black", "heavy", "semibold", "demibold"))
    italic = bool(flags & 2) or ("italic" in n) or ("oblique" in n)
    light = ("light" in n) and ("ultralight" in n or "light" in n) and "bold" not in n
    return bold, italic, light


_FAMILY_LABELS = {
    "simsun": "宋体",
    "nsimsun": "新宋体",
    "simhei": "黑体",
    "heiti": "黑体",
    "microsoftyahei": "微软雅黑",
    "microsoftyahehui": "微软雅黑 UI",
    "kaiti": "楷体",
    "simkai": "楷体",
    "fangsong": "仿宋",
    "simfang": "仿宋",
    "dengxian": "等线",
    "sourcehansans": "思源黑体",
    "sourcehansanssc": "思源黑体",
    "sourcehanserif": "思源宋体",
    "sourcehanserifsc": "思源宋体",
    "notosanscjk": "Noto Sans CJK",
    "notosanssc": "Noto Sans SC",
    "notoserifcjk": "Noto Serif CJK",
    "arial": "Arial",
    "timesnewroman": "Times New Roman",
    "helvetica": "Helvetica",
    "calibri": "Calibri",
    "stsong": "华文宋体",
    "stheiti": "华文黑体",
    "stkaiti": "华文楷体",
    "stfangsong": "华文仿宋",
    "mingliu": "细明体",
    "pmingliu": "新细明体",
    "microsoftjhenghei": "微软正黑体",
}


def font_display_label(name: str) -> str:
    """给属性栏看的字体名：修复乱码、映射常用族名、标出粗/斜。"""
    s = repair_font_name(name)
    if not s:
        return ""
    ident = font_identity_key(s)
    family = _FAMILY_LABELS.get(ident)
    if not family:
        family = re.sub(r"(?i)[\s\-_]?(regular|normal)$", "", s).strip() or s
        family = re.sub(r"\s+", " ", family)
    bold, italic, light = _face_style(s, 0)
    if bold and italic:
        return f"{family} · 粗斜体"
    if bold:
        return f"{family} · 粗体"
    if italic:
        return f"{family} · 斜体"
    if light:
        return f"{family} · 细体"
    return family


def _font_buffer_from_xref(doc, xref):
    try:
        ef = doc.extract_font(xref)
        if isinstance(ef, dict):
            return ef.get("image") or ef.get("content")
        if isinstance(ef, (tuple, list)) and len(ef) >= 4:
            return ef[3]
    except Exception:
        return None
    return None


def page_font_catalog(page) -> dict:
    """span/资源字体名 → {display, flags}。优先读嵌入 TTF 的真实名称与粗斜体。"""
    cat = {}
    doc = getattr(page, "parent", None)
    try:
        fonts = page.get_fonts(full=True)
    except Exception:
        return cat
    for info in fonts:
        xref = info[0] if info else 0
        basefont = info[3] if len(info) > 3 else ""
        resname = info[4] if len(info) > 4 else ""
        extra = 0
        true_name = ""
        buf = _font_buffer_from_xref(doc, xref) if doc is not None and xref else None
        if buf:
            try:
                f = fitz.Font(fontbuffer=buf)
                true_name = getattr(f, "name", "") or ""
                if getattr(f, "is_bold", False):
                    extra |= 16
                if getattr(f, "is_italic", False):
                    extra |= 2
                flags = getattr(f, "flags", None)
                if isinstance(flags, dict):
                    if flags.get("bold") or flags.get("fake-bold"):
                        extra |= 16
                    if flags.get("italic") or flags.get("fake-italic"):
                        extra |= 2
            except Exception:
                pass
        raw = true_name or basefont or resname
        nb, ni, _nl = _face_style(raw, extra)
        if nb:
            extra |= 16
        if ni:
            extra |= 2
        rec = {"display": font_display_label(raw), "flags": extra,
               "basefont": basefont}
        for key in (basefont, resname, strip_subset_prefix(basefont),
                    strip_subset_prefix(resname), true_name):
            if key and key not in cat:
                cat[key] = rec
    return cat


def lookup_font_info(cat: dict, pdf_name: str) -> dict:
    """按 PDF span 字体名取目录记录。"""
    if not pdf_name:
        return {}
    rec = cat.get(pdf_name) or cat.get(strip_subset_prefix(pdf_name))
    if rec:
        return rec
    for key, rec in cat.items():
        if fonts_same_face(key, pdf_name):
            return rec
    return {}


def _has_glyph(font, ch: str) -> bool:
    try:
        return font.has_glyph(ord(ch)) > 0
    except Exception:
        try:
            return font.has_glyph(ch) > 0
        except Exception:
            return False


def covers(font, text: str) -> bool:
    """字体是否覆盖文本全部基字符（组合符号不计）。"""
    if not text:
        return True
    return all(_has_glyph(font, c) for c in text if not _is_mark(c))


def _has_cjk(text: str) -> bool:
    for c in text:
        o = ord(c)
        if (0x2E80 <= o <= 0x9FFF or 0xAC00 <= o <= 0xD7AF
                or 0xF900 <= o <= 0xFAFF or 0xFF00 <= o <= 0xFFEF):
            return True
    return False


_LATIN_KEYS = {"helv", "tiro", "cour", "symb", "zadb"}
_CJK_IDENT = {
    "simsun", "nsimsun", "simhei", "microsoftyahei", "kaiti", "fangsong",
    "dengxian", "sourcehansans", "sourcehanserif", "notosanscjk",
    "notoserifcjk",
}
_CJK_NAME_HINTS = (
    "simsun", "nsimsun", "simhei", "simkai", "simfang", "yahei", "msyh",
    "sourcehan", "noto", "cjk", "dengxian", "songti", "heiti", "kaiti",
    "fangsong", "china-", "japan", "korea", "mingliu", "pmingliu",
    "msmincho", "msgothic", "malgun", "stkaiti", "stsong", "stheiti",
)


def _name_looks_cjk(name: str) -> bool:
    n = name or ""
    if any("\u4e00" <= c <= "\u9fff" for c in n):
        return True
    ident = font_identity_key(n)
    if ident in _CJK_IDENT:
        return True
    low = n.lower()
    return any(h in low for h in _CJK_NAME_HINTS)


def _wants_cjk(style, text: str) -> bool:
    return _has_cjk(text) or _name_looks_cjk(getattr(style, "font_name", "") or "")


def cjk_builtin_key(style) -> str:
    """insert_text 可用的内置 CJK fontname（衬线用 china-s）。"""
    base = strip_subset_prefix(getattr(style, "font_name", "") or "")
    flags = getattr(style, "flags", 0) or 0
    serif = bool(flags & 4)
    if not serif and base:
        b = base.lower()
        serif = b.endswith(("simsun", "song", "serif", "times", "宋体",
                            "ming", "mincho")) or "song" in b
    return "china-s" if serif else "china-ss"


def font_can_insert(rf, text: str) -> bool:
    """该解析结果能否用 insert_text 画 text（有 Unicode 字形，且中文不用 Base14）。"""
    if rf is None or not text:
        return False
    if _has_cjk(text) and getattr(rf, "key", "") in _LATIN_KEYS:
        return False
    font = getattr(rf, "font", None)
    if font is None:
        return False
    return covers(font, text)


# ------------------------------------------------------- Windows 系统字体查找

_FONT_DIR = None
_REG_FONTS = None
_ALIAS = {
    "simsun": "simsun.ttc", "nsimsun": "simsun.ttc", "宋体": "simsun.ttc",
    "新宋体": "simsun.ttc", "microsoft yahei": "msyh.ttc", "微软雅黑": "msyh.ttc",
    "simhei": "simhei.ttf", "黑体": "simhei.ttf", "kaiti": "simkai.ttf",
    "楷体": "simkai.ttf", "simkai": "simkai.ttf", "fangsong": "simfang.ttf",
    "仿宋": "simfang.ttf", "simfang": "simfang.ttf", "dengxian": "deng.ttf",
    "等线": "deng.ttf", "deng": "deng.ttf", "微软雅黑 light": "msyhl.ttc",
    "arial": "arial.ttf", "calibri": "calibri.ttf", "times new roman": "times.ttf",
    "华文宋体": "STSONG.TTF", "华文黑体": "STHEITI.TTF", "华文楷体": "STKAITI.TTF",
    "华文仿宋": "STFANGSO.TTF", "微软雅黑 bold": "msyhbd.ttc",
}

# ident → (regular, bold, italic, bolditalic, light)
_FACE_FILES = {
    "arial": ("arial.ttf", "arialbd.ttf", "ariali.ttf", "arialbi.ttf", None),
    "timesnewroman": ("times.ttf", "timesbd.ttf", "timesi.ttf", "timesbi.ttf", None),
    "calibri": ("calibri.ttf", "calibrib.ttf", "calibrii.ttf", "calibriz.ttf", None),
    "microsoftyahei": ("msyh.ttc", "msyhbd.ttc", None, None, "msyhl.ttc"),
    "simsun": ("simsun.ttc", None, None, None, None),
    "nsimsun": ("simsun.ttc", None, None, None, None),
    "simhei": ("simhei.ttf", None, None, None, None),
    "simkai": ("simkai.ttf", None, None, None, None),
    "simfang": ("simfang.ttf", None, None, None, None),
}


def _font_dir() -> str:
    global _FONT_DIR
    if _FONT_DIR is None:
        _FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    return _FONT_DIR


def _registry_fonts() -> dict:
    """注册表字体名 → 文件名映射（惰性加载一次）。"""
    global _REG_FONTS
    if _REG_FONTS is not None:
        return _REG_FONTS
    _REG_FONTS = {}
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts")
        i = 0
        while True:
            try:
                name, value, _t = winreg.EnumValue(key, i)
                i += 1
            except OSError:
                break
            if isinstance(value, str):
                _REG_FONTS[name.lower()] = value
        winreg.CloseKey(key)
    except Exception:
        pass
    return _REG_FONTS


def system_font_candidates(family: str, *, bold: bool = False,
                           italic: bool = False, light: bool = False) -> list:
    """按族名查找系统字体文件候选（策略 A'：扩展原字体）。"""
    fam = (family or "").strip()
    if not fam:
        return []
    fam_l = fam.lower()
    ident = font_identity_key(fam)
    out = []
    d = _font_dir()

    def _add(path):
        if path and os.path.isfile(path) and path not in out:
            out.append(path)

    def _add_name(fn):
        if fn:
            _add(os.path.join(d, fn))

    # 0) 按字重优先（Arial-BoldMT 不应先拿到 arial.ttf）
    faces = _FACE_FILES.get(ident)
    if faces:
        regular, fbold, fitalic, fbi, flight = faces
        if light and flight:
            _add_name(flight)
        if bold and italic and fbi:
            _add_name(fbi)
        if bold and fbold:
            _add_name(fbold)
        if italic and fitalic:
            _add_name(fitalic)
        _add_name(regular)

    # 1) 别名：原文、小写、归一身份（ArialMT / MicrosoftYaHei）
    if fam_l in _ALIAS:
        _add(os.path.join(d, _ALIAS[fam_l]))
    if fam in _ALIAS:
        _add(os.path.join(d, _ALIAS[fam]))
    if ident:
        for ak, fn in _ALIAS.items():
            if font_identity_key(ak) == ident:
                _add(os.path.join(d, fn))
        if ident == "helvetica":
            _add(os.path.join(d, "arial.ttf"))
    # 2) 注册表：同样按身份键，避免 Arial 误配 Arial Unicode
    reg = _registry_fonts()
    for name, fn in reg.items():
        nn = name.replace("(truetype)", "").replace("(opentype)", "").strip()
        if not nn:
            continue
        if ident and font_identity_key(nn) == ident:
            _add(fn if os.path.isabs(fn) else os.path.join(d, fn))
        elif nn == fam_l or nn.startswith(fam_l + " "):
            _add(fn if os.path.isabs(fn) else os.path.join(d, fn))
    # 3) 目录：仅在还没有命中时用文件名前缀兜底
    if not out:
        key = ident or fam_l.replace(" ", "")
        if len(key) >= 4:
            try:
                for fn in os.listdir(d):
                    fl = fn.lower()
                    stem = os.path.splitext(fl)[0]
                    if fl.endswith((".ttf", ".ttc", ".otf")) and (
                            stem == key or font_identity_key(stem) == ident):
                        _add(os.path.join(d, fn))
            except Exception:
                pass
    return out[:8]


# ---------------------------------------------------------------- 解析器

class ResolvedFont:
    """一次字体解析的结果。"""
    __slots__ = ("key", "font", "is_original", "source", "coverage_ok")

    def __init__(self, key, font, is_original, source, coverage_ok=True):
        self.key = key              # insert_text 使用的 fontname
        self.font = font            # fitz.Font（测量/覆盖检查）
        self.is_original = is_original
        self.source = source        # 来源说明（保真原因展示）
        self.coverage_ok = coverage_ok


class FontResolver:
    """文档级字体解析器：原始子集 → 系统完整字体 → 内置替代。"""

    def __init__(self, doc):
        self.doc = doc
        self._reg = {}          # key -> {"fontbuffer":..} | {"fontfile":..}
        self._by_buf = {}      # bytes hash -> key（去重）
        self._by_file = {}     # path -> key
        self._sys_cache = {}   # path -> fitz.Font | None
        self._page_keys = {}   # page.number -> set(key)（已注册到页面资源）
        self._xref_cache = {}  # xref -> (buf, fitz.Font | None)

    # -- 注册 --
    def _register_buffer(self, buf: bytes) -> str:
        h = hash(buf)
        if h in self._by_buf:
            return self._by_buf[h]
        key = "EF%03d" % (len(self._reg) + 1)
        self._reg[key] = {"fontbuffer": buf}
        self._by_buf[h] = key
        return key

    def _register_file(self, path: str) -> str:
        if path in self._by_file:
            return self._by_file[path]
        key = "EF%03d" % (len(self._reg) + 1)
        self._reg[key] = {"fontfile": path}
        self._by_file[path] = key
        return key

    def invalidate_page(self, page_index: int):
        """页面字节被还原后调用：缓存的 fontname 可能已不在页面资源里。"""
        self._page_keys.pop(page_index, None)

    def _font_on_page(self, page, key: str) -> bool:
        try:
            for info in page.get_fonts(full=True):
                if len(info) > 4 and info[4] == key:
                    return True
        except Exception:
            return False
        return False

    def ensure_page_font(self, page, rf: ResolvedFont) -> str:
        """把解析出的字体注册进页面资源（幂等），返回可用 fontname。

        不以内存缓存为准：restore 会撤掉页面字体，必须看见资源里真有该名才跳过。
        """
        if rf.key in BUILTIN_KEYS:
            return rf.key
        spec = self._reg.get(rf.key)
        if not spec:
            return "helv"
        done = self._page_keys.setdefault(page.number, set())
        if rf.key in done and self._font_on_page(page, rf.key):
            return rf.key
        page.insert_font(fontname=rf.key, **spec)
        done.add(rf.key)
        return rf.key

    def insert_fontname(self, page, rf, text, style) -> str:
        """页面上真正用于 insert_text 的 fontname。中文绝不落到 helv。"""
        key = self.ensure_page_font(page, rf)
        if _has_cjk(text) and (key in _LATIN_KEYS or not font_can_insert(rf, text)):
            return cjk_builtin_key(style)
        return key

    def _extracted(self, xref):
        """按 xref 缓存 extract_font，避免每次按键解 10MB+ 子集。"""
        if xref in self._xref_cache:
            return self._xref_cache[xref]
        buf = None
        try:
            ef = self.doc.extract_font(xref)
            if isinstance(ef, dict):
                buf = ef.get("image") or ef.get("content")
            elif isinstance(ef, (tuple, list)) and len(ef) >= 4:
                buf = ef[3]
        except Exception:
            buf = None
        if not buf:
            self._xref_cache[xref] = (None, None)
            return None, None
        f = self._load_buffer(buf)
        self._xref_cache[xref] = (buf, f)
        return buf, f

    def _page_font_hits(self, page, style) -> list:
        """页面字体按身份键匹配，字重/斜体更近的排前面。"""
        want = font_identity_key(style.font_name or "")
        base = strip_subset_prefix(style.font_name or "")
        if not want and not base:
            return []
        try:
            fonts = page.get_fonts(full=True)
        except Exception:
            return []
        wb, wi, _wl = _face_style(style.font_name or "", getattr(style, "flags", 0) or 0)
        scored = []
        for finfo in fonts:
            basefont = finfo[3] if len(finfo) > 3 else ""
            resname = finfo[4] if len(finfo) > 4 else ""
            exact = (basefont == (style.font_name or "")
                     or (base and strip_subset_prefix(basefont) == base)
                     or (base and resname == base))
            ident = (want and (
                fonts_same_face(basefont, style.font_name)
                or fonts_same_face(resname, style.font_name)
                or fonts_same_face(strip_subset_prefix(basefont), style.font_name)))
            builtin = resname in BUILTIN_KEYS and (
                exact or fonts_same_face(basefont, style.font_name))
            if not (exact or ident or builtin):
                continue
            fb, fi, _fl = _face_style(basefont, 0)
            scored.append(((fb == wb) + (fi == wi), finfo))
        scored.sort(key=lambda x: -x[0])
        return [f for _, f in scored]

    # -- 解析 --
    def resolve(self, page, style, text: str, *, allow_original=True) -> ResolvedFont:
        base = strip_subset_prefix(style.font_name or "")

        # 策略 A：复用原始嵌入字体（子集覆盖检查）
        if allow_original:
            for finfo in self._page_font_hits(page, style):
                resname = finfo[4] if len(finfo) > 4 else ""
                buf, f = self._extracted(finfo[0])
                if buf:
                    if f is not None and covers(f, text):
                        key = self._register_buffer(buf)
                        return ResolvedFont(key, f, True, "复用原始嵌入字体")
                    if not text:
                        # 身份解析：CID 子集可能没有 Unicode cmap，covers 会假阴性
                        key = self._register_buffer(buf)
                        return ResolvedFont(key, f, True, "复用原始嵌入字体",
                                            coverage_ok=f is not None)
                if resname in BUILTIN_KEYS:
                    bf = self._load_buffer(None, resname)
                    if bf is not None and covers(bf, text):
                        return ResolvedFont(resname, bf, True,
                                            "复用原内置字体资源")
                    if not text and bf is not None:
                        return ResolvedFont(resname, bf, True,
                                            "复用原内置字体资源")

        # 策略 A'：系统同名完整字体（扩展原字体）
        if base:
            wb, wi, wl = _face_style(style.font_name or "",
                                     getattr(style, "flags", 0) or 0)
            for path in system_font_candidates(base, bold=wb, italic=wi, light=wl):
                f = self._load_file(path)
                if f is not None and covers(f, text):
                    key = self._register_file(path)
                    return ResolvedFont(key, f, False,
                                        "系统同名完整字体（扩展原字体）")

        # 策略 C：内置替代字体（衬线/无衬线匹配）
        serif = bool(style.flags & 4) if style.flags else base.lower().endswith(("simsun", "song", "serif", "times", "宋体"))
        prefer = ["china-s", "china-ss"] if serif else ["china-ss", "china-s"]
        if not _wants_cjk(style, text):
            prefer = ["helv", "china-ss"]
        for key in prefer:
            f = self._load_buffer(None, key)
            if f is not None and covers(f, text):
                return ResolvedFont(key, f, False, f"内置替代字体 {key}")
        # 覆盖不全也退回首选（保真降级为黄色/警告）
        f = self._load_buffer(None, prefer[0])
        return ResolvedFont(prefer[0], f, False, f"内置替代字体 {prefer[0]}（部分字符缺字形）", coverage_ok=False)

    # -- 字体加载缓存 --
    def _load_buffer(self, buf, builtin=None):
        if builtin:
            try:
                return fitz.Font(builtin)
            except Exception:
                return None
        try:
            f = fitz.Font(fontbuffer=buf)
            if f.glyph_count == 0:
                return None
            return f
        except Exception:
            return None

    def _load_file(self, path):
        if path in self._sys_cache:
            return self._sys_cache[path]
        f = None
        try:
            f = fitz.Font(fontfile=path)
            if f.glyph_count == 0:
                f = None
        except Exception:
            f = None
        self._sys_cache[path] = f
        return f


# ---------------------------------------------------------------- 字体决策器

def original_chars_from_block(block) -> set:
    """框内已出现的 (style.key, char)。"""
    out = set()
    for ln in getattr(block, "lines", []) or []:
        for g in getattr(ln, "glyphs", []) or []:
            if g.style is not None and g.char:
                out.add((g.style.key, g.char))
    return out


class FontOracle:
    """缓冲区编辑的字体决策器：为每个 (样式, 字符) 决定插入字体与 advance。

    优先复用原字体，但 insert_text 必须能编该字。CID 子集没有 Unicode cmap
    时 has_glyph 为假，不能再强行用原字体，否则会落下 .notdef 方块。
    """

    def __init__(self, resolver, page, original_chars=None, blank_chars=None):
        self.resolver = resolver
        self.page = page
        self._style_font = {}    # style.key -> ResolvedFont（原始字体）
        self._char_cache = {}    # (style_key, ch) -> (rf, adv)
        self._adv_cache = {}     # (rf_key, ch, size) -> float
        self._orig_chars = original_chars or set()
        self._blank_chars = blank_chars or set()

    @classmethod
    def from_block(cls, resolver, page, block):
        blank = set()
        for ln in getattr(block, "lines", []) or []:
            for g in getattr(ln, "glyphs", []) or []:
                if g.style is not None and g.char and getattr(g, "missing_ink", False):
                    blank.add((g.style.key, g.char))
        return cls(resolver, page, original_chars_from_block(block), blank)

    def original_font(self, style) -> ResolvedFont:
        """该样式在页面上的原始字体。

        覆盖检查必须用空串：font_name（SimSun / MicrosoftYaHei）往往不在
        CJK 子集里，拿它当 probe 会误判为缺字，整框掉进替代字体。
        单字是否在子集内由 char_font 再查。
        """
        k = style.key
        if k in self._style_font:
            return self._style_font[k]
        rf = self.resolver.resolve(self.page, style, "")
        self._style_font[k] = rf
        return rf

    def char_font(self, style, ch: str) -> ResolvedFont:
        """单字符的插入字体（原字体覆盖检查 → 替代）。"""
        k = (style.key, ch)
        if k in self._char_cache:
            return self._char_cache[k][0]
        rf = self.original_font(style)
        if k in self._blank_chars:
            # cmap 有字但轮廓是空的：has_glyph 为真，必须跳过原子集
            rf = self.resolver.resolve(self.page, style, ch, allow_original=False)
        elif not font_can_insert(rf, ch):
            rf = self.resolver.resolve(self.page, style, ch)
        self._char_cache[k] = (rf, None)
        return rf

    def advance(self, style, ch: str) -> float:
        """单字符 advance（与 PDF 渲染一致：内置 CJK 按全宽）。"""
        k = (style.key, ch)
        if k in self._char_cache and self._char_cache[k][1] is not None:
            return self._char_cache[k][1]
        rf = self.char_font(style, ch)
        if rf is None or rf.font is None:
            adv = style.size
        elif getattr(rf, "key", "") in ("china-t", "china-s", "china-ts",
                                        "china-ss", "japan", "japan-s",
                                        "korea", "korea-s"):
            adv = style.size
        else:
            ak = (rf.key, ch, style.size)
            if ak in self._adv_cache:
                adv = self._adv_cache[ak]
            else:
                try:
                    adv = rf.font.text_length(ch, style.size)
                except Exception:
                    adv = style.size
                self._adv_cache[ak] = adv
        self._char_cache[k] = (self.char_font(style, ch), adv)
        return adv

    def adv_fn(self):
        """BoxBuffer.layout 用的 advance 回调。"""
        def fn(ch, st):
            return self.advance(st, ch)
        return fn
