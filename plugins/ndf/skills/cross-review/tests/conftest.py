"""pytest 共通フィクスチャ。

`scripts/state.py` は uv self-contained script として `#!/usr/bin/env -S uv run --script`
で起動される運用だが、テストでは関数を直接 import したい。
importlib.util で source loader 経由で読み込む。
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import types

_HERE = pathlib.Path(__file__).resolve().parent
_SCRIPT = _HERE.parent / "scripts" / "state.py"
_MONITOR = _HERE.parent / "scripts" / "monitor.py"


def _load_module(name: str, path: pathlib.Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_state_module() -> types.ModuleType:
    return _load_module("cross_review_state", _SCRIPT)


def _load_monitor_module() -> types.ModuleType:
    return _load_module("cross_review_monitor", _MONITOR)


import pytest


@pytest.fixture(scope="session")
def state_mod() -> types.ModuleType:
    return _load_state_module()


@pytest.fixture(scope="session")
def monitor_mod() -> types.ModuleType:
    return _load_monitor_module()
