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
        state_mod, "_sh", lambda cmd, check=True: pytest.fail("引数があるのに GitHub を呼んでいる")
    )

    state_mod.cmd_set_current_pr(_args(head_branch=NEW_BRANCH))

    st = _state(tmp_dir)
    assert st["head_branch"] == NEW_BRANCH
    assert st["current_pr"] == NEW_PR


def test_the_branch_is_read_back_from_the_pull_request(tmp_dir, state_mod, monkeypatch):
    _seed(tmp_dir)
    calls: list[list[str]] = []
    monkeypatch.setattr(
        state_mod, "_sh", lambda cmd, check=True: calls.append(list(cmd)) or NEW_BRANCH
    )

    state_mod.cmd_set_current_pr(_args())

    assert _state(tmp_dir)["head_branch"] == NEW_BRANCH
    assert calls and str(NEW_PR) in calls[0]


def test_the_previous_branch_is_kept_when_the_lookup_fails(tmp_dir, state_mod, monkeypatch, capsys):
    """取り直せないことで進行を止めない。次のラウンドの同期が書き戻す。"""
    _seed(tmp_dir)

    def boom(cmd, check=True):
        raise RuntimeError("network")

    monkeypatch.setattr(state_mod, "_sh", boom)

    state_mod.cmd_set_current_pr(_args())

    st = _state(tmp_dir)
    assert st["head_branch"] == OLD_BRANCH
    assert st["current_pr"] == NEW_PR
    assert "取得できませんでした" in capsys.readouterr().err


def test_an_empty_lookup_keeps_the_previous_branch(tmp_dir, state_mod, monkeypatch):
    _seed(tmp_dir)
    monkeypatch.setattr(state_mod, "_sh", lambda cmd, check=True: "  \n")

    state_mod.cmd_set_current_pr(_args())

    assert _state(tmp_dir)["head_branch"] == OLD_BRANCH


# ---- 履歴とラウンドが複数の PR に跨がるとき（現状固定テスト） ----
#
# 上のテストは履歴が未完了の 1 件だけで、閉じる相手を選ぶ余地が無い。完了済みの
# PR が混ざるとき、閉じるのが現在の PR だけであることと、集計に入るラウンドが
# 現在の PR のものだけであることは固定されていない。正しさを主張せず記録する。

CLOSED_PR = 4200
CLOSED_AT = "2026-09-02T00:00:00+00:00"
FIXED_NOW = "2026-09-10T12:00:00+00:00"


def _seed_with_a_closed_pr(tmp_dir: pathlib.Path) -> None:
    state = {
        "current_pr": PR,
        "repo": "o/r",
        "head_branch": OLD_BRANCH,
        "rounds": [
            {"round": 1, "pr": CLOSED_PR},
            {"round": 2, "pr": PR},
            {"round": 3, "pr": PR},
        ],
        "pr_history": [
            {"pr": CLOSED_PR, "opened_at": "2026-09-01T00:00:00+00:00",
             "closed_at": CLOSED_AT, "rounds": 5},
            {"pr": PR, "opened_at": CLOSED_AT, "closed_at": None, "rounds": 0},
        ],
        "final": None,
    }
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(json.dumps(state))


def test_only_the_current_pr_is_closed_and_counted(tmp_dir, state_mod, monkeypatch):
    """完了済みの履歴は触らず、現在の PR だけを閉じてラウンド数を入れる。"""
    _seed_with_a_closed_pr(tmp_dir)
    monkeypatch.setattr(state_mod, "_now", lambda: FIXED_NOW)

    state_mod.cmd_set_current_pr(_args(head_branch=NEW_BRANCH))

    history = _state(tmp_dir)["pr_history"]
    assert history[0] == {"pr": CLOSED_PR, "opened_at": "2026-09-01T00:00:00+00:00",
                          "closed_at": CLOSED_AT, "rounds": 5}
    # 現在の PR のラウンドだけを数える（`CLOSED_PR` の 1 件は入らない）。
    assert history[1] == {"pr": PR, "opened_at": CLOSED_AT,
                          "closed_at": FIXED_NOW, "rounds": 2}
    assert history[2] == {"pr": NEW_PR, "opened_at": FIXED_NOW,
                          "closed_at": None, "rounds": 0}
    assert len(history) == 3


def test_the_round_list_is_kept_while_the_current_pr_moves(tmp_dir, state_mod, monkeypatch):
    """ラウンド一覧は書き換えず、`current_pr` だけが新しい PR を指す。"""
    _seed_with_a_closed_pr(tmp_dir)
    monkeypatch.setattr(state_mod, "_now", lambda: FIXED_NOW)

    state_mod.cmd_set_current_pr(_args(head_branch=NEW_BRANCH))

    st = _state(tmp_dir)
    assert st["current_pr"] == NEW_PR
    assert st["rounds"] == [
        {"round": 1, "pr": CLOSED_PR},
        {"round": 2, "pr": PR},
        {"round": 3, "pr": PR},
    ]


def test_the_skeleton_passes_the_new_branch(state_mod) -> None:
    """手順書と参照の骨組みが `--head-branch` を渡していることを固定する。"""
    here = pathlib.Path(__file__).resolve().parent.parent
    for name in ("SKILL.md", "docs/02-fix-and-rotation.md"):
        body = (here / name).read_text(encoding="utf-8")
        calls = [line for line in body.splitlines() if "set-current-pr" in line and "$NEW_PR" in line]
        assert calls, f"{name} に set-current-pr の呼び出しが無い"
        for line in calls:
            assert "--head-branch" in line, f"{name}: {line}"
