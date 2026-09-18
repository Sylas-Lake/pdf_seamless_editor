"""保存验证报告对话框：结构校验 + 前后视觉对比 + 差异图。"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)


def _pixmap_from_png(data, width=170):
    if not data:
        return None
    pm = QPixmap()
    if not pm.loadFromData(data):
        return None
    if pm.width() > width:
        pm = pm.scaledToWidth(width, Qt.TransformationMode.SmoothTransformation)
    return pm


class DiffDialog(QDialog):
    """视觉回归验证报告。"""

    def __init__(self, report, parent=None):
        super().__init__(parent)
        self.setWindowTitle("保存验证报告（视觉回归 + 结构校验）")
        self.resize(760, 640)
        lay = QVBoxLayout(self)

        ok = report.get("ok", False)
        head = QLabel("验证通过：非编辑区域接近零差异，结构完整。"
                      if ok else "验证发现问题，请查看下方明细。")
        head.setStyleSheet(f"color:{'#2e9e5b' if ok else '#e74c3c'};"
                           "font-weight:600; padding:4px;")
        lay.addWidget(head)

        struct = QLabel("\n".join(report.get("structure", [])) or "—")
        struct.setWordWrap(True)
        lay.addWidget(struct)

        scr = QScrollArea()
        scr.setWidgetResizable(True)
        body = QWidget()
        body_lay = QVBoxLayout(body)
        for item in report.get("pages", []):
            row = QHBoxLayout()
            col = 1 + item.get("page", 0)
            info = (f"第 {col} 页\n"
                    f"非编辑区差异：{item.get('outside', 0) * 100:.3f}%\n"
                    f"编辑区差异：{item.get('inside', 0) * 100:.2f}%\n"
                    f"{item.get('note', '')}")
            lab = QLabel(info)
            lab.setMinimumWidth(190)
            lab.setAlignment(Qt.AlignmentFlag.AlignTop)
            row.addWidget(lab)
            for key, tag in (("orig", "原"), ("new", "新"), ("diff", "差异")):
                pm = _pixmap_from_png(item.get(key))
                colw = QWidget()
                cv = QVBoxLayout(colw)
                cv.setContentsMargins(2, 2, 2, 2)
                t = QLabel(tag)
                t.setAlignment(Qt.AlignmentFlag.AlignCenter)
                cv.addWidget(t)
                if pm:
                    im = QLabel()
                    im.setPixmap(pm)
                    cv.addWidget(im)
                else:
                    im = QLabel("—")
                    im.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    cv.addWidget(im)
                row.addWidget(colw)
            body_lay.addLayout(row)
        scr.setWidget(body)
        lay.addWidget(scr, 1)

        btn = QPushButton("关闭")
        btn.clicked.connect(self.accept)
        lay.addWidget(btn)
