"""cross-refactoring のテストが共有する補助関数。

conftest.py へ置くと、複数の Skill のテストを同時に実行したときに `conftest` という
モジュール名が衝突し、別の Skill の conftest が解決されてしまう。直接 import する
補助はこの固有名のモジュールへ置く。
"""
from __future__ import annotations

import json
import pathlib
from typing import Any


def make_state(tmp_path: pathlib.Path, **overrides: Any) -> pathlib.Path:
    """最小の状態ファイルを組み立ててパスを返す。

    テストごとに必要な部分だけ `overrides` で差し替える。
    """
    state_id = overrides.pop("id", 130)
    host = overrides.pop("host", "claude")
    runtimes = overrides.pop("runtimes", ["codex", "agy", "kiro"])
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
        "models": {"claude": None, "codex": None, "agy": None, "kiro": None},
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


def make_state_v2(tmp_path: pathlib.Path, work: pathlib.Path, **overrides: Any) -> pathlib.Path:
    """版 2（#933）の最小の状態ファイルを組み立ててパスを返す。

    `work` は書き込み用の作業ディレクトリ（テストが用意した git のリポジトリ）。
    """
    state_id = overrides.pop("id", 130)
    runtimes = overrides.pop("runtimes", ["claude", "codex", "kiro"])
    tmp_dir = tmp_path / ".cross_refactoring"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "schema": 2,
        "id": state_id,
        "started_at": "2026-09-24T10:00:00",
        "budget_minutes": 60,
        "repo": "acme/demo",
        "current_pr": state_id,
        "base_branch": "main",
        "head_branch": "refactor/target",
        "worktree_root": str(tmp_path),
        "worktrees": {"work": str(work), **{r: str(tmp_path / r) for r in runtimes}},
        "tmp_dir": str(tmp_dir),
        "target_scope": ["src", "tests"],
        "host": "claude",
        "host_detection": "explicit",
        "runtimes": runtimes,
        "participants": {"pool": runtimes, "available": runtimes},
        "implementer": "claude",
        "implementer_reason": "host",
        "implementer_named": None,
        "implementer_model": {"requested": None, "observed": None},
        "judge": {"kind": "runtime", "reason": "no_key", "failures": 0},
        "resume_changes": [],
        "models": {"claude": None, "codex": None, "agy": None, "kiro": None},
        "skills": {"required": ["refactoring", "tdd-cycle", "quality-gates"]},
        "max_fix_rounds": 3,
        "ci_check": None,
        "workflow_step": True,
        "severity_threshold": "minor",
        "baseline_test": {"command": "pytest -q tests", "status": "green",
                          "checked_at": "2026-09-24T10:00:00", "seconds": 6.0},
        "round_test": {"command": None, "status": "green", "checked_at": "2026-09-24T10:00:00"},
        "sync_command": None,
        "plan_mode": "none",
        "plan_file": "",
        "plan_comment": None,
        "test_timeout": 120,
        "phase": "propose",
        "phases": {},
        "candidates": [],
        "plan": None,
        "items": [],
        "deferred_items": [],
        "whole_test": {"ran": False, "flags": [], "status": None, "seconds": None,
                       "head": None, "reverted": False},
        "verify_stats": {"items": 0, "seconds": 0.0},
        "fix_stats": {"launches": 0, "seconds": 0.0},
        "final_gate": {"fix_rounds": 0, "checks": []},
        "pending_push": False,
        "pending_drop": None,
        "history_written": False,
    }
    state.update(overrides)
    path = tmp_dir / f"cross-refactoring-rf{state_id}-state.json"
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def write_state(path: pathlib.Path, state: dict[str, Any]) -> None:
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------- 版 2 の git を使う結合テストの土台（#933） ----------

CALC = '''def add(a, b):
    return a + b


def total(values):
    result = 0
    for v in values:
        result = add(result, v)
    return result
'''

TEST_CALC = '''from src.calc import add


def test_add():
    assert add(1, 2) == 3
'''

TEST_TOTAL = '''from src.calc import total


def test_total():
    assert total([1, 2, 3]) == 6
'''


def git(*args: str, cwd: Any) -> "subprocess.CompletedProcess[str]":
    import subprocess

    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def commit_with_trailers(repo: pathlib.Path, message: str, trailers: dict[str, str]) -> str:
    git("add", "-A", cwd=repo)
    body = message + ("\n\n" + "\n".join(f"{k}: {v}" for k, v in trailers.items()) if trailers else "")
    git("commit", "-qm", body, cwd=repo)
    return git("rev-parse", "HEAD", cwd=repo).stdout.strip()


def item_trailers(item_id: str) -> dict[str, str]:
    return {"Item-Id": item_id, "Impl-Runtime": "claude", "Impl-Model": "default"}


def build_git_flow(tmp_path: pathlib.Path, monkeypatch: Any, patch_lib: Any,
                   env_tmp_dir: Any, **state_overrides: Any) -> dict[str, Any]:
    """書き込み用の作業ディレクトリ・`pytest` の起動口・版 2 の状態を用意する。

    `pytest` は PATH の先頭に置いた起動口で走らせる（限ったテストはシェルを通さずに
    語の並びで走るため）。公開（push）は差し替え、公開した HEAD を `pushed` に積む。
    """
    import os
    import sys

    work = tmp_path / "work"
    (work / "src").mkdir(parents=True)
    (work / "tests").mkdir()
    git("init", "-q", str(work), cwd=tmp_path)
    git("config", "user.email", "t@e.st", cwd=work)
    git("config", "user.name", "test", cwd=work)
    (work / "src" / "__init__.py").write_text("", encoding="utf-8")
    (work / "src" / "calc.py").write_text(CALC, encoding="utf-8")
    (work / "tests" / "test_calc.py").write_text(TEST_CALC, encoding="utf-8")
    # 内側の pytest が作る報告とキャッシュは追跡しない（作業ツリーを汚さない）。
    (work / ".gitignore").write_text("__pycache__/\n.pytest_cache/\nreports/\n", encoding="utf-8")
    commit_with_trailers(work, "init", {})

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    runner = bin_dir / "pytest"
    runner.write_text(
        f"#!/bin/sh\nexec {sys.executable} -m pytest -p no:cacheprovider \"$@\"\n", encoding="utf-8")
    runner.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    metrics = tmp_path / "metrics"
    monkeypatch.setenv("NDF_METRICS_DIR", str(metrics))

    pushed: list[str] = []
    patch_lib("push_head", lambda state: pushed.append(
        git("rev-parse", "HEAD", cwd=state["worktrees"]["work"]).stdout.strip()))
    path = make_state_v2(tmp_path, work, **state_overrides)
    env_tmp_dir(path)
    return {"work": work, "path": path, "pushed": pushed, "metrics": metrics}
