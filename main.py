"""PDF 无感编辑器 · 程序入口。

Copyright (C) 2026 Sala, sh1man2357
SPDX-License-Identifier: AGPL-3.0-only

用法：
  python main.py                       # 正常启动
  python main.py 示例文档.pdf           # 启动并打开指定文件
  python main.py --demo 示例文档.pdf --screenshot shot.png  # 冒烟截图
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _resource_path(*parts: str) -> str:
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        return os.path.join(base, *parts)
    base = os.path.dirname(os.path.abspath(__file__))
    if parts == ("app.ico",):
        return os.path.join(base, "packaging", "app.ico")
    return os.path.join(base, *parts)


def _windows_app_id():
    if sys.platform != "win32":
        return
    try:
        import ctypes

        from core.appinfo import APP_ID
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


def _excepthook(exc_type, exc, tb):
    msg = "".join(traceback.format_exception(exc_type, exc, tb))
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.critical(None, "出错", msg)
    except Exception:
        sys.stderr.write(msg)
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="PDF 无感编辑器")
    parser.add_argument("pdf", nargs="?", help="启动时打开的 PDF 路径")
    parser.add_argument("--demo", help="同 pdf（兼容旧参数）")
    parser.add_argument("--screenshot", help="启动 2.5 秒后截图并退出（冒烟测试）")
    args = parser.parse_args()
    open_path = args.pdf or args.demo

    _windows_app_id()
    sys.excepthook = _excepthook

    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from core.appinfo import APP_NAME, APP_TITLE, APP_VERSION
    from ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_TITLE)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("Sala, sh1man2357")
    icon_path = _resource_path("app.ico")
    if os.path.isfile(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    elif getattr(sys, "frozen", False) and sys.platform == "win32":
        app.setWindowIcon(QIcon(sys.executable))

    win = MainWindow()
    win.resize(1280, 860)
    if os.path.isfile(icon_path):
        win.setWindowIcon(QIcon(icon_path))
    win.show()

    if open_path:
        win.open_file(open_path)

    if args.screenshot:
        def _shot():
            win.grab().save(args.screenshot)
            app.quit()
        QTimer.singleShot(2500, _shot)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
