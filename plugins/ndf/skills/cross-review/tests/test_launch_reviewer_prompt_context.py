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


def _prompt(tmp_path, **state_over) -> str:
    state = {
        "current_pr": PR, "repo": "o/r", "worktree_path": str(tmp_path),
        "event_downgrade": True,
        "rounds": [{"round": 1, "head_sha": "a" * 40}],
    }
    state.update(state_over)
    (tmp_path / f"cross-review-pr{PR}-state.json").write_text(json.dumps(state))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
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
    return (tmp_path / f"codex-review-pr{PR}-prompt.md").read_text()


# ---------------- 担当は結果だけを残す（#730） ----------------


@pytest.mark.parametrize("word", [
    "gh api",            # 投稿の呼び出し
    "event_downgrade",   # 判定の値の指定（格下げは投稿する側が決める）
    "posted_as",
    "comments_count",
    "review_url",
    "post_error",
    "line could not be resolved",   # インラインの組み立て（422 への対処）
    "post: submit review",
])
def test_the_prompt_has_no_step_to_post(tmp_path, word) -> None:
    """担当へ渡すプロンプトに、レビューを投稿する手順が 1 つも無い（AC1・AC2）。"""
    assert word.lower() not in _prompt(tmp_path).lower()


def test_the_prompt_asks_only_for_the_note_and_the_result(tmp_path) -> None:
    """書かせるのは指摘の控えと結果ファイルの 2 つだけ。結果は判定と重要度別の件数（AC2）。"""
    prompt = _prompt(tmp_path)
    assert f"codex-review-pr{PR}-round1-payload.json" in prompt
    assert f"codex-review-pr{PR}-result.json" in prompt
    assert '"event"' in prompt and '"by_severity"' in prompt


def test_the_prompt_asks_to_rename_the_note_before_the_result(tmp_path) -> None:
    """一時の名前で書き、控えを先・結果ファイルを後に改名させる（AC3）。"""
    prompt = _prompt(tmp_path)
    note_tmp = f"codex-review-pr{PR}-round1-payload.json.tmp"
    result_tmp = f"codex-review-pr{PR}-result.json.tmp"
    assert note_tmp in prompt and result_tmp in prompt
    first_mv = prompt.index(f"mv ") 
    assert prompt.index(note_tmp, first_mv) < prompt.index(result_tmp, first_mv)


def test_leftover_temporary_files_are_removed_before_launch(tmp_path) -> None:
    """前の起動が残した一時の名前のファイルを持ち越さない。"""
    for name in (f"codex-review-pr{PR}-result.json.tmp",
                 f"codex-review-pr{PR}-round1-payload.json.tmp"):
        (tmp_path / name).write_text("{}")
    _prompt(tmp_path)
    assert not (tmp_path / f"codex-review-pr{PR}-result.json.tmp").exists()
    assert not (tmp_path / f"codex-review-pr{PR}-round1-payload.json.tmp").exists()

