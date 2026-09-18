"""顶部图标栏。"""
from __future__ import annotations

from PySide6.QtWidgets import QLabel, QSizePolicy, QToolButton, QWidget


class ChromeBar(QWidget):
    """图标栏：左右抽屉按钮贴边，其余图标以页码为几何中心。"""

    BTN = 28

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MainBar")
        self.setFixedHeight(38)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._ends = []
        self._items = []
        self._pivot = None

    def add_end(self, widget, side):
        widget.setParent(self)
        self._ends.append((widget, side))
        return widget

    def add_item(self, widget):
        widget.setParent(self)
        self._items.append(widget)
        return widget

    def add_sep(self):
        line = QWidget(self)
        line.setObjectName("BarSep")
        line.setFixedSize(1, 16)
        self._items.append(line)
        return line

    def set_pivot(self, widget):
        self._pivot = widget

    def tool_action_names(self) -> list[str]:
        """图标栏上可见 QAction 的标题（测试与无障碍用）。"""
        names = []
        for w in self._items:
            if not isinstance(w, QToolButton):
                continue
            act = w.defaultAction()
            if act is not None:
                names.append(act.text())
        return names

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.relayout()

    def showEvent(self, e):
        super().showEvent(e)
        self.relayout()

    def _size_of(self, wid):
        if isinstance(wid, QToolButton):
            return self.BTN, self.BTN
        if wid.objectName() == "BarSep":
            return 1, 16
        if isinstance(wid, QLabel):
            return max(wid.minimumWidth(), wid.sizeHint().width()), 28
        return max(wid.sizeHint().width(), 1), max(wid.sizeHint().height(), 1)

    def relayout(self):
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        gap, margin = 2, 6

        def place(wid, x):
            ww, hh = self._size_of(wid)
            wid.setGeometry(int(x), (h - hh) // 2, int(ww), int(hh))
            return ww

        for wid, side in self._ends:
            if side == "left":
                place(wid, margin)
            else:
                ww, _ = self._size_of(wid)
                place(wid, w - margin - ww)
            wid.raise_()

        pivot = self._pivot
        if pivot is None or pivot not in self._items:
            return
        idx = self._items.index(pivot)
        pw, _ = self._size_of(pivot)
        px = (w - pw) // 2
        place(pivot, px)

        x = px
        for item in reversed(self._items[:idx]):
            iw, _ = self._size_of(item)
            x -= gap + iw
            place(item, x)

        x = px + pw
        for item in self._items[idx + 1:]:
            x += gap
            x += place(item, x)
