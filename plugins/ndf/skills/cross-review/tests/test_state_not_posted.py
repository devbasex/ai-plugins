"""投稿が届いていないレビューを結果なしとして扱う（#261）。

レビュアーが投稿に失敗したとき、判定だけが残り、指摘の中身が Pull Request に無いまま
修正の工程へ進んでいた。修正の担当は Pull Request のコメントを読んで直すため、指摘が
無ければ直すものが無く、ラウンドだけが 1 つ増える。

| 結果ファイルの状態 | 扱い |
| --- | --- |
| `post_error` に値がある | 届いていない。結果なしとして記録する |
| `review_url` が空、または識別子を取り出せない | 届いていない。結果なしとして記録する |
| 識別子から照会してレビューが存在する | 届いた。これまでどおり取り込む |
| 識別子から照会できない | 申告を採用し、確認できなかったことを出力へ残す |

結果なしとして記録すると、判定（`state.py judge`）の「同じラウンドで 1 度だけ起動し直す」
経路へ乗る。修正の担当から見ると、結果が残らなかった場合と、結果はあるが指摘が届いて
いない場合は同じ状態である（読むべき指摘が無い）。
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


def test_a_post_error_is_recorded_as_no_result(tmp_dir, state_mod, monkeypatch):
    """実測の形。`post_error` があり、`review_url` も空で件数もすべて 0。"""
    _seed_state(tmp_dir)
    monkeypatch.setattr(state_mod, "_review_exists", lambda repo, pr, url: True)

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_read_result(
            _args(_result(tmp_dir, review_url="", post_error="gh api failed"))
        )

    assert e.value.code == 1
    assert _round(tmp_dir)[AGENT]["intent"] == "NO_RESULT"
    assert _round(tmp_dir)[AGENT]["no_result_reason"] == "not_posted"


def test_an_empty_review_url_is_recorded_as_no_result(tmp_dir, state_mod, monkeypatch):
    _seed_state(tmp_dir)
    monkeypatch.setattr(state_mod, "_sh", lambda cmd, check=True: "")

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_read_result(_args(_result(tmp_dir, review_url="")))

    assert e.value.code == 1
    assert _round(tmp_dir)[AGENT]["no_result_reason"] == "not_posted"


def test_a_review_missing_on_github_is_recorded_as_no_result(tmp_dir, state_mod, monkeypatch):
    _seed_state(tmp_dir)
    monkeypatch.setattr(state_mod, "_review_exists", lambda repo, pr, url: False)

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_read_result(_args(_result(tmp_dir)))

    assert e.value.code == 1
    assert _round(tmp_dir)[AGENT]["no_result_reason"] == "not_posted"


def test_an_unavailable_lookup_keeps_the_declaration(tmp_dir, state_mod, monkeypatch, capsys):
    """照会できないときは申告を採用する。通信の失敗でループを止めない。"""
    _seed_state(tmp_dir)
    monkeypatch.setattr(state_mod, "_review_exists", lambda repo, pr, url: None)

    state_mod.cmd_read_result(_args(_result(tmp_dir)))

    assert _round(tmp_dir)[AGENT]["intent"] == "REQUEST_CHANGES"
    assert "確認できませんでした" in capsys.readouterr().err


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


def test_a_no_result_record_sends_the_judge_to_a_relaunch(tmp_dir, state_mod, monkeypatch):
    """結果なしの記録を受けて、判定が起動し直しへ進む（終了コード 7）。"""
    _seed_state(tmp_dir)
    monkeypatch.setattr(state_mod, "_review_exists", lambda repo, pr, url: False)
    with pytest.raises(SystemExit):
        state_mod.cmd_read_result(_args(_result(tmp_dir)))

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_judge(argparse.Namespace(pr=PR))

    assert e.value.code == 7
