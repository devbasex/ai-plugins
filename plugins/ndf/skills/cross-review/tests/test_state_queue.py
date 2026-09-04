"""上限を検知して待ち行列へ倒す（#291）。

GitHub の利用回数の上限に達すると、投稿は失敗する。**失敗をそのまま止める側へ倒すと、
レビューを 1 巡も進められない。** 上限のときだけ投稿する内容をローカルへ積み、回復した
後に流す。権限の誤りと存在しない対象は、これまでどおり止める。

| 応答 | 扱い |
| --- | --- |
| HTTP 403 で `message` が上限を指す | 積む。終了コード 0 で先へ進む |
| HTTP 403 の二次の上限 | 同上 |
| HTTP 403 で `message` が権限の誤りを指す | 積まない。止める |
| HTTP 404 | 積まない。止める |
| HTTP 403 で `message` が上限を指さず、残量も 0 でない | 積まない。止める |

判別は標準エラーの `(HTTP <番号>)` と標準出力の `message` の**両方**を読む。片方だけで
決めると、権限の誤り（403）と上限（403）を分けられない。
"""
from __future__ import annotations

import json
import pathlib

import pytest

REPO = "o/r"
PR = 291
BODY = "ℹ️ レビューコメント履歴整理のため本 PR を一度 close します。"


@pytest.fixture()
def qdir(tmp_path) -> pathlib.Path:
    return tmp_path / ".cross_review" / "pending"


def _post(queue_mod, qdir, body=BODY, actor="me"):
    q = queue_mod.Queue(qdir)
    return queue_mod.post(q, "pr-comment", REPO, PR, {"body": body}, actor=actor)


# ---- 受け入れ条件 4: 上限のときは積んで先へ進む ----


@pytest.mark.parametrize("mode", ["rate_limit", "secondary"])
def test_a_rate_limited_post_is_queued(queue_mod, fake_gh, qdir, mode) -> None:
    fake_gh.set_mode(mode)
    outcome, _ = _post(queue_mod, qdir)

    assert outcome == queue_mod.QUEUED
    assert queue_mod.Queue(qdir).count() == 1


def test_the_queued_item_keeps_what_to_post(queue_mod, fake_gh, qdir) -> None:
    fake_gh.set_mode("rate_limit")
    _post(queue_mod, qdir)

    item = json.loads(queue_mod.Queue(qdir).paths()[0].read_text(encoding="utf-8"))
    assert item["kind"] == "pr-comment"
    assert item["repo"] == REPO
    assert item["pr"] == PR
    assert item["attempts"] == 1
    assert item["request"]["path"] == f"repos/{REPO}/issues/{PR}/comments"
    assert item["request"]["fields"]["body"] == BODY


# ---- 受け入れ条件 5 / 6: ほかの失敗は積まない ----


@pytest.mark.parametrize("mode", ["forbidden", "not_found"])
def test_other_failures_are_not_queued(queue_mod, fake_gh, qdir, mode) -> None:
    """権限の誤りと存在しない対象は、これまでどおり止める。"""
    fake_gh.set_mode(mode)
    outcome, _ = _post(queue_mod, qdir)

    assert outcome == queue_mod.FAILED
    assert queue_mod.Queue(qdir).count() == 0


def test_the_http_status_alone_does_not_decide(queue_mod, fake_gh, qdir) -> None:
    """403 でも `message` が上限を指さず、残量も 0 でなければ積まない（条件 6）。"""
    fake_gh.set_rules([
        # `--jq` は実物の gh が適用する。模した gh は結果だけを返す。
        {"match": "api rate_limit", "stdout": "4321\n"},
        {"match": "", "stdout": '{"message":"Must have admin rights","status":"403"}',
         "stderr": "gh: Must have admin rights (HTTP 403)\n", "exit": 1},
    ])
    outcome, _ = _post(queue_mod, qdir)

    assert outcome == queue_mod.FAILED
    assert queue_mod.Queue(qdir).count() == 0


def test_a_403_with_no_quota_left_is_queued(queue_mod, fake_gh, qdir) -> None:
    """`message` で決まらないときだけ残量を引く。0 なら上限として積む。"""
    fake_gh.set_rules([
        {"match": "api rate_limit", "stdout": "0\n"},
        {"match": "", "stdout": '{"message":"Must have admin rights","status":"403"}',
         "stderr": "gh: Must have admin rights (HTTP 403)\n", "exit": 1},
    ])
    outcome, _ = _post(queue_mod, qdir)

    assert outcome == queue_mod.QUEUED


