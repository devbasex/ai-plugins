"""cross-refactoring のテストが共有する補助関数。

conftest.py へ置くと、複数の Skill のテストを同時に実行したときに `conftest` という
モジュール名が衝突し、別の Skill の conftest が解決されてしまう。直接 import する
補助はこの固有名のモジュールへ置く。
"""

from __future__ import annotations

import datetime as _dt
import json
import pathlib
import subprocess
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
        "worktrees": {"work": str(tmp_path / "work"), **{r: str(tmp_path / r) for r in runtimes}},
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
        "baseline_test": {"command": "pytest -q", "status": "green", "checked_at": "2026-08-15T00:00:00"},
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


# 状態ファイルの既定の戦略（#1334）。全体テストは JUnit を `reports/junit.xml`（`build_git_flow` が無視する置き場）へ
# 書き、範囲テストは `{paths}` の雛形から組む。
JUNIT_PATH = "reports/junit.xml"
JUNIT_OPTS = f"-o junit_family=xunit1 --junitxml={JUNIT_PATH}"
WHOLE_COMMAND = f"pytest -q {JUNIT_OPTS}"
SCOPE_COMMAND = f"pytest -q {JUNIT_OPTS} {{paths}}"


def strategy_state(name: str = "local-full", source: str = "test.strategy", *, junit: bool = True, **over: Any) -> dict[str, Any]:
    """状態の `strategy`。`junit=False` なら JUnit を書かない suite（見分けが走らせ直しへ落ちる経路）。"""
    whole = WHOLE_COMMAND if junit else "pytest -q"
    scope = SCOPE_COMMAND if junit else "pytest -q {paths}"
    state = {
        "name": name,
        "source": source,
        "suites": [{"name": "pytest", "command": whole, "scope_command": scope, "junit": JUNIT_PATH if junit else None, "paths": ["."]}],
        "round_command": None,
        "ci": {"check": "tests", "checks": ["tests"], "junit_artifacts": None} if name == "local-scoped-ci-whole" else None,
        "notes": [],
    }
    state.update(over)
    return state


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
        # **開始は今の少し前に置く。** 固定の時刻にすると、その時刻から想定最大時間が
        # 過ぎた後に走らせたテストだけが、修正の締め切りと最終ゲートの打ち切りで落ちる。
        "started_at": (_dt.datetime.now() - _dt.timedelta(minutes=1)).isoformat(timespec="seconds"),
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
        "strategy": strategy_state(),
        "baseline_test": {
            "mode": "whole",
            "command": WHOLE_COMMAND,
            "status": "green",
            "checked_at": "2026-09-24T10:00:00",
            "seconds": 6.0,
            "existing_failures": [],
            "existing_failures_reason": None,
        },
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
        "whole_test": {"ran": False, "flags": [], "status": None, "seconds": None, "head": None, "reverted": False},
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

CALC = """def add(a, b):
    return a + b


def total(values):
    result = 0
    for v in values:
        result = add(result, v)
    return result
"""

TEST_CALC = """from src.calc import add


def test_add():
    assert add(1, 2) == 3
"""

TEST_TOTAL = """from src.calc import total


def test_total():
    assert total([1, 2, 3]) == 6
"""


def planned_item(item_id: str, rank: int, **over: Any) -> dict[str, Any]:
    """計画済み（`planned`）の項目。`src/calc.py#add` の long_method を extract_method で直す形で、差分だけを `over` で渡す。"""
    return {
        "id": item_id,
        "rank": rank,
        "path": "src/calc.py",
        "symbol": "add",
        "smell": "long_method",
        "technique": "extract_method",
        "severity": "major",
        "proposed_by": ["codex"],
        "tier": "high",
        "risk": False,
        "tests": [],
        "test_targets": ["tests/test_calc.py"],
        "command": ["pytest", "-q", "tests/test_calc.py"],
        "command_source": "targets",
        "estimate": {"test": 0.0, "implement": 1.3, "verify": 0.2},
        "start_deadline": "2099-01-01T00:00:00+00:00",
        "test_start_deadline": None,
        "status": "planned",
        "commits": {"test": None, "implement": None, "fix": []},
        "seconds": {},
        "fix_count": 0,
        "danger": [],
        "estimated_diff_lines": 20,
        **over,
    }


def plan_entry(**over: Any) -> dict[str, Any]:
    """改修計画に載せる検証済みの項目。`src/foo.py#Foo.handle` を extract_method で直す形で、差分だけを `over` で渡す。"""
    return {
        "id": "I-001",
        "rank": 1,
        "path": "src/foo.py",
        "symbol": "Foo.handle",
        "smell": "long_method",
        "technique": "extract_method",
        "severity": "major",
        "tier": "high",
        "rationale": "1 関数が 6 つの処理を通しで行っている",
        "plan": "1. 範囲の確定を切り出す",
        "tests": [],
        "estimated_diff_lines": 40,
        "proposed_by": ["codex", "agy"],
        "status": "verified",
        "commits": {"test": None, "implement": "abc1234", "fix": []},
        **over,
    }


def git(*args: str, cwd: Any) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def commit_with_trailers(repo: pathlib.Path, message: str, trailers: dict[str, str]) -> str:
    git("add", "-A", cwd=repo)
    body = message + ("\n\n" + "\n".join(f"{k}: {v}" for k, v in trailers.items()) if trailers else "")
    git("commit", "-qm", body, cwd=repo)
    return git("rev-parse", "HEAD", cwd=repo).stdout.strip()


def item_trailers(item_id: str) -> dict[str, str]:
    return {"Item-Id": item_id, "Impl-Runtime": "claude", "Impl-Model": "default"}


def build_git_flow(tmp_path: pathlib.Path, monkeypatch: Any, patch_lib: Any, env_tmp_dir: Any, **state_overrides: Any) -> dict[str, Any]:
    """書き込み用の作業ディレクトリ・`pytest` の起動口・版 2 の状態を用意する。

    `pytest` は PATH の先頭に置いた起動口で走らせる（範囲テストはシェルを通さずに
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
    runner.write_text(f'#!/bin/sh\nexec {sys.executable} -m pytest -p no:cacheprovider "$@"\n', encoding="utf-8")
    runner.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    metrics = tmp_path / "metrics"
    monkeypatch.setenv("NDF_METRICS_DIR", str(metrics))

    pushed: list[str] = []
    patch_lib("push_head", lambda state: pushed.append(git("rev-parse", "HEAD", cwd=state["worktrees"]["work"]).stdout.strip()))
    path = make_state_v2(tmp_path, work, **state_overrides)
    env_tmp_dir(path)
    return {"work": work, "path": path, "pushed": pushed, "metrics": metrics}
