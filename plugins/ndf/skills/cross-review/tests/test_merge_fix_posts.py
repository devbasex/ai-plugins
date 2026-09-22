"""修正の取り込みが、送信と返信・決着・まとめを行う（#730 #585 #676）。

**修正の担当はコミットまでを行い、送らない。** 取り込みが現在の頭を指定して送り、
報告されたコミットが送り先に載ったことを確かめてから、返信・決着・まとめを
待ち行列へ積んで流す。まとめの参照は投稿の応答から記録へ書く。

| 何を確かめるか | 受け入れ条件 |
| --- | --- |
| 返信・決着・まとめが積まれて流れる | AC7 |
| 送信は取り込む側が行う | AC8 |
| 報告されたコミットが送り先に無ければ止まる | AC9 |
| 同じ共通層を使う | AC21 |
"""
from __future__ import annotations

import argparse
import json
import pathlib

import pytest

PR = 5850
REPO = "o/r"


@pytest.fixture()
def tmp_dir(monkeypatch, tmp_path, state_mod):
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    return tmp_path


def _seed(tmp_dir: pathlib.Path) -> None:
    state = {
        "current_pr": PR, "repo": REPO, "viewer_login": "takemi",
        "worktree_path": str(tmp_dir), "head_branch": "feat/x",
        "rounds": [{"round": 2, "pr": PR, "started_at": "2026-01-01T00:00:00+00:00"}],
        "deferred_nits": [], "final": None,
    }
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(json.dumps(state))


def _fix(tmp_dir: pathlib.Path) -> pathlib.Path:
    path = tmp_dir / f"fix-pr{PR}-result.json"
    path.write_text(json.dumps({
        "pr": PR, "fix_commit": "abc1234", "ci_status": "SUCCESS", "fixed_count": 1,
        "by_severity": {"major": 1},
        "resolved_threads": [{"thread_id": "PRRT_a", "comment_id": 11}],
        "deferred": [], "rejected": [],
    }), encoding="utf-8")
    return path


def _state(tmp_dir: pathlib.Path) -> dict:
    return json.loads((tmp_dir / f"cross-review-pr{PR}-state.json").read_text())


@pytest.fixture()
def calls(monkeypatch, state_mod):
    """送信と投稿の呼び出しを記録する。共通層の口をそのまま差し替える。"""
    rp = state_mod.result_posts
    seen: dict = {"push": [], "post": []}

    def push(worktree, head, commit):
        seen["push"].append((str(worktree), head, commit))
        return rp.PushResult(seen.get("push_ok", True), True,
                             seen.get("push_ok", True), "")

    def post(queue, result_path, repo, pr, round_no=None, actor=None):
        items = rp.fix_posts(result_path, repo, pr, round_no)
        seen["post"].append([i["kind"] for i in items])
        return rp.FixOutcome("https://x/pull/5850#issuecomment-9", 1, 1, 0, False, "")

    monkeypatch.setattr(state_mod, "GITHUB",
                        state_mod.GITHUB._replace(push_fix=push, post_fix=post))
    return seen


def test_the_take_in_pushes_the_head_and_posts_the_replies(tmp_dir, state_mod, calls):
    _seed(tmp_dir)
    _fix(tmp_dir)

    state_mod.cmd_merge_fix(argparse.Namespace(pr=PR, file=None))

    assert calls["push"] == [(str(tmp_dir), "feat/x", "abc1234")]
    assert calls["post"] == [["review-reply", "thread-resolve", "pr-comment"]]
    fix = _state(tmp_dir)["rounds"][-1]["fix"]
    assert fix["summary_comment_url"] == "https://x/pull/5850#issuecomment-9"


def test_the_take_in_stops_when_the_commit_is_not_on_the_branch(
        tmp_dir, state_mod, calls):
    """報告されたコミットが送り先に載っていなければ、記録も投稿もせずに止まる（AC9）。"""
    _seed(tmp_dir)
    _fix(tmp_dir)
    calls["push_ok"] = False

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_merge_fix(argparse.Namespace(pr=PR, file=None))

    assert e.value.code != 0
    assert calls["post"] == []
    assert "fix" not in _state(tmp_dir)["rounds"][-1]
