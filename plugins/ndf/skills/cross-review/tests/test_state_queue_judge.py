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


def test_a_review_that_was_already_there_is_confirmed_too(
        state_mod, queue_mod, fake_gh, tmp_dir, monkeypatch) -> None:
    """既に届いていた項目も確認を通す。**送った項目だけを通さない。**

    送信に成功した直後に中断すると、GitHub 側には投稿があるのに項目は残る。次に流すと
    冪等の照会で見つかって送らずに消えるため、確認を通さないと `queued` が解除されず
    `review_url` も戻らないまま待ち行列が空になる。判定は `queued` を見て照会を飛ばす
    ため、届いたことを一度も確かめないまま収束する（#261 の前提が崩れる）。
    """
    _seed(tmp_dir)
    queue_mod.enqueue(
        queue_mod.Queue(tmp_dir / "pending"), "review-post", REPO, PR,
        {"body": "指摘の本文", "event": "REQUEST_CHANGES"},
        actor="me", extra={"agent": "codex", "round": 1})
    fake_gh.set_rules([
        # 冪等の照会が、前回の中断で届いていた投稿を見つける。
        {"match": f"pulls/{PR}/reviews?", "stdout": json.dumps([
            {"id": 4961230016, "html_url": REVIEW_URL, "user": {"login": "me"},
             "state": "CHANGES_REQUESTED", "body": "指摘の本文"}])},
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
    assert queue_mod.Queue(tmp_dir / "pending").count() == 0
    # 送り直していない。冪等の照会が効いていることを、同じテストで見る。
    assert [c for c in fake_gh.joined() if "--method POST" in c] == []


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


# ---- 流した結果を、再開の入口の書き戻しが消さない ----


def test_the_resume_keeps_what_the_flush_wrote_to_the_state(
        state_mod, queue_mod, fake_gh, tmp_dir, monkeypatch) -> None:
    """`init` の再開が手元の古い `st` で流した結果を上書きしない。

    `_confirm_flushed` は状態ファイルへ直接書く。**手元の `st` はその書き込みを
    知らない。** 流してから書き戻すと、`queued` の解除と結果なしの記録が消え、
    届いていない投稿を承認として数えることになる。
    """
    _seed(tmp_dir,
          auto_review_instructions="コードの観点",
          review_instructions="コードの観点",
          rounds=[{
              "round": 1, "pr": PR, "started_at": "2026-09-03T00:00:00+00:00",
              "codex": {"intent": "APPROVE", "queued": True, "by_severity": {}},
              "agy": {"intent": "APPROVE", "by_severity": {}},
          }])
    queue_mod.enqueue(
        queue_mod.Queue(tmp_dir / "pending"), "review-post", REPO, PR,
        {"body": "指摘の本文", "event": "APPROVE"},
        actor="me", extra={"agent": "codex", "round": 1})
    fake_gh.set_rules([
        {"match": f"pulls/{PR}/reviews?", "stdout": "[]"},   # 冪等の照会
        {"match": "graphql", "stdout": ""},                  # 未解決スレッドは 0 件
        {"match": "", "stdout": json.dumps(
            {"id": 4961230016, "html_url": REVIEW_URL})},
    ])
    monkeypatch.setattr(state_mod, "_review_exists", lambda repo, pr, url: True)

    # `--focus` を渡し、手元の `st` を書き戻す経路（`state_changed`）を通す。
    state_mod.cmd_init(argparse.Namespace(
        pr=PR, max_rounds=12, rotate_after=8, only=None, worktree=None,
        focus="重点観点", extra_instructions_file=None))

    saved = _state(tmp_dir)
    assert saved["rounds"][0]["codex"]["queued"] is False
    assert saved["rounds"][0]["codex"]["review_url"] == REVIEW_URL
    # 書き戻す側の変更も残る。どちらか一方だけが残る直し方にしない。
    assert saved["manual_extra_review_instructions"] == "重点観点"
    assert queue_mod.Queue(tmp_dir / "pending").count() == 0
# ---- 取り込みの前に流さない ----


def test_the_read_result_does_not_flush_the_queue(
        state_mod, queue_mod, fake_gh, tmp_dir) -> None:
    """取り込みの入口では流さない。**書き戻し先がまだ無い。**

    流すと `_confirm_flushed` は書き戻せないまま項目が消え、この後の取り込みが
    `queued: true` だけを保存する。待ち行列は空になるため判定は収束させ、投稿の
    存在も参照も一度も確かめられない。
    """
    _seed(tmp_dir, rounds=[{"round": 1, "pr": PR,
                            "started_at": "2026-09-03T00:00:00+00:00"}])
    queue_mod.enqueue(
        queue_mod.Queue(tmp_dir / "pending"), "review-post", REPO, PR,
        {"body": "指摘の本文", "event": "APPROVE"},
        actor="me", extra={"agent": "codex", "round": 1})
    rfile = tmp_dir / "result.json"
    rfile.write_text(json.dumps({
        "event": "APPROVE", "comments_count": 0, "review_url": "",
        "queued": True, "by_severity": {},
    }), encoding="utf-8")

    state_mod.cmd_read_result(argparse.Namespace(pr=PR, agent="codex",
                                                 file=str(rfile)))

    assert queue_mod.Queue(tmp_dir / "pending").count() == 1
    assert fake_gh.joined() == []


def test_the_queued_reviews_are_confirmed_once_both_results_are_taken_in(
        state_mod, queue_mod, fake_gh, tmp_dir, monkeypatch) -> None:
    """両方を取り込んだ後の判定が流し、両方の担当へ書き戻す。"""
    _seed(tmp_dir, rounds=[{"round": 1, "pr": PR,
                            "started_at": "2026-09-03T00:00:00+00:00"}])
    q = queue_mod.Queue(tmp_dir / "pending")
    for agent in ("codex", "agy"):
        queue_mod.enqueue(q, "review-post", REPO, PR,
                          {"body": f"{agent} の本文", "event": "APPROVE"},
                          actor="me", extra={"agent": agent, "round": 1})
    fake_gh.set_rules([
        {"match": f"pulls/{PR}/reviews?", "stdout": "[]"},
        {"match": "", "stdout": json.dumps(
            {"id": 4961230016, "html_url": REVIEW_URL})},
    ])
    monkeypatch.setattr(state_mod, "_review_exists", lambda repo, pr, url: True)
    monkeypatch.setattr(state_mod, "_round_ci", lambda st, last, pr: {
        "verdict": "unverified", "failed": [], "pending": [], "reason": "テスト"})

    for agent in ("codex", "agy"):
        rfile = tmp_dir / f"{agent}-result.json"
        rfile.write_text(json.dumps({
            "event": "APPROVE", "comments_count": 0, "review_url": "",
            "queued": True, "by_severity": {},
        }), encoding="utf-8")
        state_mod.cmd_read_result(argparse.Namespace(pr=PR, agent=agent,
                                                     file=str(rfile)))

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_judge(argparse.Namespace(pr=PR))

    assert e.value.code == 0
    round1 = _state(tmp_dir)["rounds"][0]
    for agent in ("codex", "agy"):
        assert round1[agent]["review_url"] == REVIEW_URL
        assert round1[agent]["queued"] is False


# ---- 読めない項目 ----


def test_a_broken_item_is_reported_instead_of_being_skipped(
        state_mod, tmp_dir, capsys) -> None:
    """壊れた項目を黙って飛ばさない。

    飛ばすと `flush()` は送りも失敗の報告もしない一方、`count()` はファイルを数え
    続ける。判定は終了コード 8 を返し続け、理由が出ないため誰も直せない。
    """
    _seed(tmp_dir)
    pending = tmp_dir / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    (pending / "0001-review-post-broken.json").write_text(
        '{"kind": "review-post"', encoding="utf-8")

    with pytest.raises(SystemExit) as e:
        state_mod.cmd_judge(argparse.Namespace(pr=PR))

    assert e.value.code == 8
    assert "待ち行列の項目を読めない" in capsys.readouterr().err


def test_the_flush_stops_at_a_broken_item(queue_mod, tmp_path) -> None:
    """後ろの項目まで送らない。**順序が入れ替わる。**"""
    pending = tmp_path / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    (pending / "0001-pr-comment-broken.json").write_text("", encoding="utf-8")
    queue_mod.enqueue(queue_mod.Queue(pending), "pr-comment", REPO, PR,
                      {"body": "後ろの本文"})

    result = queue_mod.Queue(pending).flush()

    assert result.sent == [] and result.skipped == []
    assert result.remaining == 2
    assert "読めない" in (result.failed or {})["last_error"]
