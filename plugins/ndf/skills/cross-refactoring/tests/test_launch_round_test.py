"""担当へ渡すテストコマンドが、進行側の検証と同じであること（#880 / #933）。

進行側は項目を**項目ごとに組み立てた語の並び**（`items[].command`）で検証する。実装と
修正の担当が別のコマンドを走らせると、担当が通したつもりの変更を進行側が落とす
（またはその逆）。計画の担当には、組み立ての元になる `round_test`（無ければ
`baseline_test`）を渡す。最終ゲートの修正は全体のテストで判定するため、`baseline_test`
のままである。

文言は照合しない。状態の値がプロンプトへ渡ったかだけを見る。
"""
from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

from crossref_helpers import make_state_v2

LAUNCH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "launch-cli.sh"
BASELINE = "pytest -q whole-suite"
ROUND_TEST = "pytest -q tests/unit"
ITEM_COMMAND = ["pytest", "-q", "tests/unit/test_scope_only.py"]


def _item(status: str) -> dict:
    return {"id": "I-001", "rank": 1, "path": "src/a.py", "symbol": "Foo", "smell": "long_method",
            "technique": "extract_method", "rationale": "r", "plan": "p", "tests": [],
            "test_targets": ["tests/unit/test_scope_only.py"], "command": ITEM_COMMAND,
            "estimate": {"test": 0.0, "implement": 1.3, "verify": 0.2},
            "start_deadline": "2099-01-01T00:00:00+00:00", "test_start_deadline": None,
            "status": status, "fix_count": 0, "last_log": "/tmp/verify-I-001.log"}


def _prompt(tmp_path, phase, *, round_test=ROUND_TEST, status="planned"):
    work = tmp_path / "work"
    for name in ("work", "codex"):
        (tmp_path / name).mkdir(exist_ok=True)
    state_path = make_state_v2(
        tmp_path, work, runtimes=["codex", "kiro"],
        baseline_test={"command": BASELINE, "status": "green"},
        round_test={"command": round_test, "status": "green"},
        candidates=[{"path": "src/a.py", "symbol": "Foo", "smell": "long_method",
                     "technique": "extract_method", "severity": "major", "rationale": "r",
                     "plan": "p", "proposed_by": ["codex"]}],
        items=[_item(status)])
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "codex"
    stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)
    subprocess.run(
        [str(LAUNCH), "codex", phase, "130"],
        env={**os.environ,
             "CROSS_REFACTORING_TMP_DIR": str(state_path.parent),
             "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"},
        check=True, capture_output=True, text=True,
    )
    name = "codex-final-fix" if phase == "final-fix" else f"codex-{phase}-rf130"
    return (state_path.parent / f"{name}-prompt.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("phase,status", [("implement", "planned"), ("fix", "failing")])
def test_implement_and_fix_run_the_item_command(tmp_path, phase, status):
    """実装と修正は、進行側が検証に使う項目の語の並びを受け取る。"""
    text = _prompt(tmp_path, phase, status=status)
    assert " ".join(ITEM_COMMAND) in text
    assert BASELINE not in text
    assert "$RF_" not in text


def test_plan_receives_the_round_test_to_build_from(tmp_path):
    text = _prompt(tmp_path, "plan")
    assert ROUND_TEST in text
    assert "$RF_" not in text


def test_plan_falls_back_to_the_baseline(tmp_path):
    """`round_test` を省いた実行は、組み立ての元が `baseline_test` になる。"""
    text = _prompt(tmp_path, "plan", round_test=None)
    assert BASELINE in text
    assert "$RF_" not in text


def test_final_fix_runs_the_whole_suite(tmp_path):
    text = _prompt(tmp_path, "final-fix")
    assert BASELINE in text
    assert " ".join(ITEM_COMMAND) not in text


def test_fix_of_a_whole_test_failure_runs_only_the_failed_tests(tmp_path):
    """全体のテストで落ちた項目（決定 22）は、落ちたテストだけを走らせ直すコマンドを受け取る。"""
    work = tmp_path / "work"
    for name in ("work", "codex"):
        (tmp_path / name).mkdir(exist_ok=True)
    item = {**_item("failing"), "whole_test_command": ["pytest", "-q", "tests/t.py::test_whole"]}
    state_path = make_state_v2(tmp_path, work, runtimes=["codex", "kiro"],
                               baseline_test={"command": BASELINE, "status": "green"},
                               items=[item])
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "codex").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (bin_dir / "codex").chmod(0o755)
    subprocess.run([str(LAUNCH), "codex", "fix", "130"],
                   env={**os.environ, "CROSS_REFACTORING_TMP_DIR": str(state_path.parent),
                        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"},
                   check=True, capture_output=True, text=True)
    text = (state_path.parent / "codex-fix-rf130-prompt.md").read_text(encoding="utf-8")
    assert "pytest -q tests/t.py::test_whole" in text
    assert " ".join(ITEM_COMMAND) not in text
