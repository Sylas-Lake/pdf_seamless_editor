# Agent 指南 · PDF 无感编辑器

给在本仓库改代码的 agent 用。先读本文件再动手。

## 产品不变量

这不是「在页面上盖一层文字」。编辑必须改 PDF 内容流：

1. 删除用 Redaction（`fill=False`，不画遮盖、不伤底图）。
2. 提交以文本框为单位：框内只改 `BoxBuffer`，提交时一次 redact + 按行 `insert_runs`。
3. 撤销 / 重做是页面字节快照（`capture_page_state` / `restore_page_state`），禁止用「再插一遍字」模拟撤销。
4. 保存后非编辑区域视觉差异应接近零（见 `core/verifier.py`）。

破坏以上四点的改动视为回归，不要合入。

## 分层

| 路径 | 职责 |
|------|------|
| `core/` | 模型、提取、字体、执行器、命令、快照、保真、校验。无 Qt。 |
| `ui/` | PySide6：选中 / 双击进会话 / 画布叠加 / 属性栏。不直接改内容流。 |
| `main.py` | 启动。 |
| `selftest.py` | 无头全链路。改 `core/` 必须能过。 |
| `smoke_gui.py` | GUI 会话链路。改交互必须能过。 |

PyMuPDF 统一写成：

```python
try:
    import pymupdf as fitz
except ImportError:
    import fitz
```

## 编辑会话（`ui/main_window.py`）

- 单击选中，双击进入 `EditSession`。
- 会话中输入、退格、换行只动 `BoxBuffer` 与光标/选区。
- 失焦、换页、保存前要提交当前会话。
- 提交后立刻 `PageStateCommand` 入栈，并记录 `edit_rects` 供视觉回归。

溢出默认策略是 `shrink`（缩小字号适配框）。左右拖动手柄后框宽锁定并换行；未锁定时框随内容变宽。

## 字体（`core/fonts.py`）

优先级：原嵌入子集能覆盖 → 系统同名完整字体 → 内置 CJK / Base14。  
缺字不要静默画方框；应走替代并下调保真等级（绿 → 黄）。扫描页走红色。

## 几何

PDF 矩形是 `(x0, y0, x2, y2)`。交给 Qt 时用 `core.geom.qrect_args`（宽高，不是对角点）。图片缩放默认所见即所得，不要擅自 `keep_proportion=True`，除非调用方明确要求等比。

## 改动范围

- 只改与任务相关的文件。不要顺手重构、不要加无关依赖。
- 不要新增覆盖层作为主编辑路径（例如只画 QGraphicsTextItem 却不写回 PDF）。
- 不要在 GUI 里绕过 `executor` 直接 `insert_text` / 画白块。
- 用户未要求时不要提交 git、不要改远程。

## 验证

```bash
python selftest.py
python smoke_gui.py    # 改了 ui/ 时
```

`selftest.py` 覆盖：提取、BoxBuffer、快照撤销零差异、框移动、图片变换、保存后可搜索且无游离残骸。失败项修到通过再结束。

## 语言与风格

- 用户对话用中文。代码注释、提交说明沿用仓库现有中文。
- 保持现有模块切分与 dataclass 风格，不要上大型框架。
- 新增测试用 `check(name, cond)` 模式，与 `selftest.py` / `smoke_gui.py` 一致。
