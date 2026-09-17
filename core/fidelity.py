"""保真等级评估（对应方案第十三、十四章产品规则）。

绿色 = 原生编辑（原字体/原布局完整保留）
黄色 = 局部重建（相同或近似字体重建，可能有细微差异）
橙色 = 覆盖/超限编辑（超出原区域或覆盖实现）
红色 = 图像/OCR 编辑（扫描件页面）
"""

ORDER = {"green": 0, "yellow": 1, "orange": 2, "red": 3}
LABELS = {
    "green": "原生编辑",
    "yellow": "局部重建",
    "orange": "覆盖编辑",
    "red": "图像/OCR 编辑",
}
COLORS = {
    "green": "#2e9e5b",
    "yellow": "#d4a017",
    "orange": "#e67e22",
    "red": "#e74c3c",
}


def worse(a: str, b: str) -> str:
    return a if ORDER[a] >= ORDER[b] else b


class PageFidelity:
    """单页保真状态：等级 + 原因列表。"""

    def __init__(self, page_model=None):
        self.level = "green"
        self.reasons = []
        if page_model is not None and page_model.scanned:
            self.level = "red"
            self.reasons.append(page_model.scanned_note)

    def update(self, results: list):
        """合并一批命令执行结果（ApplyResult 列表）。"""
        for res in results or []:
            self.level = worse(self.level, res.level)
            for r in getattr(res, "reasons", []):
                if r not in self.reasons:
                    self.reasons.append(r)
            for w in getattr(res, "warnings", []):
                if w not in self.reasons:
                    self.reasons.append(w)
