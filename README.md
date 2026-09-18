# PDF 无感编辑器

桌面端 PDF 编辑器：以文本框为单元做类 PPT 编辑，提交时对内容流做局部重写（真删除 + 原位重建），撤销通过页面字节快照回到原版。

## 环境

- Python 3.10+
- Windows / macOS / Linux（GUI 依赖 Qt）

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -e ".[dev]"
```

只跑程序也可以：`pip install -r requirements.txt`

## 运行

```bash
python main.py
```

启动后为空工作台，自行打开 PDF。指定文件：

```bash
python main.py --demo 合同.pdf
python main.py --demo 合同.pdf --screenshot shot.png
```

生成演示 PDF（不经过 GUI）：

```bash
python -c "from core.sample import create_sample_pdf; create_sample_pdf('demo.pdf')"
```

## 编辑模型

- 单击文本框：选中，可拖动移动，左右手柄调宽
- 双击文本框：进入框内编辑（光标 / 选区 / 输入法）
- 提交：一次 redact 清除原区域，再按行插入
- 撤销 / 重做：恢复页面对象与内容流的原始字节
- 图片：移动、手柄缩放（所见即所得）
- Ctrl+滚轮缩放；Shift+滚轮横向平移；滚轮在页边翻页

保真等级：绿（原生）/ 黄（局部重建）/ 橙（覆盖或超限）/ 红（扫描件）。

## 测试

```bash
pytest -m "not gui"     # 核心 + 几何（无头，CI 默认）
pytest -m gui           # GUI 冒烟（需显示或 QT_QPA_PLATFORM=offscreen）
ruff check core ui tests main.py
```

兼容入口：`python selftest.py`、`python smoke_gui.py`。

## 目录

```
main.py           入口
core/             PDF 引擎（无 Qt）
ui/               PySide6 界面（主窗口已拆 stage/chrome/canvas）
tests/            pytest
```

开发约定见 [AGENTS.md](AGENTS.md)。
