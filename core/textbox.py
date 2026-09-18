"""文本框编辑缓冲（类 PPT 编辑模型）。

- 以"文本框"（TextBlock）为编辑单元；
- 框内编辑只修改内存缓冲（BoxBuffer），光标/选区/输入法全部自然工作；
- 布局（断行 + 基线）由 advance 度量回调驱动，与 PDF 提交度量一致；
- 提交时整体重建（一次 redact + 按行插入），撤销通过页面快照字节级恢复。
"""
from dataclasses import dataclass

from .extractor import char_kind
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
        if block is not None:
            self._from_block(block)

    # ------------------------------------------------ 构造
    def _from_block(self, block: TextBlock):
        for ln in block.lines:
            self.hard_lines.append([(g.char, g.style) for g in ln.glyphs])
        if not self.hard_lines:
            self.hard_lines = [[]]
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
        self._origin_text = self.text()
        self.layout()
        self._orig_visual_count = len(self.visual)

    # ------------------------------------------------ 文本
    def text(self) -> str:
        return "\n".join("".join(c for c, _s in hl) for hl in self.hard_lines)

    @property
    def changed(self) -> bool:
        return self.text() != self._origin_text

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

    # ------------------------------------------------ 布局
    def layout(self, adv_fn=None):
        """断行 + 基线（adv_fn(char, style) → advance，None 用已绑定度量或字号近似）。"""
        adv_fn = self._measure(adv_fn)
        wrap_w = None if self.auto_width else self.width
        self.visual = []
        baseline = self.first_baseline
        max_w = 0.0
        for hi, hl in enumerate(self.hard_lines):
            if not hl:
                self.visual.append(VisualLine(hi, 0, 0, self.box_left, baseline))
                baseline += self.line_height
                continue
            start = 0
            cur_w = 0.0
            for i, (ch, st) in enumerate(hl):
                w = adv_fn(ch, st)
                if (wrap_w is not None and cur_w + w > wrap_w + 0.01 and i > start):
                    # 断行（尽量避免行尾空白）
                    end = i
                    while end > start and hl[end - 1][0].isspace():
                        end -= 1
                    if end == start:
                        end = i
                    seg_w = sum(adv_fn(c, s) for c, s in hl[start:end])
                    self.visual.append(VisualLine(hi, start, end,
                                                  self.box_left, baseline,
                                                  seg_w))
                    max_w = max(max_w, seg_w)
                    baseline += self.line_height
                    start = end
                    # 跳过断点处的空白
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
                max_w = max(max_w, seg_w)
            baseline += self.line_height  # 硬换行额外行距
        if self.auto_width:
            self.width = max(self.min_width, max_w)

    def bbox(self) -> tuple:
        """缓冲内容包围盒（含底部）。锁定宽度时框宽取 wrap 宽，否则随最长行。"""
        pad = max(2.0, self.line_height * 0.06)
        if not self.visual:
            return (self.box_left - pad,
                    self.first_baseline - self.line_height,
                    self.box_left + self.width + pad, self.first_baseline)
        last = self.visual[-1]
        top = self.visual[0].baseline - self.line_height * 0.85
        bottom = last.baseline + self.line_height * 0.35
        content_w = max((v.width for v in self.visual), default=0.0)
        box_w = self.width if not self.auto_width else max(content_w, self.min_width)
        right = self.box_left + box_w
        return (self.box_left - pad, top - pad * 0.3, right + pad, bottom + pad * 0.3)

    # ------------------------------------------------ 光标几何
    def cursor_pos(self, cursor: tuple, adv_fn=None) -> tuple:
        """光标 (硬行, 偏移) → (x, baseline)"""
        hl_idx, off = cursor
        if not (0 <= hl_idx < len(self.hard_lines)):
            return (self.box_left, self.first_baseline)
        adv_fn = self._measure(adv_fn)
        hl = self.hard_lines[hl_idx]
        off = max(0, min(off, len(hl)))
        # 找到包含该偏移的可视行
        for v in self.visual:
            if v.hard_idx == hl_idx and v.start <= off <= v.end:
                x = v.x + sum(adv_fn(c, s) for c, s in hl[v.start:off])
                return (x, v.baseline)
        # 未找到（偏移在末尾等）
        for v in reversed(self.visual):
            if v.hard_idx == hl_idx:
                x = v.x + sum(adv_fn(c, s) for c, s in hl[v.start:off])
                return (x, v.baseline)
        return (self.box_left, self.first_baseline)

    def hit_test(self, x: float, y: float, adv_fn=None) -> tuple:
        """页面坐标 → 光标 (硬行, 偏移)。"""
        adv_fn = self._measure(adv_fn)
        # 垂直：最近可视行（基线带）
        best_v, best_d = None, None
        for v in self.visual:
            d = abs(y - v.baseline)
            if best_d is None or d < best_d:
                best_v, best_d = v, d
        if best_v is None:
            return (0, 0)
        hl = self.hard_lines[best_v.hard_idx]
        # 水平：advance 累积边界
        if x <= best_v.x:
            return (best_v.hard_idx, best_v.start)
        cx = best_v.x
        i = best_v.start
        while i < best_v.end:
            w = adv_fn(*hl[i])
            if x < cx + w / 2:
                return (best_v.hard_idx, i)
            cx += w
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

    def insert(self, cursor: tuple, text: str) -> tuple:
        """插入文本，返回新光标。"""
        if not text:
            return cursor
        hl_idx, off = cursor
        st = self.style_at(cursor)
        hl = self.hard_lines[hl_idx]
        n = 0
        for ch in text:
            if ch == "\n":
                hl[off:off] = []
                self.hard_lines.insert(hl_idx + 1, hl[off:])
                del hl[off:]
                hl_idx += 1
                off = 0
                n = 0  # 光标计数重置
                hl = self.hard_lines[hl_idx]
                continue
            hl.insert(off, (ch, st.copy()))
            off += 1
            n += 1
        self.layout()
        return (hl_idx, off)

    def backspace(self, cursor: tuple) -> tuple:
        hl_idx, off = cursor
        if off > 0:
            del self.hard_lines[hl_idx][off - 1]
            new = (hl_idx, off - 1)
        elif hl_idx > 0:
            # 合并到上一硬行
            prev = self.hard_lines[hl_idx - 1]
            cur = self.hard_lines[hl_idx]
            off = len(prev)
            prev.extend(cur)
            del self.hard_lines[hl_idx]
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
        elif hl_idx < len(self.hard_lines) - 1:
            nxt = self.hard_lines.pop(hl_idx + 1)
            hl.extend(nxt)
        self.layout()
        return cursor

    def split_line(self, cursor: tuple) -> tuple:
        """Enter：硬换行。"""
        hl_idx, off = cursor
        hl = self.hard_lines[hl_idx]
        self.hard_lines.insert(hl_idx + 1, hl[off:])
        del hl[off:]
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
        else:
            # 首行保留前缀，尾行保留后缀，中间行整删
            head = self.hard_lines[first_hi][:first_s]
            tail = self.hard_lines[last_hi][last_e:]
            for hi in range(last_hi, first_hi - 1, -1):
                del self.hard_lines[hi]
            self.hard_lines.insert(first_hi, head + tail)
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
        for v in self.visual:
            hl = self.hard_lines[v.hard_idx]
            cx = v.x
            seg = hl[v.start:v.end]
            i = 0
            while i < len(seg):
                ch, st = seg[i]
                rf = oracle.char_font(st, ch)
                j = i + 1
                text = ch
                while j < len(seg):
                    cj, sj = seg[j]
                    rfj = oracle.char_font(sj, cj)
                    if rfj is rf and sj.key == st.key:
                        text += cj
                        j += 1
                    else:
                        break
                runs.append((text, st, cx, v.baseline, rf))
                cx += oracle.advance(st, text)
                i = j
        return runs
