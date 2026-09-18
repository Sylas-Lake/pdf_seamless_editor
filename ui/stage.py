"""页面舞台：画布铺满，左右侧栏为可召唤抽屉。"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QSizePolicy, QWidget


class StageHost(QWidget):
    """页面铺满；左右侧栏都是可召唤抽屉。"""

    left_toggled = Signal(bool)
    RIGHT_W = 272
    LEFT_W = 164

    def __init__(self, canvas, left_drawer, right_drawer, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self.left = left_drawer
        self.right = right_drawer
        self._left_open = False
        self._right_open = False
        self.setObjectName("RootSplit")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(240, 200)
        canvas.setParent(self)
        left_drawer.setParent(self)
        right_drawer.setParent(self)
        left_drawer.hide()
        right_drawer.hide()

    @property
    def left_open(self) -> bool:
        return self._left_open

    @property
    def right_open(self) -> bool:
        return self._right_open

    def set_left_open(self, on, *, emit=True):
        on = bool(on)
        if on == self._left_open:
            return
        self._left_open = on
        self.left.setVisible(on)
        self._layout_stage(center=True)
        if emit:
            self.left_toggled.emit(on)

    def set_right_open(self, on):
        on = bool(on)
        if on == self._right_open:
            return
        self._right_open = on
        self.right.setVisible(on)
        self._layout_stage(center=True)

    def toggle_left(self):
        self.set_left_open(not self._left_open)

    def toggle_right(self):
        self.set_right_open(not self._right_open)

    def toggle_drawer(self):
        """兼容旧调用：切换右侧属性栏。"""
        self.toggle_right()

    def is_open(self) -> bool:
        return self._right_open

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._layout_stage(center=False)

    def _layout_stage(self, center=False):
        w, h = self.width(), self.height()
        left = self.LEFT_W if self._left_open else 0
        right = self.RIGHT_W if self._right_open else 0
        self.canvas.setGeometry(left, 0, max(1, w - left - right), h)
        if self._left_open:
            self.left.setGeometry(0, 0, self.LEFT_W, h)
            self.left.raise_()
        if self._right_open:
            self.right.setGeometry(max(0, w - self.RIGHT_W), 0, self.RIGHT_W, h)
            self.right.raise_()
        if center:
            self.canvas.center_page()
