"""pytest 共通フィクスチャ。

`scripts/refactor.py` は uv 自己完結スクリプトとして
`#!/usr/bin/env -S uv run --script` で起動される運用だが、テストでは関数を直接
呼びたい。cross-review と同じく importlib の source loader で読み込む。

外部プロセス（gh / 各 CLI / git push）は呼ばない。状態ファイルを一時ディレクトリへ
組み立ててサブコマンドを実行する方式に揃える。
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import types
from typing import Any

import pytest

from crossref_helpers import make_state, read_state, write_result  # noqa: F401

_HERE = pathlib.Path(__file__).resolve().parent
_SCRIPT = _HERE.parent / "scripts" / "refactor.py"
_LIB = _HERE.parent.parent / "cross-review" / "scripts" / "lib"


def _load_module(name: str, path: pathlib.Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def refactor() -> types.ModuleType:
    return _load_module("cross_refactoring_refactor", _SCRIPT)


@pytest.fixture(scope="session")
def assignment() -> types.ModuleType:
    sys.path.insert(0, str(_LIB))
    return _load_module("ndf_lib_assignment", _LIB / "assignment.py")


@pytest.fixture(scope="session")
def models() -> types.ModuleType:
    sys.path.insert(0, str(_LIB))
    return _load_module("ndf_lib_models", _LIB / "models.py")


@pytest.fixture(scope="session")
def metrics() -> types.ModuleType:
    sys.path.insert(0, str(_LIB))
    return _load_module("ndf_lib_metrics", _LIB / "metrics.py")


@pytest.fixture
def no_git(refactor, monkeypatch):
    """git / gh を呼ばせず、実行されたコマンドを記録する。

    外部プロセスを呼ばないという方針を保ちつつ、取り消しと push の**順序と引数**を
    検証できるようにする。
    """
    import subprocess

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(refactor.subprocess, "run", fake_run)
    monkeypatch.setattr(refactor, "_sh", lambda cmd, **k: calls.append(list(cmd)) or "")
    return calls


@pytest.fixture
def env_tmp_dir(monkeypatch):
    """`CROSS_REFACTORING_TMP_DIR` を差し替えるヘルパ。"""
    def _set(state_path: pathlib.Path) -> None:
        monkeypatch.setenv("CROSS_REFACTORING_TMP_DIR", str(state_path.parent))
    return _set
