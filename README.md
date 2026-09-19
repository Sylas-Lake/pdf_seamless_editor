# PDF 无感编辑器

桌面端 PDF 编辑器：以文本框为单元做类 PPT 编辑。提交时改的是 PDF **内容流**（真删除 + 原位重建），不是在页面上盖一层字。撤销通过页面字节快照回到原版。

**作者：** [Sala](https://github.com/Sylas-Lake)、[sh1man2357](https://github.com/sh1man2357)

当前版本 **v0.2.0**。免费开源，欢迎使用、改、再分发（见 [许可证](#许可证)）。

## 安装与运行

需要 **Python 3.10+**，Windows / macOS / Linux 均可（界面依赖 Qt）。

```bash
git clone https://github.com/Sylas-Lake/pdf_seamless_editor.git
cd pdf_seamless_editor

python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
python main.py
```

启动后是空工作台：点页面打开 PDF，或把 PDF 拖进去。也可以指定文件：

```bash
python main.py --demo 你的文件.pdf
```

开发安装（含测试工具）：`pip install -e ".[dev]"`。

## 怎么用

- **单击**文本框：选中；拖动移动；左右手柄调宽
- **双击**文本框：进入框内编辑（光标、选区、输入法）
- **方向键**：选中框后轴向平移（Shift 加速）
- **粗体 / 斜体**：工具栏按钮，或 `Ctrl+B` / `Ctrl+I`
- **图片**：移动、手柄缩放（所见即所得）
- **撤销 / 重做**：`Ctrl+Z` / `Ctrl+Y`，恢复该页原始字节，不是再插一遍字
- 缩放：`Ctrl` + 滚轮；横向平移：`Shift` + 滚轮；页边滚轮翻页
- 打开 / 保存：`Ctrl+O` / `Ctrl+S`；另存为：`Ctrl+Shift+S`

失焦、换页、保存前会自动提交当前编辑框。

保真指示：绿（原生）/ 黄（局部重建，例如换了字体）/ 橙（超出原区域）/ 红（扫描件，无法改字）。

## 已知限制

- 部分 PDF 的斜体在编辑后可能掉成正体，可用斜体按钮手动加回
- 扫描页（纯图片）不能当文字改
- 第一版请从源码运行；安装包以后再挂到 Release

问题请开 [Issues](https://github.com/Sylas-Lake/pdf_seamless_editor/issues)。

## 许可证

Copyright © 2026 [Sala](https://github.com/Sylas-Lake)、[sh1man2357](https://github.com/sh1man2357)

本软件以 [GNU Affero General Public License v3.0](LICENSE) 发布，**不含任何担保**。你可以免费使用、修改、再分发；若再分发（含把改过的版本做成在线服务），需要按 AGPL 公开对应源码。

PDF 引擎使用 [PyMuPDF](https://pymupdf.readthedocs.io/)（Artifex），同样为 AGPL-3.0。界面使用 PySide6（LGPL）。若需要闭源商用，请自行向 Artifex 了解 PyMuPDF 的商业授权，本仓库不提供商业授权。

## 开发

```bash
pytest -m "not gui"     # 核心（无头，CI 默认）
pytest -m gui           # GUI 冒烟（需显示或 QT_QPA_PLATFORM=offscreen）
ruff check core ui tests main.py
```

兼容入口：`python selftest.py`、`python smoke_gui.py`。生成演示 PDF（不经 GUI）：

```bash
python -c "from core.sample import create_sample_pdf; create_sample_pdf('demo.pdf')"
```

| 路径 | 职责 |
|------|------|
| `main.py` | 入口 |
| `core/` | PDF 引擎（无 Qt） |
| `ui/` | PySide6 界面 |
| `tests/` | pytest |

Agent / 合入约定见 [AGENTS.md](AGENTS.md)。
