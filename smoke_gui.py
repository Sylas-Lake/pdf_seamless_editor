"""GUI 冒烟（兼容入口）。

推荐：pytest -m gui
无显示器时：set QT_QPA_PLATFORM=offscreen
"""
from pathlib import Path

import pytest

if __name__ == "__main__":
    raise SystemExit(pytest.main([str(Path(__file__).parent / "tests" / "test_gui_smoke.py"), "-q", "-s"]))
