"""pytest 配置。"""
from __future__ import annotations


def pytest_configure(config):
    config.addinivalue_line("markers", "gui: 需要 Qt 界面（可用 QT_QPA_PLATFORM=offscreen）")
