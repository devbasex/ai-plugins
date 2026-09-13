"""追加レビュー観点をプロンプトへ描画する分岐の現状固定。"""
import json
import os
import pathlib
import subprocess

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts/launch-reviewer.sh"
PR = 535


@pytest.mark.parametrize("instructions", ["認証境界を確認する。\n権限不足の経路も確認する。", ""])
def test_extra_review_block_follows_state_instructions(tmp_path, instructions):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    tmp_dir = worktree / ".cross_review"
    tmp_dir.mkdir()
    state = {
        "current_pr": PR,
        "repo": "o/r",
        "worktree_path": str(worktree),
        "review_instructions": instructions,
        "rounds": [{"round": 1, "head_sha": "1" * 40}],
    }
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(
        json.dumps(state), encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "codex"
    stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)

    subprocess.run(
        ["bash", str(SCRIPT), "codex", str(PR), "1"],
        env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
             "CROSS_REVIEW_TMP_DIR": str(tmp_dir)},
        check=True, capture_output=True, text=True, timeout=10,
    )

    prompt = (tmp_dir / f"codex-review-pr{PR}-prompt.md").read_text(encoding="utf-8")
    if instructions:
        assert "追加レビュー観点" in prompt
        assert instructions in prompt
    else:
        assert "追加レビュー観点" not in prompt
