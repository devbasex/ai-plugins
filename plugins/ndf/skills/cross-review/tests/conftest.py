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


def _load_state_module() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("cross_review_state", _SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cross_review_state"] = mod
    spec.loader.exec_module(mod)
    return mod


import pytest


@pytest.fixture(scope="session")
def state_mod() -> types.ModuleType:
    return _load_state_module()
