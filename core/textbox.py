"""文本框编辑缓冲（类 PPT 编辑模型）。

- 以"文本框"（TextBlock）为编辑单元；
- 框内编辑只修改内存缓冲（BoxBuffer），光标/选区/输入法全部自然工作；
- 布局（断行 + 基线）由 advance 度量回调驱动，与 PDF 提交度量一致；
- 提交时整体重建（一次 redact + 按行插入），撤销通过页面快照字节级恢复。
"""
from dataclasses import dataclass

from .extractor import char_kind
from .fonts import set_style_bold, set_style_italic
from .models import TextBlock, TextStyle


@dataclass
class VisualLine:
    """布局产物：一个可视行（框内某个硬行的片段）。"""
    hard_idx: int          # 所属硬行
    start: int             # 片段在硬行内的起始偏移
    end: int               # 结束偏移（不含）
    x: float               # 行起点 x
    baseline: float        # 基线 y
    width: float = 0.0


class BoxBuffer:
    """可编辑文本缓冲（硬行 + 布局）。"""

    def __init__(self, block: TextBlock = None):
        self.hard_lines = []      # [[(char, style), ...]]
        self.box_left = 0.0
        self.width = 20.0
        self.min_width = 20.0
        self.auto_width = True    # 随内容变宽；左右拖动手柄后锁定并换行
        self.first_baseline = 0.0
        self.line_height = 14.0
        self.visual = []          # 布局结果 [VisualLine]
        self._origin_text = ""
        self._orig_visual_count = 0
        self._adv_fn = None
        self._src_origin = []     # 与 hard_lines 平行：原始 PDF origin 或 None
        self._src_id = []         # 与 hard_lines 平行：(原行, 原下标) 或 None
        self._glyph_pos = []      # 布局后每字 (x, baseline)
        self.overflow = "shrink"  # shrink | keep
        self.fit_scale = 1.0
        self.overflowed = False
        self.tracking = 0.0
        self._cap_height = 20.0
        self._style_dirty = False
        if block is not None:
            self._from_block(block)

    # ------------------------------------------------ 构造
    def _from_block(self, block: TextBlock):
        for li, ln in enumerate(block.lines):
            self.hard_lines.append([(g.char, g.style) for g in ln.glyphs])
            self._src_origin.append([tuple(g.origin) for g in ln.glyphs])
            self._src_id.append([(li, gi) for gi in range(len(ln.glyphs))])
        if not self.hard_lines:
            self.hard_lines = [[]]
            self._src_origin = [[]]
            self._src_id = [[]]
        b = block.bbox
        self.box_left = min(l.glyphs[0].origin[0] for l in block.lines
                            if l.glyphs) if block.lines and block.lines[0].glyphs else b[0]
        self.min_width = max(20.0, block.dominant_style().size)
        self.width = max(self.min_width, b[2] - self.box_left)
        self.auto_width = True
        self.first_baseline = block.first_baseline
        # 行距：原始行基线差均值，退化用主导字号 × 1.35
        bld = [l.baseline for l in block.lines if l.glyphs]
        if len(bld) >= 2:
            self.line_height = (bld[-1] - bld[0]) / (len(bld) - 1)
        else:
            self.line_height = block.dominant_style().size * 1.35
        if self.line_height <= 0.5:
            self.line_height = block.dominant_style().size * 1.35
        self._cap_height = max(b[3] - b[1], self.line_height)
        self._origin_text = self.text()
        self._style_dirty = False
        self.layout()
        self._orig_visual_count = len(self.visual)

    def _orig_split(self, hi: int, off: int, dy: float):
        """硬换行：后半段带走 origin，并下移基线。"""
        orest = self._src_origin[hi][off:]
        irest = self._src_id[hi][off:]
        del self._src_origin[hi][off:]
        del self._src_id[hi][off:]
        if dy:
            orest = [None if p is None else (p[0], p[1] + dy) for p in orest]
        self._src_origin.insert(hi + 1, orest)
        self._src_id.insert(hi + 1, irest)

    def _nudge_origins_y(self, line, dy: float):
        if not dy:
            return
        for i, p in enumerate(line):
            if p is not None:
                line[i] = (p[0], p[1] + dy)

    def _absorb_line(self, dst_hi: int, src_hi: int):
        """把 src 硬行并入 dst，并把 src 的 origin 上移一行，抵消 split 时的下移。"""
        self._nudge_origins_y(self._src_origin[src_hi], -self.line_height)
        self.hard_lines[dst_hi].extend(self.hard_lines[src_hi])
        self._src_origin[dst_hi].extend(self._src_origin[src_hi])
        self._src_id[dst_hi].extend(self._src_id[src_hi])
        del self.hard_lines[src_hi]
        del self._src_origin[src_hi]
        del self._src_id[src_hi]

    # ------------------------------------------------ 文本
    def text(self) -> str:
        return "\n".join("".join(c for c, _s in hl) for hl in self.hard_lines)

    @property
    def changed(self) -> bool:
        return self.text() != self._origin_text or self._style_dirty

    def char_count(self) -> int:
        return sum(len(hl) for hl in self.hard_lines)

    def set_measure(self, adv_fn):
        """绑定与 PDF 提交一致的 advance 回调，并立即重排。"""
        self._adv_fn = adv_fn
        self.layout()

    def _measure(self, adv_fn=None):
        if adv_fn is not None:
            return adv_fn
        if self._adv_fn is not None:
            return self._adv_fn

        def approx(ch, st):
            o = ord(ch)
            if 0x2E80 <= o <= 0x9FFF or 0xFF00 <= o <= 0xFFEF or 0x3000 <= o <= 0x303F:
                return st.size
            return st.size * 0.5
        return approx

    def _scaled_measure(self, adv_fn=None):
        adv_fn = self._measure(adv_fn)
        k = self.fit_scale if self.fit_scale > 0 else 1.0
        return self._scale_adv(adv_fn, k)

    @staticmethod
    def _scale_adv(adv_fn, k):
        if abs(k - 1.0) < 1e-6:
            return adv_fn

        def scaled(ch, st, f=adv_fn, factor=k):
            return f(ch, st) * factor
        return scaled

    # ------------------------------------------------ 布局
    def _place_origin_line(self, hi, hl, default_bl, adv_fn, scale=1.0, tracking=0.0):
        """不换行时按原始 origin 摆字：插入后移、删除合拢、TJ 间距保留。"""
        n = len(hl)
        if n == 0:
            return []
        origins = list(self._src_origin[hi]) if hi < len(self._src_origin) else []
        ids = list(self._src_id[hi]) if hi < len(self._src_id) else []

        def _sx(x, y):
            if scale == 1.0:
                return x, y
            return (self.box_left + (x - self.box_left) * scale,
                    self.first_baseline + (y - self.first_baseline) * scale)

        if len(origins) != n or len(ids) != n:
            pos, x = [], self.box_left
            for ch, st in hl:
                pos.append((x, default_bl))
                x += adv_fn(ch, st) + tracking
            return pos
        anchor_x, anchor_bl = self.box_left, default_bl
        for src in origins:
            if src is not None:
                anchor_x, anchor_bl = _sx(src[0], src[1])
                break
        pos = [None] * n
        shift = 0.0
        prev_id = None
        prev_end_x = None
        bl = anchor_bl
        for i, (ch, st) in enumerate(hl):
            w = adv_fn(ch, st)
            src = origins[i]
            gid = ids[i]
            if src is not None and gid is not None:
                x0, y0 = _sx(src[0], src[1])
                consecutive = (prev_id is not None
                               and gid[0] == prev_id[0]
                               and gid[1] == prev_id[1] + 1)
                if consecutive:
                    x, bl = x0 + shift, y0
                elif prev_end_x is None:
                    x, bl = x0 + shift, y0
                else:
                    x, bl = prev_end_x, y0
                    shift = x - x0
                pos[i] = (x, bl)
                prev_id = gid
                prev_end_x = x + w
            else:
                if prev_end_x is None:
                    x, bl = anchor_x, anchor_bl
                else:
                    x = prev_end_x + tracking
                pos[i] = (x, bl)
                prev_end_x = x + w
                shift += w + tracking
        return pos

    def _estimate_tracking(self, adv_fn):
        """原相邻字形 origin 差 − 字体 advance，即 Tc 一类额外字距。"""
        gaps = []
        for hi, hl in enumerate(self.hard_lines):
            origins = self._src_origin[hi] if hi < len(self._src_origin) else []
            ids = self._src_id[hi] if hi < len(self._src_id) else []
            for i in range(len(hl) - 1):
                if i + 1 >= len(origins) or origins[i] is None or origins[i + 1] is None:
                    continue
                if not ids or i + 1 >= len(ids) or ids[i] is None or ids[i + 1] is None:
                    continue
                if ids[i][0] != ids[i + 1][0] or ids[i][1] + 1 != ids[i + 1][1]:
                    continue
                gap = origins[i + 1][0] - origins[i][0] - adv_fn(*hl[i])
                if abs(gap) < 5.0:
                    gaps.append(gap)
        if not gaps:
            return 0.0
        gaps.sort()
        med = gaps[len(gaps) // 2]
        return med if abs(med) >= 0.05 else 0.0

    def _natural_width(self, adv_fn):
        widest = 0.0
        for hi, hl in enumerate(self.hard_lines):
            if not hl:
                continue
            pos = self._place_origin_line(hi, hl, self.first_baseline, adv_fn)
            last = pos[-1][0] + adv_fn(*hl[-1])
            widest = max(widest, last - self.box_left)
        return widest

    def _layout_origin(self, adv_fn, scale):
        scaled = self._scale_adv(adv_fn, scale)
        track = self.tracking * scale
        self.visual = []
        self._glyph_pos = [[] for _ in self.hard_lines]
        baseline = self.first_baseline
        max_w = 0.0
        lh = self.line_height * scale
        for hi, hl in enumerate(self.hard_lines):
            pos = self._place_origin_line(hi, hl, baseline, scaled, scale, track)
            self._glyph_pos[hi] = pos
            if not hl:
                self.visual.append(VisualLine(hi, 0, 0, self.box_left, baseline))
            else:
                x0, bl0 = pos[0]
                last_x = pos[-1][0] + scaled(*hl[-1])
                seg_w = last_x - x0
                self.visual.append(VisualLine(hi, 0, len(hl), x0, bl0, seg_w))
                max_w = max(max_w, last_x - self.box_left, seg_w)
            if pos:
                baseline = pos[0][1] + lh
            else:
                baseline += lh
        if self.auto_width:
            self.width = max(self.min_width, max_w)

    def _layout_wrap(self, adv_fn, wrap_w, line_height):
        self.visual = []
        self._glyph_pos = [[] for _ in self.hard_lines]
        baseline = self.first_baseline
        max_w = 0.0
        for hi, hl in enumerate(self.hard_lines):
            self._glyph_pos[hi] = [(self.box_left, baseline)] * len(hl)
            if not hl:
                self.visual.append(VisualLine(hi, 0, 0, self.box_left, baseline))
                baseline += line_height
                continue
            start = 0
            cur_w = 0.0
            for i, (ch, st) in enumerate(hl):
                w = adv_fn(ch, st)
                if cur_w + w > wrap_w + 0.01 and i > start:
                    end = i
                    while end > start and hl[end - 1][0].isspace():
                        end -= 1
                    if end == start:
                        end = i
                    seg_w = sum(adv_fn(c, s) for c, s in hl[start:end])
                    self.visual.append(VisualLine(hi, start, end,
                                                  self.box_left, baseline,
                                                  seg_w))
                    cx = self.box_left
                    for k in range(start, end):
                        self._glyph_pos[hi][k] = (cx, baseline)
                        cx += adv_fn(*hl[k])
                    max_w = max(max_w, seg_w)
                    baseline += line_height
                    start = end
                    while start < len(hl) and hl[start][0].isspace():
                        start += 1
                    if start > i:
                        continue
                    cur_w = sum(adv_fn(c, s) for c, s in hl[start:i + 1])
                else:
                    cur_w += w
            if start < len(hl):
                seg_w = sum(adv_fn(c, s) for c, s in hl[start:])
                self.visual.append(VisualLine(hi, start, len(hl),
                                              self.box_left, baseline, seg_w))
                cx = self.box_left
                for k in range(start, len(hl)):
                    self._glyph_pos[hi][k] = (cx, baseline)
                    cx += adv_fn(*hl[k])
                max_w = max(max_w, seg_w)
            baseline += line_height

    def layout(self, adv_fn=None):
        """断行 + 基线（adv_fn(char, style) → advance，None 用已绑定度量或字号近似）。"""
        adv_fn = self._measure(adv_fn)
        self.tracking = self._estimate_tracking(adv_fn)
        self.fit_scale = 1.0
        self.overflowed = False
        if self.auto_width:
            self._layout_origin(adv_fn, 1.0)
            return
        wrap_w = self.width
        if self.overflow == "shrink":
            nat = max(self._natural_width(adv_fn), 0.01)
            n = max(1, len([hl for hl in self.hard_lines if hl]) or 1)
            cap = max(self._cap_height, self.line_height)
            scale = min(1.0, wrap_w / nat)
            if n * self.line_height > cap + 0.5:
                scale = min(scale, cap / (n * self.line_height))
            self.fit_scale = max(0.45, scale)
            scaled = self._scale_adv(adv_fn, self.fit_scale)
            if nat * self.fit_scale <= wrap_w + 0.5:
                self._layout_origin(adv_fn, self.fit_scale)
                return
            self._layout_wrap(scaled, wrap_w, self.line_height * self.fit_scale)
            return
        self._layout_wrap(adv_fn, wrap_w, self.line_height)
        widest = max((v.width for v in self.visual), default=0.0)
        bottom = self.first_baseline
        if self.visual:
            bottom = self.visual[-1].baseline + self.line_height * 0.35
        self.overflowed = (widest > wrap_w + 0.5
                           or bottom - (self.first_baseline - self.line_height)
                           > self._cap_height + 1.0)

    def bbox(self) -> tuple:
        """缓冲内容包围盒（含底部）。锁定宽度时框宽取 wrap 宽，否则随最长行。"""
        lh = self.line_height * (self.fit_scale if self.fit_scale > 0 else 1.0)
        pad = max(2.0, lh * 0.06)
        adv_fn = self._scaled_measure()
        xs, tops, bottoms = [], [], []
        for hi, hl in enumerate(self.hard_lines):
            pos = self._glyph_pos[hi] if hi < len(self._glyph_pos) else []
            for i, (ch, st) in enumerate(hl):
                if i >= len(pos):
                    continue
                x, bl = pos[i]
                xs.append(x)
                xs.append(x + adv_fn(ch, st))
                tops.append(bl - lh * 0.85)
                bottoms.append(bl + lh * 0.35)
        if not xs:
            return (self.box_left - pad,
                    self.first_baseline - lh,
                    self.box_left + self.width + pad, self.first_baseline)
        if not self.auto_width:
            left = self.box_left
            right = self.box_left + self.width
        else:
            left = min(xs)
            right = max(max(xs), left + self.min_width)
        top = min(tops)
        bottom = max(bottoms)
        return (left - pad, top - pad * 0.3, right + pad, bottom + pad * 0.3)

    # ------------------------------------------------ 光标几何
    def cursor_pos(self, cursor: tuple, adv_fn=None) -> tuple:
        """光标 (硬行, 偏移) → (x, baseline)"""
        hl_idx, off = cursor
        if not (0 <= hl_idx < len(self.hard_lines)):
            return (self.box_left, self.first_baseline)
        adv_fn = self._scaled_measure(adv_fn)
        hl = self.hard_lines[hl_idx]
        off = max(0, min(off, len(hl)))
        pos = self._glyph_pos[hl_idx] if hl_idx < len(self._glyph_pos) else []
        if off < len(pos):
            return pos[off]
        if pos:
            x, bl = pos[-1]
            return (x + adv_fn(*hl[-1]), bl)
        for v in self.visual:
            if v.hard_idx == hl_idx and v.start <= off <= v.end:
                x = v.x + sum(adv_fn(c, s) for c, s in hl[v.start:off])
                return (x, v.baseline)
        for v in reversed(self.visual):
            if v.hard_idx == hl_idx:
                x = v.x + sum(adv_fn(c, s) for c, s in hl[v.start:off])
                return (x, v.baseline)
        return (self.box_left, self.first_baseline)

    def hit_test(self, x: float, y: float, adv_fn=None) -> tuple:
        """页面坐标 → 光标 (硬行, 偏移)。"""
        adv_fn = self._scaled_measure(adv_fn)
        best_v, best_d = None, None
        for v in self.visual:
            d = abs(y - v.baseline)
            if best_d is None or d < best_d:
                best_v, best_d = v, d
        if best_v is None:
            return (0, 0)
        hl = self.hard_lines[best_v.hard_idx]
        pos = (self._glyph_pos[best_v.hard_idx]
               if best_v.hard_idx < len(self._glyph_pos) else [])
        if x <= best_v.x:
            return (best_v.hard_idx, best_v.start)
        i = best_v.start
        while i < best_v.end:
            gx = pos[i][0] if i < len(pos) else best_v.x
            w = adv_fn(*hl[i])
            if x < gx + w / 2:
                return (best_v.hard_idx, i)
            i += 1
        return (best_v.hard_idx, best_v.end)

    # ------------------------------------------------ 编辑操作
    def style_at(self, cursor: tuple) -> TextStyle:
        """光标处继承样式。"""
        hl_idx, off = cursor
        if not (0 <= hl_idx < len(self.hard_lines)):
            return TextStyle()
        hl = self.hard_lines[hl_idx]
        if 0 < off <= len(hl):
            return hl[off - 1][1].copy()
        if hl and off == 0:
            return hl[0][1].copy()
        # 空行：找最近非空行
        for d in range(1, len(self.hard_lines) + 1):
            for j in (hl_idx - d, hl_idx + d):
                if 0 <= j < len(self.hard_lines) and self.hard_lines[j]:
                    return self.hard_lines[j][0][1].copy()
        return TextStyle()

    def apply_style(self, size=None, color=None, selection=None,
                    bold=None, italic=None):
        """把字号/颜色/粗斜体应用到选区；无选区则整框。"""
        if size is None and color is None and bold is None and italic is None:
            return
        if selection:
            ranges = self.selection_range(*selection)
        else:
            ranges = [(hi, 0, len(hl)) for hi, hl in enumerate(self.hard_lines)]
        for hi, s, e in ranges:
            hl = self.hard_lines[hi]
            for i in range(s, e):
                ch, st = hl[i]
                st = st.copy()
                if size is not None:
                    st.size = float(size)
                    if st.render_mode:
                        st.border_width = max(0.15, st.size * 0.035)
                if color is not None:
                    st.color = tuple(color)
                if bold is not None:
                    st = set_style_bold(st, bool(bold))
                if italic is not None:
                    st = set_style_italic(st, bool(italic))
                hl[i] = (ch, st)
        self._style_dirty = True
        self.layout()

    def insert(self, cursor: tuple, text: str) -> tuple:
        """插入文本，返回新光标。"""
        if not text:
            return cursor
        hl_idx, off = cursor
        st = self.style_at(cursor)
        hl = self.hard_lines[hl_idx]
        for ch in text:
            if ch == "\n":
                self.hard_lines.insert(hl_idx + 1, hl[off:])
                del hl[off:]
                self._orig_split(hl_idx, off, self.line_height)
                hl_idx += 1
                off = 0
                hl = self.hard_lines[hl_idx]
                continue
            hl.insert(off, (ch, st.copy()))
            self._src_origin[hl_idx].insert(off, None)
            self._src_id[hl_idx].insert(off, None)
            off += 1
        self.layout()
        return (hl_idx, off)

    def backspace(self, cursor: tuple) -> tuple:
        hl_idx, off = cursor
        if off > 0:
            del self.hard_lines[hl_idx][off - 1]
            del self._src_origin[hl_idx][off - 1]
            del self._src_id[hl_idx][off - 1]
            new = (hl_idx, off - 1)
        elif hl_idx > 0:
            off = len(self.hard_lines[hl_idx - 1])
            self._absorb_line(hl_idx - 1, hl_idx)
            new = (hl_idx - 1, off)
        else:
            return cursor
        self.layout()
        return new

    def delete_forward(self, cursor: tuple) -> tuple:
        hl_idx, off = cursor
        hl = self.hard_lines[hl_idx]
        if off < len(hl):
            del hl[off]
            del self._src_origin[hl_idx][off]
            del self._src_id[hl_idx][off]
        elif hl_idx < len(self.hard_lines) - 1:
            self._absorb_line(hl_idx, hl_idx + 1)
        self.layout()
        return cursor

    def split_line(self, cursor: tuple) -> tuple:
        """Enter：硬换行。"""
        hl_idx, off = cursor
        hl = self.hard_lines[hl_idx]
        self.hard_lines.insert(hl_idx + 1, hl[off:])
        del hl[off:]
        self._orig_split(hl_idx, off, self.line_height)
        self.layout()
        return (hl_idx + 1, 0)

    # ------------------------------------------------ 选区
    def selection_range(self, anchor: tuple, focus: tuple) -> list:
        """选区 [(hard_idx, start, end), ...]（阅读顺序）。"""
        a, f = anchor, focus
        if a > f:
            a, f = f, a
        out = []
        for hi in range(a[0], f[0] + 1):
            s = a[1] if hi == a[0] else 0
            e = f[1] if hi == f[0] else len(self.hard_lines[hi])
            if e > s:
                out.append((hi, s, e))
        return out

    def selection_text(self, anchor, focus) -> str:
        segs = self.selection_range(anchor, focus)
        parts = ["".join(c for c, _s in self.hard_lines[hi][s:e])
                 for (hi, s, e) in segs]
        return "\n".join(parts)

    def delete_selection(self, anchor, focus) -> tuple:
        segs = self.selection_range(anchor, focus)
        if not segs:
            return focus if anchor <= focus else anchor
        first_hi, first_s, _ = segs[0]
        last_hi, _, last_e = segs[-1]
        if first_hi == last_hi:
            hl = self.hard_lines[first_hi]
            del hl[first_s:last_e]
            del self._src_origin[first_hi][first_s:last_e]
            del self._src_id[first_hi][first_s:last_e]
        else:
            head = self.hard_lines[first_hi][:first_s]
            tail = self.hard_lines[last_hi][last_e:]
            ohead = self._src_origin[first_hi][:first_s]
            otail = self._src_origin[last_hi][last_e:]
            ihead = self._src_id[first_hi][:first_s]
            itail = self._src_id[last_hi][last_e:]
            self._nudge_origins_y(otail, -(last_hi - first_hi) * self.line_height)
            for hi in range(last_hi, first_hi - 1, -1):
                del self.hard_lines[hi]
                del self._src_origin[hi]
                del self._src_id[hi]
            self.hard_lines.insert(first_hi, head + tail)
            self._src_origin.insert(first_hi, ohead + otail)
            self._src_id.insert(first_hi, ihead + itail)
        self.layout()
        return (first_hi, first_s)

    # ------------------------------------------------ 词导航
    def word_bounds(self, cursor: tuple) -> tuple:
        hl_idx, off = cursor
        hl = self.hard_lines[hl_idx]
        n = len(hl)
        if n == 0:
            return (off, off)
        i = min(off, n - 1)
        k = char_kind(hl[i][0])
        if k == "space":
            a = i
            while a > 0 and char_kind(hl[a - 1][0]) == "space":
                a -= 1
            b = i + 1
            while b < n and char_kind(hl[b][0]) == "space":
                b += 1
            return (a, b)
        if k == "punct":
            return (i, i + 1)
        a = i
        while a > 0 and char_kind(hl[a - 1][0]) == k:
            a -= 1
        b = i + 1
        while b < n and char_kind(hl[b][0]) == k:
            b += 1
        return (a, b)

    def word_step(self, cursor: tuple, forward: bool) -> tuple:
        hl_idx, off = cursor
        hl = self.hard_lines[hl_idx]
        n = len(hl)
        if forward:
            i = off
            if i < n:
                k = char_kind(hl[i][0])
                while i < n and char_kind(hl[i][0]) == k:
                    i += 1
            while i < n and char_kind(hl[i][0]) == "space":
                i += 1
            if i >= n and hl_idx < len(self.hard_lines) - 1:
                return (hl_idx + 1, 0)
            return (hl_idx, i)
        i = off
        if i > 0:
            k = char_kind(hl[i - 1][0])
            while i > 0 and char_kind(hl[i - 1][0]) == k:
                i -= 1
        while i > 0 and char_kind(hl[i - 1][0]) == "space":
            i -= 1
        if i == 0 and hl_idx > 0:
            return (hl_idx - 1, len(self.hard_lines[hl_idx - 1]))
        return (hl_idx, i)

    # ------------------------------------------------ 几何变换（框移动/缩放）
    def translate(self, dx: float, dy: float):
        self.box_left += dx
        self.first_baseline += dy
        for line in self._src_origin:
            for i, p in enumerate(line):
                if p is not None:
                    line[i] = (p[0] + dx, p[1] + dy)
        self.layout()

    def set_width(self, width: float):
        self.width = max(self.min_width, width)
        self.auto_width = False
        self.layout()

    # ------------------------------------------------ 提交序列化
    def commit_runs(self, oracle) -> list:
        """把缓冲序列化为提交序列：
        [ (text, style, x, baseline, rf) ... ]（按可视行、Run 分组）"""
        runs = []
        scale = self.fit_scale if self.fit_scale > 0 else 1.0
        for v in self.visual:
            hl = self.hard_lines[v.hard_idx]
            pos = (self._glyph_pos[v.hard_idx]
                   if v.hard_idx < len(self._glyph_pos) else [])
            i = v.start
            while i < v.end:
                ch, st = hl[i]
                rf = oracle.char_font(st, ch)
                x, bl = pos[i] if i < len(pos) else (v.x, v.baseline)
                st_out = st
                if abs(scale - 1.0) > 1e-4:
                    st_out = st.copy()
                    st_out.size = st.size * scale
                    if st_out.border_width:
                        st_out.border_width *= scale
                j = i + 1
                text = ch
                cx = x + oracle.advance(st, ch) * scale
                while j < v.end:
                    cj, sj = hl[j]
                    rfj = oracle.char_font(sj, cj)
                    pj = pos[j] if j < len(pos) else (cx, bl)
                    if (rfj.key == rf.key and sj.key == st.key
                            and abs(pj[0] - cx) < 0.05 and abs(pj[1] - bl) < 0.05):
                        text += cj
                        cx += oracle.advance(sj, cj) * scale
                        j += 1
                    else:
                        break
                runs.append((text, st_out, x, bl, rf))
                i = j
        return runs
