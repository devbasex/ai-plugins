"""担当へ渡すテストコマンドが、進行側の検証と同じであること（#880）。

進行側は群と修正コミットを `round_test_command(state)`（`round_test`、無ければ
`baseline_test`）で検証する。適用と修正の担当が別のコマンドを走らせると、担当が
通したつもりの変更を進行側が落とす（またはその逆）。最終ゲートの修正は全体の
テストで判定するため、`baseline_test` のままである。
"""
from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

from crossref_helpers import make_state

LAUNCH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "launch-cli.sh"
BASELINE = "pytest -q whole-suite"
ROUND_TEST = "pytest -q tests/unit/test_scope_only.py"


def _prompt(tmp_path, phase, round_test):
    over = {"baseline_test": {"command": BASELINE, "status": "green"},
            "rounds": [{"round": 1, "apply_round": 1}],
            "items": [{"item_id": "R1-001", "round": 1, "apply_round": 1}]}
    if round_test is not None:
        over["round_test"] = {"command": round_test}
    state_path = make_state(tmp_path, **over)
    (tmp_path / "work").mkdir(exist_ok=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "codex"
    stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)
    args = [str(LAUNCH), "codex", phase, "130"]
    if phase != "final-fix":
        args.append("1")
    subprocess.run(
        args,
        env={**os.environ,
             "CROSS_REFACTORING_TMP_DIR": str(state_path.parent),
             "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"},
        check=True, capture_output=True, text=True,
    )
    name = "codex-final-fix" if phase == "final-fix" else f"codex-{phase}-r1"
    return (state_path.parent / f"{name}-prompt.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("phase", ["apply", "fix"])
def test_apply_and_fix_run_the_round_test(tmp_path, phase):
    text = _prompt(tmp_path, phase, ROUND_TEST)
    assert ROUND_TEST in text
    assert BASELINE not in text
    assert "$RF_" not in text


@pytest.mark.parametrize("phase", ["apply", "fix"])
def test_apply_and_fix_fall_back_to_the_baseline(tmp_path, phase):
    """`round_test` を持たない状態ファイルは、進行側の検証と同じく baseline になる。"""
    text = _prompt(tmp_path, phase, None)
    assert BASELINE in text
    assert "$RF_" not in text


def test_final_fix_runs_the_whole_suite(tmp_path):
    text = _prompt(tmp_path, "final-fix", ROUND_TEST)
    assert BASELINE in text
    assert ROUND_TEST not in text
