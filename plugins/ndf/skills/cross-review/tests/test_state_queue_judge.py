"""待ち行列が空になるまで収束させない（#291、受け入れ条件 11〜13）。

**未反映のまま「両方が承認した」と記録しないことが、この課題で守る一線である。**
止めるのは `final = approved` を出すところだけで、レビューも修正もローカルで進む。

| 段階 | #261 の検査 | 待ち行列を入れた後 |
| --- | --- | --- |
| 結果を取り込む | 投稿の失敗があれば結果なし | `queued` が真の結果はこの検査を通す |
| 結果を取り込む | 投稿先の参照の存在を照会 | `queued` が真のときは照会しない（識別子がまだ無い） |
| 流した直後 | — | 参照を書き戻し、存在を 1 度だけ確かめる |
| 判定 | 収束 | 待ち行列が空のときだけ |
"""
from __future__ import annotations

import argparse
import json
import pathlib

import pytest

REPO = "o/r"
PR = 2911
REVIEW_URL = f"https://github.com/o/r/pull/{PR}#pullrequestreview-4961230016"


@pytest.fixture()
def tmp_dir(monkeypatch, tmp_path, state_mod) -> pathlib.Path:
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    return tmp_path


def _seed(tmp_dir: pathlib.Path, **over) -> None:
    state = {
        "current_pr": PR,
        "repo": REPO,
        "tmp_dir": str(tmp_dir),
        "rounds": [{
            "round": 1, "pr": PR, "started_at": "2026-09-03T00:00:00+00:00",
            "codex": {"intent": "APPROVE", "by_severity": {}},
            "agy": {"intent": "APPROVE", "by_severity": {}},
        }],
        "carried_over": None,
        "final": None,
    }
    state.update(over)
    (tmp_dir / f"cross-review-pr{PR}-state.json").write_text(
        json.dumps(state), encoding="utf-8")


def _state(tmp_dir: pathlib.Path) -> dict:
    return json.loads(
        (tmp_dir / f"cross-review-pr{PR}-state.json").read_text(encoding="utf-8"))


# ---- 受け入れ条件 11 ----


def test_a_pending_queue_blocks_convergence(state_mod, queue_mod, fake_gh,
                                            tmp_dir, monkeypatch) -> None:
    _seed(tmp_dir)
    queue_mod.enqueue(queue_mod.Queue(tmp_dir / "pending"),
                      "pr-comment", REPO, PR, {"body": "積んだ本文"})
    fake_gh.set_mode("rate_limit")   # 流そうとしても、まだ上限のまま

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_judge(argparse.Namespace(pr=PR))

    assert e.value.code == 8
    assert _state(tmp_dir)["final"] is None


def test_an_empty_queue_still_converges(state_mod, tmp_dir, monkeypatch) -> None:
    """待ち行列が空なら、これまでどおり収束する。"""
    _seed(tmp_dir)
    monkeypatch.setattr(state_mod, "_round_ci", lambda st, last, pr: {
        "verdict": "unverified", "failed": [], "pending": [], "reason": "テスト"})

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_judge(argparse.Namespace(pr=PR))

    assert e.value.code == 0
    assert _state(tmp_dir)["final"] == "approved"


def test_the_judge_reports_the_pending_count(state_mod, queue_mod, fake_gh,
                                             tmp_dir, capsys) -> None:
    _seed(tmp_dir)
    queue_mod.enqueue(queue_mod.Queue(tmp_dir / "pending"),
                      "pr-comment", REPO, PR, {"body": "積んだ本文"})
    fake_gh.set_mode("rate_limit")

    with pytest.raises(SystemExit):
        state_mod.cmd_judge(argparse.Namespace(pr=PR))

    assert "PENDING_POSTS=1" in capsys.readouterr().out


# ---- 受け入れ条件 12 ----


