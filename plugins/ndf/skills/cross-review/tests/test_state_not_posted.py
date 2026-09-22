"""投稿が届いたことを、送った後に確かめる（#261 #730）。

**投稿するのはレビューを回す側である**（#730）。担当は投稿せず、結果ファイルに
投稿の失敗や参照を申告しない。取り込みは送信の応答をそのまま記録にするため、
申告を読んで結果なしにする経路は無い。

残るのは、上限で積んだ投稿を後から流したときの確かめである。流した直後に参照から
照会し、届いていなければ結果なしとして記録して、判定の「同じラウンドで 1 度だけ
起動し直す」経路へ乗せる（`_confirm_flushed`）。この文書はその照会の振る舞いを見る。

| 照会の結果 | 扱い |
| --- | --- |
| 識別子から照会してレビューが存在する | 届いた |
| 識別子を取り出せない | 届いていない |
| 照会できない・何も返らない | 分からない（届いていないとは読まない） |
"""
from __future__ import annotations

import argparse
import json
import pathlib

import pytest

PR = 4261
AGENT = "agy"
REVIEW_URL = f"https://github.com/o/r/pull/{PR}#pullrequestreview-4961230016"


@pytest.fixture()
def tmp_dir(monkeypatch, tmp_path, state_mod):
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    return tmp_path


def _seed_state(tmp_dir: pathlib.Path) -> None:
    state = {
        "current_pr": PR,
        "repo": "o/r",
        "rounds": [{"round": 1, "pr": PR, "started_at": "2026-09-02T00:00:00+00:00"}],
        "final": None,
    }
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(json.dumps(state))


def _result(tmp_dir: pathlib.Path, **over) -> pathlib.Path:
    payload = {
        "event": "REQUEST_CHANGES",
        "posted_as": "COMMENT",
        "comments_count": 0,
        "review_url": REVIEW_URL,
        "by_severity": {"critical": 0, "major": 0, "minor": 0, "nit": 0},
    }
    payload.update(over)
    rfile = tmp_dir / "result.json"
    rfile.write_text(json.dumps(payload))
    return rfile


def _args(rfile: pathlib.Path) -> argparse.Namespace:
    return argparse.Namespace(pr=PR, agent=AGENT, file=str(rfile))


def _round(tmp_dir: pathlib.Path) -> dict:
    return json.loads((tmp_dir / f"cross-review-pr{PR}-state.json").read_text())["rounds"][-1]


def test_a_posted_review_is_merged(tmp_dir, state_mod, monkeypatch):
    _seed_state(tmp_dir)
    monkeypatch.setattr(state_mod, "_review_exists", lambda repo, pr, url: True)

    state_mod.cmd_read_result(_args(_result(tmp_dir)))

    assert _round(tmp_dir)[AGENT]["intent"] == "REQUEST_CHANGES"


# ---------------- レビューの実在確認 ----------------


def test_the_lookup_reads_the_review_id_from_the_url(state_mod, monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(
        state_mod, "_sh", lambda cmd, check=True: calls.append(list(cmd)) or "4961230016"
    )

    assert state_mod._review_exists("o/r", PR, REVIEW_URL) is True
    assert calls and f"repos/o/r/pulls/{PR}/reviews/4961230016" in calls[0]


def test_the_lookup_is_false_without_a_review_id(state_mod, monkeypatch):
    monkeypatch.setattr(
        state_mod, "_sh", lambda cmd, check=True: pytest.fail("識別子が無いのに GitHub を呼んでいる")
    )

    assert state_mod._review_exists("o/r", PR, "https://example.test/") is False
    assert state_mod._review_exists("o/r", PR, None) is False


def test_the_lookup_is_none_when_the_api_fails(state_mod, monkeypatch):
    def boom(cmd, check=True):
        raise RuntimeError("network")

    monkeypatch.setattr(state_mod, "_sh", boom)

    assert state_mod._review_exists("o/r", PR, REVIEW_URL) is None


def test_the_lookup_is_none_when_the_api_returns_nothing(state_mod, monkeypatch):
    """取得できなかったことと、無いことを混同しない。"""
    monkeypatch.setattr(state_mod, "_sh", lambda cmd, check=True: "")

    assert state_mod._review_exists("o/r", PR, REVIEW_URL) is None
