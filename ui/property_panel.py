"""属性面板：样式继承展示、选区样式修改、溢出策略、保真状态。"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QColorDialog, QComboBox, QDoubleSpinBox,
                               QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QListWidget, QPushButton, QToolButton,
                               QVBoxLayout, QWidget)

from core.fidelity import COLORS, LABELS


class PropertyPanel(QWidget):
    """右侧属性面板。"""

    apply_style = Signal(float, tuple)       # (size, color)
    overflow_changed = Signal(str)
    selection_stats = Signal()

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setMinimumWidth(250)
        lay = QVBoxLayout(self)

        # ---- 光标样式（继承） ----
        gb_style = QGroupBox("样式继承（光标处）")
        form = QFormLayout(gb_style)
        self.ed_font = QLabel("—")
        self.ed_font.setWordWrap(True)
        self.ed_font.setFont(QFont("Microsoft YaHei", 9))
        self.ed_font.setTextInteractionFlags(Qt.TextInteractionFlags.TextSelectableByMouse)
        self.sp_size = QDoubleSpinBox()
        self.sp_size.setRange(3.0, 96.0)
        self.sp_size.setSingleStep(0.5)
        self.sp_size.setDecimals(1)
        self.btn_color = QPushButton()
        self.btn_color.setFixedHeight(24)
        form.addRow("字体：", self.ed_font)
        form.addRow("字号：", self.sp_size)
        form.addRow("颜色：", self.btn_color)
        flags_row = QWidget()
        flags_lay = QHBoxLayout(flags_row)
        flags_lay.setContentsMargins(0, 0, 0, 0)
        flags_lay.setSpacing(6)
        self.btn_bold = QToolButton()
        self.btn_italic = QToolButton()
        for btn, act_name in ((self.btn_bold, "act_bold"),
                              (self.btn_italic, "act_italic")):
            btn.setAutoRaise(True)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            btn.setFixedSize(28, 24)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            act = getattr(controller, act_name, None)
            if act is not None:
                btn.setDefaultAction(act)
        flags_lay.addWidget(self.btn_bold)
        flags_lay.addWidget(self.btn_italic)
        flags_lay.addStretch(1)
        form.addRow("字形：", flags_row)
        self.btn_apply = QPushButton("应用样式到选区")
        self.btn_apply.clicked.connect(self._apply)
        form.addRow(self.btn_apply)
        lay.addWidget(gb_style)
        self.btn_color.clicked.connect(self._pick_color)

        # ---- 编辑设置 ----
        gb_edit = QGroupBox("编辑设置（原位约束）")
        f2 = QFormLayout(gb_edit)
        self.cmb_overflow = QComboBox()
        self.cmb_overflow.addItem("自动缩小字号（推荐）", "shrink")
        self.cmb_overflow.addItem("保持字号，超出时提示", "keep")
        self.cmb_overflow.currentIndexChanged.connect(
            lambda i: self.overflow_changed.emit(self.cmb_overflow.itemData(i)))
        f2.addRow("文本溢出策略：", self.cmb_overflow)
        self.lb_sel = QLabel("选区：—")
        f2.addRow(self.lb_sel)
        lay.addWidget(gb_edit)

        # ---- 保真等级 ----
        gb_fid = QGroupBox("保真等级（本页）")
        v3 = QVBoxLayout(gb_fid)
        self.lb_level = QLabel("—")
        self.lb_level.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lb_level.setStyleSheet("color:#fff; border-radius:4px; padding:4px;")
        self.lst_reasons = QListWidget()
        self.lst_reasons.setMaximumHeight(140)
        v3.addWidget(self.lb_level)
        v3.addWidget(self.lst_reasons)
        lay.addWidget(gb_fid)

        lay.addStretch(1)

    def _pick_color(self):
        c = QColorDialog.getColor(parent=self)
        if c.isValid():
            self._color = (c.red() / 255, c.green() / 255, c.blue() / 255)
            self._update_color_btn()

    def _update_color_btn(self):
        c = getattr(self, "_color", (0, 0, 0))
        qc = QColor(int(c[0] * 255), int(c[1] * 255), int(c[2] * 255))
        self.btn_color.setStyleSheet(
            f"background:{qc.name()}; border:1px solid #999;")

    def _apply(self):
        self.apply_style.emit(self.sp_size.value(),
                              getattr(self, "_color", (0, 0, 0)))

    # ---- 刷新 ----
    def update_style(self, style):
        if style is None:
            self.ed_font.setText("—")
            return
        self.ed_font.setText(style.display_name or style.font_name or "（默认）")
        self.sp_size.setValue(round(style.size, 1))
        self._color = style.color
        self._update_color_btn()

    def update_selection(self, n_chars, n_lines):
        self.lb_sel.setText(f"选区：{n_chars} 字符 / {n_lines} 行"
                            if n_chars else "选区：—")

    def update_fidelity(self, level, reasons):
        self.lb_level.setText(LABELS.get(level, level))
        self.lb_level.setStyleSheet(
            f"background:{COLORS.get(level, '#888')}; color:#fff;"
            "border-radius:4px; padding:4px; font-weight:600;")
        self.lst_reasons.clear()
        for r in reasons or []:
            self.lst_reasons.addItem(r)
