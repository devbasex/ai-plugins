"""既存コメントスナップショットの埋め込み境界を現状固定する。"""
import json
import os
import pathlib
import subprocess

import pytest


SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts/launch-reviewer.sh"
PR = 6003


@pytest.mark.parametrize("comments, expected", [
    ("a.py:1 の指摘\nb.py:2 の指摘\n", "a.py:1 の指摘\nb.py:2 の指摘"),
    ("", "(なし)"),
    (None, "(なし)"),
], ids=["nonempty", "empty", "missing"])
def test_existing_comments_are_inlined_or_replaced_with_none(tmp_path, comments, expected):
    state = {
        "current_pr": PR, "repo": "o/r", "worktree_path": str(tmp_path),
        "rounds": [{"round": 1, "head_sha": "a" * 40}],
    }
    (tmp_path / f"cross-review-pr{PR}-state.json").write_text(json.dumps(state))
    if comments is not None:
        (tmp_path / f"cross-review-pr{PR}-existing-comments.txt").write_text(comments)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "codex"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)

    result = subprocess.run(
        ["bash", str(SCRIPT), "codex", str(PR), "1"],
        capture_output=True, text=True,
        env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
             "CROSS_REVIEW_TMP_DIR": str(tmp_path)},
    )

    assert result.returncode == 0, result.stderr
    prompt = (tmp_path / f"codex-review-pr{PR}-prompt.md").read_text()
    snapshot = prompt.split("## 既存コメントスナップショット（重複指摘禁止）\n", 1)[1]
    snapshot = snapshot.split("```\n", 2)[1]
    assert snapshot == expected + "\n"


@pytest.mark.parametrize("event_downgrade, expected_line", [
    (True, "- event_downgrade: true"),
    (False, "- event_downgrade: false"),
], ids=["downgrade-true", "downgrade-false"])
def test_event_downgrade_is_reflected_in_prompt(tmp_path, event_downgrade, expected_line):
    """現状固定: state.json の event_downgrade がプロンプトに反映される。"""
    state = {
        "current_pr": PR, "repo": "o/r", "worktree_path": str(tmp_path),
        "event_downgrade": event_downgrade,
        "rounds": [{"round": 1, "head_sha": "a" * 40}],
    }
    (tmp_path / f"cross-review-pr{PR}-state.json").write_text(json.dumps(state))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "codex"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)

    result = subprocess.run(
        ["bash", str(SCRIPT), "codex", str(PR), "1"],
        capture_output=True, text=True,
        env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
             "CROSS_REVIEW_TMP_DIR": str(tmp_path)},
    )

    assert result.returncode == 0, result.stderr
    prompt = (tmp_path / f"codex-review-pr{PR}-prompt.md").read_text()
    assert expected_line in prompt

