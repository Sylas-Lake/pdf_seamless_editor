"""文档中间层：统一编辑命令（对应方案第九章第 3 节）。

UI 不直接操作 PDF 原始对象，而是通过命令：
  InsertText / DeleteRange / ReplaceRange / SetTextStyle /
  MoveObject / ResizeObject / RotateObject / ReplaceImage / DeleteImage
每条命令记录编辑前后状态，可用于撤销/重做。
"""
from dataclasses import dataclass

from . import executor


class Command:
    title = "编辑"

    def apply(self, ctx):
        """ctx: SimpleNamespace(doc=..., resolver=...)，返回结果列表。"""
        raise NotImplementedError

    def undo(self, ctx):
        raise NotImplementedError


# ---------------------------------------------------------------- 文本命令

class TextEditCommand(Command):
    """InsertText / DeleteRange / ReplaceRange 统一实现：行内局部重建。"""

    def __init__(self, title, page_index, rebuilds,
                 cursor_before=None, cursor_after=None):
        self.title = title
        self.page_index = page_index
        self.rebuilds = rebuilds
        self.cursor_before = cursor_before  # (line_index, glyph_index, line_bbox)
        self.cursor_after = cursor_after    # (line_bbox, glyph_index)
        self.last_results = []

    def apply(self, ctx):
        page = ctx.doc[self.page_index]
        self.last_results = executor.rebuild_lines(page, self.rebuilds,
                                                   ctx.resolver)
        return self.last_results

    def undo(self, ctx):
        page = ctx.doc[self.page_index]
        return executor.restore_lines(page, self.rebuilds, ctx.resolver)


class TextStyleCommand(Command):
    """SetTextStyle：选区样式修改（原位重插 + 样式增量）。"""

    def __init__(self, page_index, rebuilds):
        self.title = "设置文本样式"
        self.page_index = page_index
        # rebuilds 携带 style_delta；撤销用同一批 old_runs 原位恢复
        self.rebuilds = rebuilds
        self.last_results = []

    def apply(self, ctx):
        page = ctx.doc[self.page_index]
        self.last_results = executor.rebuild_lines(page, self.rebuilds,
                                                   ctx.resolver)
        return self.last_results

    def undo(self, ctx):
        page = ctx.doc[self.page_index]
        return executor.restore_lines(page, self.rebuilds, ctx.resolver)


# ---------------------------------------------------------------- 图片命令

@dataclass
class ImageState:
    """图片状态快照（rect + 旋转角 + 原始字节）。"""
    rect: tuple
    deg: float = 0.0
    blob: bytes = None


class ImageTransformCommand(Command):
    """MoveObject / ResizeObject / RotateObject 合一实现。"""

    def __init__(self, title, page_index, before: ImageState, after: ImageState):
        self.title = title
        self.page_index = page_index
        self.before = before
        self.after = after
        self._cur = "before"  # 当前处于哪个状态

    def _page(self, ctx):
        return ctx.doc[self.page_index]

    def apply(self, ctx):
        if self._cur == "after":
            return []  # 已是目标状态
        page = self._page(ctx)
        executor.place_image(ctx.doc, page, [self.before.rect],
                             self.after.rect, self.after.deg, self.after.blob)
        self._cur = "after"
        return []

    def undo(self, ctx):
        if self._cur == "before":
            return []
        page = self._page(ctx)
        executor.place_image(ctx.doc, page, [self.after.rect],
                             self.before.rect, self.before.deg, self.before.blob)
        self._cur = "before"
        return []


class ImageDeleteCommand(Command):
    """DeleteImage：仅移除图片实例（保留文字与矢量）。"""

    def __init__(self, page_index, state: ImageState):
        self.title = "删除图片"
        self.page_index = page_index
        self.state = state
        self._deleted = False

    def apply(self, ctx):
        if self._deleted:
            return []
        page = ctx.doc[self.page_index]
        executor.place_image(ctx.doc, page, [self.state.rect],
                             None, 0, self.state.blob)
        self._deleted = True
        return []

    def undo(self, ctx):
        if not self._deleted:
            return []
        page = ctx.doc[self.page_index]
        executor.place_image(ctx.doc, page, [],
                             self.state.rect, self.state.deg, self.state.blob)
        self._deleted = False
        return []


class ImageReplaceCommand(Command):
    """ReplaceImage：替换图片内容（位置/变换不变）。"""

    def __init__(self, page_index, xref, old_blob, new_blob):
        self.title = "替换图片"
        self.page_index = page_index
        self.xref = xref
        self.old_blob = old_blob
        self.new_blob = new_blob
        self._cur = "old"

    def apply(self, ctx):
        if self._cur == "new":
            return []
        page = ctx.doc[self.page_index]
        executor.replace_image(page, self.xref, self.new_blob)
        self._cur = "new"
        return []

    def undo(self, ctx):
        if self._cur == "old":
            return []
        page = ctx.doc[self.page_index]
        executor.replace_image(page, self.xref, self.old_blob)
        self._cur = "old"
        return []


# ---------------------------------------------------------------- 撤销栈

class UndoStack:
    """命令栈（上限 200，超出丢弃最早命令）。"""

    def __init__(self, limit=200, on_change=None):
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
        cmd = self._redo.pop()  # LIFO：重做最近撤销的命令（与 QUndoStack 语义一致）
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
