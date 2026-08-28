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


def make_state(tmp_path: pathlib.Path, **overrides: Any) -> pathlib.Path:
    """最小の状態ファイルを組み立ててパスを返す。

    テストごとに必要な部分だけ `overrides` で差し替える。
    """
    state_id = overrides.pop("id", 130)
    host = overrides.pop("host", "claude")
    runtimes = overrides.pop("runtimes", ["codex", "gemini", "kiro"])
    tmp_dir = tmp_path / ".cross_refactoring"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "id": state_id,
        "started_at": "2026-08-15T00:00:00",
        "repo": "devbasex/ai-plugins",
        "current_pr": state_id,
        "base_branch": "main",
        "head_branch": "refactor/target",
        "worktree_root": str(tmp_path),
        "worktrees": {"work": str(tmp_path / "work"),
                      **{r: str(tmp_path / r) for r in runtimes}},
        "tmp_dir": str(tmp_dir),
        "target_scope": ["src"],
        "host": host,
        "host_detection": "explicit",
        "runtimes": runtimes,
        "impl_capable": ["claude", "codex", "kiro"],
        "models": {"claude": None, "codex": None, "gemini": None, "kiro": None},
        "skills": {"required": ["refactoring", "tdd-cycle", "quality-gates"]},
        "max_outer_rounds": 3,
        "max_fix_rounds": 3,
        "max_items_per_round": 5,
        "severity_threshold": "minor",
        "baseline_test": {"command": "pytest -q", "status": "green",
                          "checked_at": "2026-08-15T00:00:00"},
        "outer_round": 0,
        "phase": "init",
        "rounds": [],
        "items": [],
        "deferred_items": [],
        "final": None,
    }
    state.update(overrides)
    path = tmp_dir / f"cross-refactoring-rf{state_id}-state.json"
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def read_state(path: pathlib.Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_result(state_path: pathlib.Path, stem: str, payload: Any) -> pathlib.Path:
    out = state_path.parent / f"{stem}-result.json"
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return out


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
