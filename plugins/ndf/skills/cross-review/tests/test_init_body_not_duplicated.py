"""新規 init の副作用が公開入口から 1 回ずつ実行されることを固定する。"""
from __future__ import annotations

import argparse
import collections
import json


def test_new_init_runs_each_observable_effect_once(
    monkeypatch, tmp_path, state_mod, capsys,
) -> None:
    """メタデータ取得、worktree 作成、コメント取得、state 書込は各 1 回。"""
    pr = 5490
    repo = "o/r"
    worktree = tmp_path / "worktree"
    tmp_dir = tmp_path / "cross-review"
    calls: list[str] = []

    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_dir))
    monkeypatch.setenv("NDF_SKIP_AUTH_CHECK", "1")
    monkeypatch.setattr(state_mod, "_repo_from_git", lambda: repo)

    def fetch_metadata(number, resolved_repo=None):
        calls.append("metadata")
        return state_mod.PrMetadata(
            repo, "other", "feat/x", "abc123", "develop", False, 4000, None
        )

    def create_worktree(path, number, head_branch):
        calls.append("worktree")
        worktree.mkdir()

    def fetch_comments(resolved_repo, number, path, strict=False):
        calls.append("comments")
        path.write_text("", encoding="utf-8")
        return None

    real_write_state = state_mod._write_state

    def write_state(path, state):
        calls.append("state")
        real_write_state(path, state)

    monkeypatch.setattr(state_mod, "_fetch_pr_metadata", fetch_metadata)
    monkeypatch.setattr(state_mod, "_fetch_changed_files", lambda number, resolved_repo: [])
    monkeypatch.setattr(state_mod, "_sh", lambda command, check=True: "viewer")
    monkeypatch.setattr(state_mod, "_create_worktree", create_worktree)
    monkeypatch.setattr(state_mod, "_fetch_existing_comments", fetch_comments)
    monkeypatch.setattr(state_mod, "_write_state", write_state)

    args = argparse.Namespace(
        pr=pr, max_rounds=12, rotate_after=8, only=None, include=None, exclude=None,
        require_all=None, worktree=str(worktree), focus=None,
        extra_instructions_file=None, host="claude", verify_command=None,
        verify_exit_code=None,
    )
    state_mod.cmd_init(args)

    assert collections.Counter(calls) == {
        "metadata": 1, "worktree": 1, "comments": 1, "state": 1,
    }
    saved = json.loads(
        (tmp_dir / f"cross-review-pr{pr}-state.json").read_text(encoding="utf-8")
    )
    assert saved["current_pr"] == pr
    output = capsys.readouterr()
    assert output.out.count(f"PR={pr}") == 1
    assert output.err.count("✅ state 初期化") == 1
