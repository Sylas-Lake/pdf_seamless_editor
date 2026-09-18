"""文档中间层：统一编辑命令（v2：快照式）。

所有命令基于"页面状态快照"：
  - before：命令应用前的页面字节状态
  - after：命令应用后的页面字节状态
撤销 = 恢复 before（字节级原版），重做 = 恢复 after。
不存在"重新插入模拟"，彻底解决撤销无法回到原版的问题。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from . import executor
from .snapshot import restore_page_state
from .types import PageState


class Command:
    title = "编辑"

    def apply(self, ctx: Any) -> list:
        raise NotImplementedError

    def undo(self, ctx: Any) -> list:
        raise NotImplementedError


class PageStateCommand(Command):
    """通用页面状态命令（文本框编辑 / 框移动缩放 / 图片变换）。"""

    def __init__(self, title: str, page_index: int,
                 before_state: PageState, after_state: PageState):
        self.title = title
        self.page_index = page_index
        self.before = before_state
        self.after = after_state
        self.edit_rects = []  # 验证用：编辑影响区域
        self._applied = False  # push 时页面已处于 after 状态，跳过冗余恢复

    def _page(self, ctx):
        return ctx.doc[self.page_index]

    def apply(self, ctx):
        if not self._applied:
            self._applied = True
            return []
        page = self._page(ctx)
        restore_page_state(ctx.doc, page, self.after, self.page_index)
        return []

    def undo(self, ctx):
        page = self._page(ctx)
        restore_page_state(ctx.doc, page, self.before, self.page_index)
        return []


class ImageReplaceCommand(Command):
    """替换图片内容（页面快照无法覆盖图片对象字节，单独处理）。"""

    def __init__(self, page_index, xref, old_blob, new_blob):
        self.title = "替换图片"
        self.page_index = page_index
        self.xref = xref
        self.old_blob = old_blob
        self.new_blob = new_blob
        self._cur = "old"
        self.edit_rects = []

    def apply(self, ctx):
        if self._cur == "new":
            return []
        page = ctx.doc[self.page_index]
        executor.replace_image(page, self.xref, self.new_blob)
        try:
            ctx.doc.reload_page(page)
        except Exception:
            pass
        self._cur = "new"
        return []

    def undo(self, ctx):
        if self._cur == "old":
            return []
        page = ctx.doc[self.page_index]
        executor.replace_image(page, self.xref, self.old_blob)
        try:
            ctx.doc.reload_page(page)
        except Exception:
            pass
        self._cur = "old"
        return []


class UndoStack:
    """命令栈（上限 200）。"""

    def __init__(self, limit: int = 200, on_change: Callable[[], None] | None = None):
        self.limit = limit
        self._undo = []
        self._redo = []
        self.on_change = on_change

    def push(self, cmd, ctx):
        results = cmd.apply(ctx)
        self._undo.append(cmd)
        if len(self._undo) > self.limit:
            self._undo.pop(0)
        self._redo.clear()
        self._notify()
        return results

    def undo(self, ctx):
        if not self._undo:
            return None
        cmd = self._undo.pop()
        results = cmd.undo(ctx)
        self._redo.append(cmd)
        self._notify()
        return cmd, results

    def redo(self, ctx):
        if not self._redo:
            return None
        cmd = self._redo.pop()
        results = cmd.apply(ctx)
        self._undo.append(cmd)
        self._notify()
        return cmd, results

    @property
    def can_undo(self):
        return bool(self._undo)

    @property
    def can_redo(self):
        return bool(self._redo)

    def clear(self):
        self._undo.clear()
        self._redo.clear()
        self._notify()

    def _notify(self):
        if self.on_change:
            self.on_change()
