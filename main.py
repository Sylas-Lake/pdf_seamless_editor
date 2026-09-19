"""PDF 无感编辑器 · 程序入口。

Copyright (C) 2026 Sala, sh1man2357
SPDX-License-Identifier: AGPL-3.0-only

用法：
  python main.py                       # 正常启动
  python main.py --demo 示例文档.pdf    # 启动并打开指定文件
  python main.py --demo 示例文档.pdf --screenshot shot.png  # 冒烟截图
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", help="启动时打开的 PDF 路径")
    parser.add_argument("--screenshot", help="启动 2.5 秒后截图并退出（冒烟测试）")
    args = parser.parse_args()

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from ui.main_window import APP_TITLE, MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    win = MainWindow()
    win.resize(1280, 860)
    win.show()

    if args.demo:
        win.open_file(args.demo)

    if args.screenshot:
        def _shot():
            win.grab().save(args.screenshot)
            app.quit()
        QTimer.singleShot(2500, _shot)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
