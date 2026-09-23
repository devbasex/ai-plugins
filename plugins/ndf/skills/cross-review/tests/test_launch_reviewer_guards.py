"""席の形に合わない名前を副作用なしで拒否する入口の現状固定。"""
import os
import pathlib
import subprocess

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts/launch-reviewer.sh"


def test_a_name_outside_the_seat_pattern_exits_before_writing_files(tmp_path):
    result = subprocess.run(
        ["bash", str(SCRIPT), "bogus", "1", "1"],
        env={**os.environ, "CROSS_REVIEW_TMP_DIR": str(tmp_path)},
        capture_output=True, text=True, timeout=10,
    )

    assert result.returncode == 1
    assert "受け付けられない席の名前です" in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_missing_state_exits_before_writing_files(tmp_path):
    result = subprocess.run(
        ["bash", str(SCRIPT), "codex", "1", "1"],
        env={**os.environ, "CROSS_REVIEW_TMP_DIR": str(tmp_path)},
        capture_output=True, text=True, timeout=10,
    )

    assert result.returncode == 1
    assert "state.json not found" in result.stderr
    assert not (tmp_path / "codex-review-pr1-prompt.md").exists()
    assert list(tmp_path.iterdir()) == []



@pytest.mark.parametrize("args,missing", [
    ([], "seat required"),
    (["codex"], "STATE_PR required"),
    (["codex", "1"], "ROUND required"),
])
def test_missing_positional_arguments_exit_before_writing_files(tmp_path, args, missing):
    """位置引数が足りないと、不足した名前を出して非 0 で止まる（R2-004 の現状固定）。"""
    result = subprocess.run(
        ["bash", str(SCRIPT), *args],
        env={**os.environ, "CROSS_REVIEW_TMP_DIR": str(tmp_path)},
        capture_output=True, text=True, timeout=10,
    )

    assert result.returncode == 1
    assert missing in result.stderr
    assert list(tmp_path.iterdir()) == []
