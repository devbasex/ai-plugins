"""判断のテストで共有する、隣接モジュールの読み込み。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_counting() -> ModuleType:
    """隣の _counting.py を読む（importlib のモードでは隣のモジュールを名前で import できない）。"""
    spec = importlib.util.spec_from_file_location("_counting", Path(__file__).with_name("_counting.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
