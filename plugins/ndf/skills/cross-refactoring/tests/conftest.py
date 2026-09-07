"""pytest 共通フィクスチャ。

`scripts/refactor.py` は uv 自己完結スクリプトとして
`#!/usr/bin/env -S uv run --script` で起動される運用だが、テストでは関数を直接
呼びたい。cross-review と同じく importlib の source loader で読み込む。

外部プロセス（gh / 各 CLI / git push）は呼ばない。状態ファイルを一時ディレクトリへ
組み立ててサブコマンドを実行する方式に揃える。
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import types

import pytest

_HERE = pathlib.Path(__file__).resolve().parent
_SCRIPT = _HERE.parent / "scripts" / "refactor.py"
_LIB = _HERE.parents[2] / "scripts" / "lib"

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

# ---------- モジュールごとのフィクスチャ ----------
#
# **`refactor` フィクスチャは入口そのものを見るテストのために残す。** 個々の
# モジュールを確かめるテストは、この下のフィクスチャでその実体を直に引く。
# 入口の再エクスポートを通さないため、**どのモジュールが何を公開しているかが
# テストから読める**（#440）。
#
# 実体は `refactor.py` が読み込んだ時点で `sys.modules` に載る。ここで読み直すと
# 別のオブジェクトになり、差し替えが入口側へ伝わらない。

_MODULES = (
    "commands.apply", "commands.converge", "commands.gate",
    "commands.report", "commands.setup",
    "gitfacts", "outbound", "paths", "plan", "proposals",
    "rounds", "scope", "verify", "vocabulary",
)


def _module_fixture(name: str, fixture_name: str):
    @pytest.fixture(scope="session", name=fixture_name)
    def _fixture(refactor: types.ModuleType) -> types.ModuleType:
        return sys.modules[f"refactor_lib.{name}"]

    return _fixture


for _name in _MODULES:
    # `commands.apply` → `cmd_apply`、`gitfacts` → `gitfacts`
    _fixture_name = (
        "cmd_" + _name.split(".", 1)[1] if _name.startswith("commands.") else _name
    )
    globals()[_fixture_name] = _module_fixture(_name, _fixture_name)


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
def patch_lib(refactor, monkeypatch):
    """`refactor_lib` の全モジュールで、その名前を持つものを差し替える。

    **取り込みは値の写しである。** `from .paths import sh` と書いたモジュールは、
    定義元の `paths.sh` を差し替えても元の値を呼び続ける。差し替えたい対象が
    どのモジュールで使われているかはテストからは決まらないため、その名前を
    持つモジュールすべてへ当てる。

    段階 3（#441）より前は入口の `__setattr__` がこれを行っていた。**入口から
    仕掛けを外したので、テストの側が持つ。** 実装には何も残さない。
    """
    def _patch(name: str, value: object) -> None:
        hit = False
        for mod in list(sys.modules.values()):
            if not getattr(mod, "__name__", "").startswith("refactor_lib"):
                continue
            if name in vars(mod):
                monkeypatch.setattr(mod, name, value)
                hit = True
        assert hit, f"{name} を持つモジュールが無い"

    return _patch


@pytest.fixture
def no_git(paths, patch_lib, monkeypatch):
    """git / gh を呼ばせず、実行されたコマンドを記録する。

    外部プロセスを呼ばないという方針を保ちつつ、取り消しと push の**順序と引数**を
    検証できるようにする。
    """
    import subprocess

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    # **差し替えは定義元へ向ける。** 入口の再エクスポートは無くなった（#441）。
    monkeypatch.setattr(paths.subprocess, "run", fake_run)
    patch_lib("sh", lambda cmd, **k: calls.append(list(cmd)) or "")
    return calls

@pytest.fixture
def env_tmp_dir(monkeypatch):
    """`CROSS_REFACTORING_TMP_DIR` を差し替えるヘルパ。"""
    def _set(state_path: pathlib.Path) -> None:
        monkeypatch.setenv("CROSS_REFACTORING_TMP_DIR", str(state_path.parent))
    return _set