def test_the_message_alone_decides_when_no_http_status_is_printed(
        queue_mod, fake_gh, qdir) -> None:
    """GraphQL の失敗は `(HTTP <番号>)` を伴わない（#291 の実例）。"""
    fake_gh.set_rules([
        {"match": "", "stdout": "",
         "stderr": "gh: GraphQL: API rate limit already exceeded for user ID 10234200.\n",
         "exit": 1},
    ])
    outcome, _ = _post(queue_mod, qdir)

    assert outcome == queue_mod.QUEUED


def test_the_quota_lookup_is_skipped_when_the_message_decides(
        queue_mod, fake_gh, qdir) -> None:
    """上限だと `message` で分かるときは、残量を引かない（余分な照会を足さない）。"""
    fake_gh.set_mode("rate_limit")
    _post(queue_mod, qdir)

    assert not [c for c in fake_gh.joined() if "rate_limit" in c]


# ---- 受け入れ条件 7: 連番の順に流れる ----


def test_the_items_flush_in_sequence_order(queue_mod, fake_gh, qdir) -> None:
    fake_gh.set_mode("rate_limit")
    for i in range(3):
        _post(queue_mod, qdir, body=f"本文 {i}")
    assert [p.name for p in queue_mod.Queue(qdir).paths()] == [
        f"{n:04d}-pr-comment-{PR}.json" for n in (1, 2, 3)
    ]

    fake_gh.set_mode("ok")
    result = queue_mod.Queue(qdir).flush()

    assert [i["match"]["body"] for i in result.sent] == ["本文 0", "本文 1", "本文 2"]
    assert queue_mod.Queue(qdir).count() == 0


# ---- 受け入れ条件 9: 流せなかった項目は記録を増やして残る ----


def test_a_failed_flush_records_the_attempt(queue_mod, fake_gh, qdir) -> None:
    fake_gh.set_mode("rate_limit")
    _post(queue_mod, qdir)
    result = queue_mod.Queue(qdir).flush()

    assert result.remaining == 1
    item = json.loads(queue_mod.Queue(qdir).paths()[0].read_text(encoding="utf-8"))
    assert item["attempts"] == 2          # 積んだときの 1 回 + 流そうとした 1 回
    assert "rate limit" in item["last_error"].lower()


def test_the_flush_stops_at_the_first_failure(queue_mod, fake_gh, qdir) -> None:
    """順序を守る。1 件目が送れないまま 2 件目を送ると、順番が入れ替わる。"""
    fake_gh.set_mode("rate_limit")
    for i in range(3):
        _post(queue_mod, qdir, body=f"本文 {i}")
    result = queue_mod.Queue(qdir).flush()

    assert result.sent == []
    assert result.remaining == 3


# ---- 受け入れ条件 10: 明示と自動の両方で流れる ----


def test_the_flush_subcommand_drains_the_queue(state_mod, queue_mod, fake_gh,
                                               monkeypatch, tmp_path) -> None:
    import argparse

    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    (tmp_path / f"cross-review-pr{PR}-state.json").write_text(
        json.dumps({"current_pr": PR, "repo": REPO, "tmp_dir": str(tmp_path),
                    "rounds": [], "final": None}), encoding="utf-8")
    fake_gh.set_mode("rate_limit")
    _post(queue_mod, tmp_path / "pending")

    fake_gh.set_mode("ok")
    state_mod.cmd_flush(argparse.Namespace(pr=PR))

    assert queue_mod.Queue(tmp_path / "pending").count() == 0


def test_the_judge_drains_the_queue_at_its_entry(state_mod, queue_mod, fake_gh,
                                                 monkeypatch, tmp_path) -> None:
    import argparse

    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    (tmp_path / f"cross-review-pr{PR}-state.json").write_text(
        json.dumps({
            "current_pr": PR, "repo": REPO, "tmp_dir": str(tmp_path), "final": None,
            "rounds": [{"round": 1, "pr": PR,
                        "codex": {"intent": "APPROVE"}, "agy": {"intent": "APPROVE"}}],
        }), encoding="utf-8")
    fake_gh.set_mode("rate_limit")
    _post(queue_mod, tmp_path / "pending")

    fake_gh.set_mode("ok")
    monkeypatch.setattr(state_mod, "_round_ci", lambda st, last, pr: {
        "verdict": "unverified", "failed": [], "pending": [], "reason": "テスト"})
    with pytest.raises(SystemExit):
        state_mod.cmd_judge(argparse.Namespace(pr=PR))

    assert queue_mod.Queue(tmp_path / "pending").count() == 0