def test_the_review_is_confirmed_once_right_after_it_is_flushed(
        state_mod, queue_mod, fake_gh, tmp_dir, monkeypatch) -> None:
    _seed(tmp_dir)
    queue_mod.enqueue(
        queue_mod.Queue(tmp_dir / "pending"), "review-post", REPO, PR,
        {"body": "指摘の本文", "event": "REQUEST_CHANGES"},
        actor="me", extra={"agent": "codex", "round": 1})
    fake_gh.set_rules([
        {"match": f"pulls/{PR}/reviews?", "stdout": "[]"},
        {"match": "", "stdout": json.dumps(
            {"id": 4961230016, "html_url": REVIEW_URL})},
    ])
    calls: list = []

    def _exists(repo, pr, url):
        calls.append(url)
        return True

    monkeypatch.setattr(state_mod, "_review_exists", _exists)

    state_mod.cmd_flush(argparse.Namespace(pr=PR))

    assert calls == [REVIEW_URL]
    assert _state(tmp_dir)["rounds"][0]["codex"]["review_url"] == REVIEW_URL
    assert _state(tmp_dir)["rounds"][0]["codex"]["queued"] is False


def test_a_review_that_did_not_arrive_is_recorded_as_no_result(
        state_mod, queue_mod, fake_gh, tmp_dir, monkeypatch) -> None:
    """流した直後の確認で無ければ、#261 の決まりどおり結果なしにする。"""
    _seed(tmp_dir)
    queue_mod.enqueue(
        queue_mod.Queue(tmp_dir / "pending"), "review-post", REPO, PR,
        {"body": "指摘の本文", "event": "REQUEST_CHANGES"},
        actor="me", extra={"agent": "codex", "round": 1})
    fake_gh.set_rules([
        {"match": f"pulls/{PR}/reviews?", "stdout": "[]"},
        {"match": "", "stdout": json.dumps(
            {"id": 4961230016, "html_url": REVIEW_URL})},
    ])
    monkeypatch.setattr(state_mod, "_review_exists", lambda repo, pr, url: False)

    state_mod.cmd_flush(argparse.Namespace(pr=PR))

    assert _state(tmp_dir)["rounds"][0]["codex"]["intent"] == "NO_RESULT"


# ---- 受け入れ条件 13 ----


def test_a_queued_result_skips_the_arrival_check(state_mod, tmp_dir,
                                                 monkeypatch) -> None:
    """積んだ時点では届いていない。照会すると結果なしになり、二重に積まれる。"""
    _seed(tmp_dir, rounds=[{"round": 1, "pr": PR,
                            "started_at": "2026-09-03T00:00:00+00:00"}])
    called: list = []
    monkeypatch.setattr(state_mod, "_review_exists",
                        lambda *a: called.append(a) or True)
    monkeypatch.setattr(state_mod, "_posted_comment_count",
                        lambda *a: called.append(a) or 0)
    rfile = tmp_dir / "result.json"
    rfile.write_text(json.dumps({
        "event": "REQUEST_CHANGES", "posted_as": "COMMENT", "comments_count": 3,
        "review_url": "", "queued": True,
        "post_error": "API rate limit exceeded",
        "by_severity": {"major": 3},
    }), encoding="utf-8")

    state_mod.cmd_read_result(argparse.Namespace(pr=PR, agent="codex",
                                                 file=str(rfile)))

    assert called == []
    entry = _state(tmp_dir)["rounds"][0]["codex"]
    assert entry["intent"] == "REQUEST_CHANGES"
    assert entry["queued"] is True


def test_a_normal_result_still_checks_the_arrival(state_mod, tmp_dir,
                                                  monkeypatch) -> None:
    """`queued` を持たない結果ファイルの扱いは変えない（#261 のまま）。"""
    _seed(tmp_dir, rounds=[{"round": 1, "pr": PR,
                            "started_at": "2026-09-03T00:00:00+00:00"}])
    called: list = []
    monkeypatch.setattr(state_mod, "_review_exists",
                        lambda *a: called.append(a) or True)
    rfile = tmp_dir / "result.json"
    rfile.write_text(json.dumps({
        "event": "APPROVE", "comments_count": 0, "review_url": REVIEW_URL,
        "by_severity": {},
    }), encoding="utf-8")

    state_mod.cmd_read_result(argparse.Namespace(pr=PR, agent="codex",
                                                 file=str(rfile)))

    assert len(called) == 1
