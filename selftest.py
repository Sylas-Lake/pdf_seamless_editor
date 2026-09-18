"""无头核心自测（兼容入口）。

推荐：pytest -m "not gui"
"""
from pathlib import Path

import pytest

if __name__ == "__main__":
    raise SystemExit(pytest.main([str(Path(__file__).parent / "tests" / "test_core_pipeline.py"), "-q"]))
