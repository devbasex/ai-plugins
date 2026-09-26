"""巻き直しの後に、状態ファイルの枝名が新しい値を指すことのテスト（#244）。

巻き直し（`rotate-pr.sh`）の `squash` は `<枝名>-r<時刻>` という新しい枝を作って push する。
`set-current-pr` が `current_pr` と `pr_history` だけを更新していたため、状態ファイルの
`head_branch` は `init` が書いた値のまま残っていた。巻き直しの直後に再開すると、
巻き直し前の枝へ作業ツリーを合わせようとする。

| 枝名の決め方 | 条件 |
| --- | --- |
| 引数で受け取った値 | 骨組みが `--head-branch` を渡したとき |
| 新しい Pull Request から取り直す | 引数が無いとき |
| 既存の値を残す | 取り直せないとき。取れなかったことを出力へ残す |
"""
from __future__ import annotations

import argparse
import json
import pathlib

import pytest
import review_lib
import review_lib.commands.loop
import gh_call  # review_lib が sys.path に足したライブラリの置き場から読む

PR = 4244
NEW_PR = 4299
OLD_BRANCH = "feature/foo"
NEW_BRANCH = "feature/foo-r123456"


@pytest.fixture()
def tmp_dir(monkeypatch, tmp_path, state_mod):
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    return tmp_path


def _seed(tmp_dir: pathlib.Path) -> None:
    state = {
        "current_pr": PR,
        "repo": "o/r",
        "head_branch": OLD_BRANCH,
        "rounds": [{"round": 1, "pr": PR}],
        "pr_history": [{"pr": PR, "opened_at": "x", "closed_at": None, "rounds": 0}],
        "final": None,
    }
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(json.dumps(state))


def _state(tmp_dir: pathlib.Path) -> dict:
    return json.loads((tmp_dir / f"cross-review-pr{PR}-state.json").read_text())


def _args(**over) -> argparse.Namespace:
    values = {"pr": PR, "new_pr": NEW_PR, "head_branch": None}
    values.update(over)
    return argparse.Namespace(**values)


def test_the_given_branch_is_written_back(tmp_dir, state_mod, monkeypatch):
    _seed(tmp_dir)
    monkeypatch.setattr(
        gh_call, "RUNNER", lambda *a, **k: pytest.fail("引数があるのに GitHub を呼んでいる")
    )

    review_lib.commands.loop.cmd_set_current_pr(_args(head_branch=NEW_BRANCH))

    st = _state(tmp_dir)
    assert st["head_branch"] == NEW_BRANCH
    assert st["current_pr"] == NEW_PR


def test_the_branch_is_read_back_from_the_pull_request(tmp_dir, state_mod, monkeypatch):
    _seed(tmp_dir)
    calls: list[list[str]] = []
    monkeypatch.setattr(
        gh_call, "RUNNER",
        lambda args, stdin=None, cwd=None: calls.append(list(args)) or gh_call.GhResult(0, NEW_BRANCH + "\n", "")
    )

    review_lib.commands.loop.cmd_set_current_pr(_args())

    assert _state(tmp_dir)["head_branch"] == NEW_BRANCH
    assert calls and str(NEW_PR) in calls[0]


def test_the_previous_branch_is_kept_when_the_lookup_fails(tmp_dir, state_mod, monkeypatch, capsys):
    """取り直せないことで進行を止めない。次のラウンドの同期が書き戻す。"""
    _seed(tmp_dir)

    def boom(args, stdin=None, cwd=None):
        return gh_call.GhResult(1, "", "network")

    monkeypatch.setattr(gh_call, "RUNNER", boom)

    review_lib.commands.loop.cmd_set_current_pr(_args())

    st = _state(tmp_dir)
    assert st["head_branch"] == OLD_BRANCH
    assert st["current_pr"] == NEW_PR
    assert "取得できませんでした" in capsys.readouterr().err


def test_an_empty_lookup_keeps_the_previous_branch(tmp_dir, state_mod, monkeypatch):
    _seed(tmp_dir)
    monkeypatch.setattr(gh_call, "RUNNER", lambda args, stdin=None, cwd=None: gh_call.GhResult(0, "  \n", ""))

    review_lib.commands.loop.cmd_set_current_pr(_args())

    assert _state(tmp_dir)["head_branch"] == OLD_BRANCH


def test_only_the_current_pr_entry_is_closed_when_history_has_past_prs(
    tmp_dir, state_mod, monkeypatch
):
    """現状固定（R2-005）。過去に閉じた PR を含む履歴で、直前の現在 PR だけを閉じる。

    `pr_history` に閉じた過去 PR（`closed_at` 設定済み）と現在の PR（`closed_at`
    が None）を順に持たせて `cmd_set_current_pr` を実行する。過去 PR は変わらず、
    直前の現在 PR に `closed_at` と `rounds` が入り、新 PR エントリが
    `closed_at: None` / `rounds: 0` で末尾へ足される分岐を固定する。
    """
    past_pr = 4200
    state = {
        "current_pr": PR,
        "repo": "o/r",
        "head_branch": OLD_BRANCH,
        "rounds": [
            {"round": 1, "pr": past_pr},
            {"round": 2, "pr": PR},
            {"round": 3, "pr": PR},
        ],
        "pr_history": [
            {"pr": past_pr, "opened_at": "t0", "closed_at": "t1", "rounds": 1},
            {"pr": PR, "opened_at": "t2", "closed_at": None, "rounds": 0},
        ],
        "final": None,
    }
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(json.dumps(state))
    # 引数で枝名を渡し、GitHub を呼ばない経路で確かめる。
    monkeypatch.setattr(
        gh_call, "RUNNER", lambda *a, **k: pytest.fail("GitHub を呼んでいる")
    )

    review_lib.commands.loop.cmd_set_current_pr(_args(head_branch=NEW_BRANCH))

    history = _state(tmp_dir)["pr_history"]
    # 過去 PR は変わらない。
    assert history[0] == {
        "pr": past_pr, "opened_at": "t0", "closed_at": "t1", "rounds": 1}
    # 直前の現在 PR に closed_at と rounds（その PR のラウンド数 2）が入る。
    assert history[1]["pr"] == PR
    assert history[1]["closed_at"] is not None
    assert history[1]["rounds"] == 2
    # 新 PR エントリが末尾に closed_at: None / rounds: 0 で足される。
    assert history[2]["pr"] == NEW_PR
    assert history[2]["closed_at"] is None
    assert history[2]["rounds"] == 0
    assert len(history) == 3
