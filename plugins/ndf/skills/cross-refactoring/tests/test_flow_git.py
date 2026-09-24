"""5 フェーズを**実際の git とテスト**で通す（#933 の AC4 AC7 AC10 AC11 AC13 AC16b AC17）。

CLI は起動しない。担当が残すもの（結果ファイルとトレーラー付きのコミット）をテストが
作り、進行側の副コマンドを順に呼ぶ。公開（push）だけは差し替える。

履歴は `NDF_METRICS_DIR` を `tmp_path` へ向けて書かせ、利用者の状態ディレクトリを
触らない（#938）。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys

import pytest

from crossref_helpers import make_state_v2, read_state, write_result, write_state

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


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def _commit(repo, message, trailers):
    _git("add", "-A", cwd=repo)
    body = message + "\n\n" + "\n".join(f"{k}: {v}" for k, v in trailers.items())
    _git("commit", "-qm", body, cwd=repo)
    return _git("rev-parse", "HEAD", cwd=repo).stdout.strip()


def _trailers(item_id):
    return {"Item-Id": item_id, "Impl-Runtime": "claude", "Impl-Model": "default"}


@pytest.fixture
def flow(tmp_path, monkeypatch, refactor, patch_lib, env_tmp_dir):
    """書き込み用の作業ディレクトリ・`pytest` の起動口・版 2 の状態を用意する。"""
    work = tmp_path / "work"
    (work / "src").mkdir(parents=True)
    (work / "tests").mkdir()
    _git("init", "-q", str(work), cwd=tmp_path)
    _git("config", "user.email", "t@e.st", cwd=work)
    _git("config", "user.name", "test", cwd=work)
    (work / "src" / "__init__.py").write_text("", encoding="utf-8")
    (work / "src" / "calc.py").write_text(CALC, encoding="utf-8")
    (work / "tests" / "test_calc.py").write_text(TEST_CALC, encoding="utf-8")
    (work / ".gitignore").write_text("__pycache__/\n.pytest_cache/\nreports/\n", encoding="utf-8")
    _commit(work, "init", {})

    # `pytest` を既知の実行器として走らせる。**シェルを通さない**（AC10b）ため、PATH に置く。
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    runner = bin_dir / "pytest"
    runner.write_text(f"#!/bin/sh\nexec {sys.executable} -m pytest -p no:cacheprovider \"$@\"\n",
                      encoding="utf-8")
    runner.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    metrics = tmp_path / "metrics"
    monkeypatch.setenv("NDF_METRICS_DIR", str(metrics))
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)

    pushed = []
    patch_lib("push_head", lambda state: pushed.append(
        _git("rev-parse", "HEAD", cwd=state["worktrees"]["work"]).stdout.strip()))
    path = make_state_v2(tmp_path, work)
    env_tmp_dir(path)
    return {"work": work, "path": path, "pushed": pushed, "metrics": metrics}


def _call(refactor_module, name, **kwargs):
    args = argparse.Namespace(id=130, **kwargs)
    return getattr(refactor_module, name)(args)


def _propose(flow):
    proposal = {"path": "src/calc.py", "symbol": "total", "smell": "one_by_one_iteration",
                "technique": "replace_with_bulk_operation", "severity": "major",
                "rationale": "足し込みを 1 件ずつ回している", "plan": "sum() へ置き換える",
                "estimated_diff_lines": 6}
    other = {"path": "src/calc.py", "symbol": "add", "smell": "dead_code",
             "technique": "remove_dead_code", "severity": "minor", "rationale": "r",
             "plan": "p", "estimated_diff_lines": 2}
    write_result(flow["path"], "codex-propose-rf130", {"items": [proposal, other]})
    write_result(flow["path"], "kiro-propose-rf130", {"items": [proposal]})
    write_result(flow["path"], "claude-propose-rf130", {"items": [{**proposal, "smell": "日本語"}]})


def _plan(flow, **total_answer):
    answer = {"key": "src/calc.py#total#one_by_one_iteration", "tier": "high",
              "tests": ["tests/test_total.py"], "test_targets": ["tests/test_total.py"],
              "merge_into": None, "risk": False, **total_answer}
    write_result(flow["path"], "claude-plan-rf130", {"items": [
        answer,
        {"key": "src/calc.py#add#dead_code", "tier": "low", "tests": [], "test_targets": [],
         "merge_into": None, "risk": False},
    ]})


def test_the_five_phases_run_once_and_append_one_history_row(flow, cmd_propose, cmd_plan,
                                                             cmd_setup, cmd_implement,
                                                             cmd_converge, cmd_gate, cmd_report):
    work, path = flow["work"], flow["path"]
    _propose(flow)
    _call(cmd_propose, "cmd_merge_proposals")
    state = read_state(path)
    # 語彙外の提案は見送られ、鍵が同じ提案は賛同した者を足し合わせる。
    assert [c["id"] for c in state["candidates"]] == ["C-001", "C-002"]
    assert state["candidates"][0]["proposed_by"] == ["codex", "kiro"]
    assert [d["defer_reason"] for d in state["deferred_items"]] == ["vocabulary"]

    _plan(flow)
    _call(cmd_plan, "cmd_merge_plan")
    state = read_state(path)
    # `add` は round_test が無く test_targets も空なので no_target で見送られる（AC10b）。
    assert [i["id"] for i in state["items"]] == ["I-001"]
    item = state["items"][0]
    assert item["command"] == ["pytest", "-q", "tests/test_total.py"]
    assert item["command_source"] == "targets"
    assert {d["defer_reason"] for d in state["deferred_items"]} == {"vocabulary", "no_target"}
    assert state["plan"]["table_source"] == "defaults"

    _call(cmd_setup, "cmd_start_phase", phase="add-tests")
    (work / "tests" / "test_total.py").write_text(TEST_TOTAL, encoding="utf-8")
    test_sha = _commit(work, "Test: total", _trailers("I-001"))
    _call(cmd_implement, "cmd_merge_tests")
    state = read_state(path)
    assert state["items"][0]["status"] == "tested"
    assert state["items"][0]["commits"]["test"] == test_sha

    _call(cmd_setup, "cmd_start_phase", phase="implement")
    (work / "src" / "calc.py").write_text(CALC.replace(
        "    result = 0\n    for v in values:\n        result = add(result, v)\n    return result\n",
        "    return sum(values)\n"), encoding="utf-8")
    impl_sha = _commit(work, "Refactor: total", _trailers("I-001"))
    _call(cmd_implement, "cmd_merge_implement")
    state = read_state(path)
    assert state["items"][0]["status"] == "implemented"
    assert state["items"][0]["commits"]["implement"] == impl_sha

    _call(cmd_converge, "cmd_verify")
    state = read_state(path)
    assert state["items"][0]["status"] == "verified"
    assert state["phase"] == "final"

    _call(cmd_gate, "cmd_final_gate")
    _call(cmd_report, "cmd_finalize", review_status=None)
    state = read_state(path)
    assert state["final_gate"]["status"] == "passed"
    assert state["history_written"] is True
    rows = (flow["metrics"] / "acme--demo" / "cross-refactoring-allocation.jsonl").read_text(
        encoding="utf-8").splitlines()
    assert len(rows) == 1
    row = json.loads(rows[0])
    assert row["kinds"]["test"]["count"] == 1
    assert row["kinds"]["structure/replace_with_bulk_operation"]["count"] == 1
    # 履歴は 1 実行に 1 行。叩き直しても二重に書かない。
    _call(cmd_report, "cmd_finalize", review_status=None)
    assert len((flow["metrics"] / "acme--demo" / "cross-refactoring-allocation.jsonl")
               .read_text(encoding="utf-8").splitlines()) == 1

