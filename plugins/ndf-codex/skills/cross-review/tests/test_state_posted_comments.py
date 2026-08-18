"""申告されたインラインコメント数を、GitHub 側の実数と突き合わせる。

レビューの投稿は AI 自身が `gh api` で行うため、**投稿に失敗しても結果ファイルの
申告だけは残る**。申告を信じて先へ進むと、修正担当が読むべき指摘が GitHub 上に
存在しないまま収束判定まで走る。実測では 2 件の申告に対しスレッドが 1 つも
作られていなかった。

| 申告 | GitHub 側 | 扱い |
| --- | --- | --- |
| 0 件 | 見に行かない | 投稿が無いので突き合わせる相手がいない |
| 2 件 | 2 件 | そのまま採用する |
| 2 件 | 0 件 | 投稿が届いていないので中断する |
| 2 件 | 取得できない | 申告を採用し、確認できなかったことを残す |

「取得できなかった」と「0 件」を混同しない。取得の失敗で止めると、GitHub 側の
一時的な不調でループが進まなくなる。
"""
from __future__ import annotations

import argparse
import json
import pathlib

import pytest

PR = 4242
AGENT = "gemini"
REVIEW_URL = f"https://github.com/o/r/pull/{PR}#pullrequestreview-4961230016"


def _seed_state(tmp_dir: pathlib.Path) -> None:
    state = {
        "current_pr": PR,
        "repo": "o/r",
        "rounds": [{"round": 1, "pr": PR, "started_at": "2026-08-18T00:00:00+00:00"}],
        "final": None,
    }
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(json.dumps(state))


def _result(tmp_dir: pathlib.Path, **over) -> pathlib.Path:
    payload = {
        "event": "REQUEST_CHANGES",
        "posted_as": "REQUEST_CHANGES",
        "comments_count": 2,
        "review_url": REVIEW_URL,
        "by_severity": {"major": 2},
    }
    payload.update(over)
    rfile = tmp_dir / "result.json"
    rfile.write_text(json.dumps(payload))
    return rfile


def _args(rfile: pathlib.Path) -> argparse.Namespace:
    return argparse.Namespace(pr=PR, agent=AGENT, file=str(rfile))


def _read_state(tmp_dir: pathlib.Path) -> dict:
    return json.loads((tmp_dir / f"cross-review-pr{PR}-state.json").read_text())


@pytest.fixture()
def tmp_dir(monkeypatch, tmp_path, state_mod):
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture()
def posted(monkeypatch, state_mod):
    """GitHub 側の件数を差し替える。`None` は取得できなかったことを表す。"""
    def _set(count):
        monkeypatch.setattr(
            state_mod, "_posted_comment_count",
            lambda repo, pr, review_url: count,
        )
    return _set


def test_declared_count_matching_github_is_accepted(tmp_dir, state_mod, posted):
    _seed_state(tmp_dir)
    posted(2)

    state_mod.cmd_read_result(_args(_result(tmp_dir)))

    assert _read_state(tmp_dir)["rounds"][-1][AGENT]["comments"] == 2


def test_declared_comments_missing_on_github_aborts(tmp_dir, state_mod, posted):
    """申告があるのに GitHub 側へ届いていなければ中断する。

    そのまま進むと、修正担当が読むべき指摘が存在しないまま収束判定まで走る。
    """
    _seed_state(tmp_dir)
    posted(0)

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_read_result(_args(_result(tmp_dir)))

    assert e.value.code == 1
    assert AGENT not in _read_state(tmp_dir)["rounds"][-1]


def test_partially_posted_comments_abort(tmp_dir, state_mod, posted):
    """一部しか届いていない場合も中断する。取りこぼしは全件欠落と同じ扱いにする。"""
    _seed_state(tmp_dir)
    posted(1)

    with pytest.raises(SystemExit):
        state_mod.cmd_read_result(_args(_result(tmp_dir)))


def test_more_comments_on_github_is_accepted(tmp_dir, state_mod, posted):
    """GitHub 側が多い分には通す。人の追記など、申告以外の経路で増えうる。"""
    _seed_state(tmp_dir)
    posted(3)

    state_mod.cmd_read_result(_args(_result(tmp_dir)))

    assert _read_state(tmp_dir)["rounds"][-1][AGENT]["comments"] == 2


def test_zero_declared_skips_the_check(tmp_dir, state_mod, monkeypatch):
    """申告 0 件なら GitHub を見に行かない。"""
    _seed_state(tmp_dir)
    called: list = []
    monkeypatch.setattr(
        state_mod, "_posted_comment_count",
        lambda *a, **k: called.append(a) or 0,
    )

    state_mod.cmd_read_result(_args(_result(tmp_dir, event="APPROVE", comments_count=0)))

    assert called == []
    assert _read_state(tmp_dir)["rounds"][-1][AGENT]["comments"] == 0


def test_unavailable_github_count_keeps_the_declaration(tmp_dir, state_mod, posted):
    """GitHub 側を取得できなければ申告を採用する。取得失敗で止めない。"""
    _seed_state(tmp_dir)
    posted(None)

    state_mod.cmd_read_result(_args(_result(tmp_dir)))

    assert _read_state(tmp_dir)["rounds"][-1][AGENT]["comments"] == 2


def test_missing_review_url_is_treated_as_unavailable(tmp_dir, state_mod, monkeypatch):
    """投稿先の参照が無ければ、突き合わせる相手を決められないので申告を採用する。"""
    _seed_state(tmp_dir)
    monkeypatch.setattr(
        state_mod, "_sh",
        lambda cmd, check=True: pytest.fail("参照が無いのに GitHub を呼んでいる"),
    )

    state_mod.cmd_read_result(_args(_result(tmp_dir, review_url=None)))

    assert _read_state(tmp_dir)["rounds"][-1][AGENT]["comments"] == 2


# ---------------- 件数の取得 ----------------

def test_posted_count_reads_the_review_id_from_the_url(state_mod, monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(
        state_mod, "_sh",
        lambda cmd, check=True: calls.append(list(cmd)) or "2",
    )

    count = state_mod._posted_comment_count("o/r", PR, REVIEW_URL)

    assert count == 2
    assert calls and "repos/o/r/pulls/4242/reviews/4961230016/comments" in calls[0]


def test_posted_count_is_none_when_the_url_has_no_review_id(state_mod, monkeypatch):
    monkeypatch.setattr(
        state_mod, "_sh",
        lambda cmd, check=True: pytest.fail("識別子が無いのに GitHub を呼んでいる"),
    )

    assert state_mod._posted_comment_count("o/r", PR, "https://example.test/") is None


def test_posted_count_is_none_when_the_api_fails(state_mod, monkeypatch):
    def boom(cmd, check=True):
        raise RuntimeError("network")

    monkeypatch.setattr(state_mod, "_sh", boom)

    assert state_mod._posted_comment_count("o/r", PR, REVIEW_URL) is None
