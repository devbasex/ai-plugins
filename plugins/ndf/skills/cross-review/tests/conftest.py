"""pytest 共通フィクスチャ。

`scripts/state.py` は uv self-contained script として `#!/usr/bin/env -S uv run --script`
で起動される運用だが、テストでは関数を直接 import したい。
importlib.util で source loader 経由で読み込む。
"""
from __future__ import annotations

import importlib.util
import pathlib
import subprocess
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


# 既定で差し替える、GitHub を読みに行く関数。実物は `_REAL` へ退避する。
_GITHUB_LOOKUPS = ("_fetch_check_runs", "_fetch_pr_metadata")
_REAL: dict[str, object] = {}


@pytest.fixture(scope="session")
def state_mod() -> types.ModuleType:
    mod = _load_state_module()
    for name in _GITHUB_LOOKUPS:
        _REAL[name] = getattr(mod, name)
    return mod


@pytest.fixture(scope="session")
def monitor_mod() -> types.ModuleType:
    return _load_monitor_module()


@pytest.fixture(autouse=True)
def _no_github(monkeypatch, state_mod) -> None:
    """テストから GitHub を呼ばない。

    収束の判定は継続的統合を照会するようになった（#327）。差し替えを忘れると、
    テストが実物の `gh` を起動して枠を消費し、対象のリポジトリの状態で結果が変わる。
    **差し替えていない `gh` の実行はその場で落とす。**

    `subprocess.run` そのものを差し替えるテストは、この見張りを上書きして先へ進む。
    """
    real = subprocess.run

    def _guard(cmd, *args, **kwargs):
        if isinstance(cmd, (list, tuple)) and cmd and str(cmd[0]) == "gh":
            raise AssertionError(
                f"テストが gh を実行しようとしました: {list(cmd)}。"
                " 呼び出しを差し替えてください"
            )
        return real(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _guard)
    # 照会は既定で「確かめられなかった」に倒す。判定は収束を止めない側へ倒すため、
    # 検査ジョブを見ない既存のテストは期待値を変えずに通る。
    monkeypatch.setattr(state_mod, "_fetch_check_runs", lambda repo, sha: None)
    monkeypatch.setattr(state_mod, "_fetch_pr_metadata", lambda pr, repo=None: None)


@pytest.fixture()
def real_github(monkeypatch, state_mod):
    """既定の差し替えを外し、実物の取得を戻す。

    取得そのものの組み立てを見るテストが使う。GitHub へは `_gh_rest` か
    `subprocess.run` の差し替えで届かないようにする。
    """
    for name in _GITHUB_LOOKUPS:
        monkeypatch.setattr(state_mod, name, _REAL[name])
