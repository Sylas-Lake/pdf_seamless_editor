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

pip install -r requirements.txt
```

依赖：`PyMuPDF`、`PySide6`、`Pillow`、`numpy`。

## 运行

```bash
python main.py
```

启动后为空工作台，由用户自行打开 PDF。也可以：

```bash
python main.py --demo 示例文档.pdf
python main.py --demo 示例文档.pdf --screenshot shot.png
```

菜单「生成示例」会写出一份中英混排 + 图片的演示 PDF。

## 编辑模型

- 单击文本框：选中，可拖动移动，左右手柄调宽
- 双击文本框：进入框内编辑（光标 / 选区 / 输入法）
- 提交：一次 redact 清除原区域，再按行插入
- 撤销 / 重做：恢复页面对象与内容流的原始字节，而不是重新插入模拟
- 图片：移动、手柄缩放（所见即所得，不强制等比）

保真等级：绿（原生）/ 黄（局部重建）/ 橙（覆盖或超限）/ 红（扫描件）。

## 测试

```bash
python selftest.py      # 无头核心链路
python smoke_gui.py     # GUI 程序化冒烟（需显示环境）
```

## 目录

```
main.py           入口
selftest.py       核心自测
smoke_gui.py      GUI 冒烟
core/             提取、字体、执行器、快照、校验
ui/               PySide6 主窗口、画布、属性栏
```

## 开发约定

见 [AGENTS.md](AGENTS.md)。
