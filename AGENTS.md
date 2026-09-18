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
| `core/compat.py` | 唯一的 PyMuPDF 导入点。 |
| `ui/main_window.py` | 文档与编辑会话控制器。 |
| `ui/stage.py` / `ui/chrome.py` | 抽屉舞台、图标栏。 |
| `ui/page_canvas.py` | 页面交互。手柄/覆盖层在 `handles.py`、`box_editor.py`。 |
| `ui/` 其它 | 不直接改内容流。 |
| `tests/` | pytest。改 `core/` 必须过 `-m "not gui"`；改交互必须过 `-m gui`。 |

## 编辑会话

- 单击选中，双击进入 `EditSession`（`ui/session.py`）。
- 会话中输入只动 `BoxBuffer`。
- 失焦、换页、保存前要提交当前会话。
- 提交后 `PageStateCommand` 入栈，并记录 `edit_rects`。

溢出默认 `shrink`。未锁定框宽时随内容变宽。

## 字体

原嵌入子集能覆盖 → 系统同名完整字体 → 内置 CJK / Base14。  
缺字走替代并下调保真（绿 → 黄）。扫描页红色。

## 几何

PDF 矩形 `(x0, y0, x2, y2)`。交给 Qt 用 `core.geom.qrect_args`。图片缩放默认所见即所得。

## 测试约定

- 新逻辑写进 `tests/`，用 `assert`，不要再往 `selftest.py` 堆全局计数。
- GUI 测试只调用公开 API（例如 `page_model()`、`chrome.tool_action_names()`、`canvas.try_turn_page()`）。
- 不要测 `_*` 私有属性。

```bash
pytest -m "not gui"
pytest -m gui
ruff check core ui tests main.py
```

## 改动范围

- 不要新增覆盖层作为主编辑路径。
- 不要在 GUI 里绕过 `executor` 直接 `insert_text` / 画白块。
- 用户未要求时不要提交 git、不要改远程。
