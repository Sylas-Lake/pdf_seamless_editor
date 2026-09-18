"""文本框编辑会话（内存缓冲，提交前不写 PDF）。"""
from __future__ import annotations

from core.fonts import FontOracle
from core.models import TextBlock
from core.textbox import BoxBuffer
from core.types import PageState


class EditSession:
    """一次文本框编辑会话。"""

    def __init__(self, block: TextBlock, page_index: int, buffer: BoxBuffer,
                 before_state: PageState, oracle: FontOracle):
        self.block = block
        self.page_index = page_index
        self.buffer = buffer
        self.before_state = before_state
        self.oracle = oracle
        self.cursor: tuple[int, int] = (0, 0)
        self.selection = None      # (anchor, focus) 框内缓冲坐标
        self.preedit = ""
