"""待ち行列を流す公開入口に対する現状固定テスト。"""
from __future__ import annotations

import json
import pathlib
import sys
from typing import Any

import pytest

LIB = pathlib.Path(__file__).resolve().parents[1] / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import post_queue


def _write_item(directory: pathlib.Path, seq: int) -> pathlib.Path:
    path = directory / f"{seq:04d}-pr-comment-{seq}.json"
    path.write_text(
        json.dumps(
            {
                "seq": seq,
                "kind": "pr-comment",
                "repo": "devbasex/ai-plugins",
                "pr": 757,
                "attempts": 0,
                "request": {"method": "POST", "path": f"items/{seq}"},
                "match": {"body": f"body-{seq}"},
            }
        ),
        encoding="utf-8",
    )
    return path


def test_flush_skips_posted_sends_success_and_stops_at_rate_limit(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """現状固定。先頭から処理し、上限失敗とその後の項目を残す。"""
    paths = [_write_item(tmp_path, seq) for seq in range(1, 5)]
    sent_sequences: list[int] = []

    def posted_match(item):
        if item["seq"] == 1:
            return True, {"id": 101, "body": "body-1"}
        return False, None

    def send(item):
        sent_sequences.append(item["seq"])
        if item["seq"] == 2:
            return post_queue.Attempt(0, '{"id": 202}', "")
        return post_queue.Attempt(1, "", "API rate limit exceeded (HTTP 429)")

    monkeypatch.setattr(post_queue, "posted_match", posted_match)
    monkeypatch.setattr(post_queue, "send", send)

    result = post_queue.Queue(tmp_path).flush()

    assert [item["seq"] for item in result.skipped] == [1]
    assert result.skipped[0]["response"] == {"id": 101, "body": "body-1"}
    assert [item["seq"] for item in result.sent] == [2]
    assert result.sent[0]["response"] == {"id": 202}
    assert result.failed["seq"] == 3
    assert result.failed["attempts"] == 1
    assert result.remaining == 2
    assert result.rate_limited is True
    assert sent_sequences == [2, 3]
    assert [path.name for path in post_queue.Queue(tmp_path).paths()] == [
        paths[2].name,
        paths[3].name,
    ]


def test_flush_stops_at_a_normal_send_failure(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """現状固定。通常失敗でも後続を送らず、上限とは区別する。"""
    paths = [_write_item(tmp_path, seq) for seq in range(1, 3)]
    sent_sequences: list[int] = []

    monkeypatch.setattr(post_queue, "posted_match", lambda item: (False, None))

    def send(item):
        sent_sequences.append(item["seq"])
        return post_queue.Attempt(1, "", "permission denied (HTTP 403)")

    monkeypatch.setattr(post_queue, "send", send)
    monkeypatch.setattr(post_queue, "quota_remaining", lambda: 100)

    result = post_queue.Queue(tmp_path).flush()

    assert result.sent == []
    assert result.skipped == []
    assert result.failed["seq"] == 1
    assert result.remaining == 2
    assert result.rate_limited is False
    assert sent_sequences == [1]
    assert [path.name for path in post_queue.Queue(tmp_path).paths()] == [
        path.name for path in paths
    ]


def test_flush_stops_at_corrupt_json_and_keeps_following_items(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """現状固定。壊れた先頭項目を黙って飛ばさず、対象名を報告する。"""
    corrupt = tmp_path / "0001-pr-comment-corrupt.json"
    corrupt.write_text("{", encoding="utf-8")
    following = _write_item(tmp_path, 2)
    monkeypatch.setattr(
        post_queue,
        "send",
        lambda item: pytest.fail("壊れた先頭項目より後を送ってはならない"),
    )

    result = post_queue.Queue(tmp_path).flush()

    assert result.sent == []
    assert result.skipped == []
    assert result.failed["path"] == str(corrupt)
    assert corrupt.name in result.failed["last_error"]
    assert result.remaining == 2
    assert result.rate_limited is False
    assert [path.name for path in post_queue.Queue(tmp_path).paths()] == [
        corrupt.name,
        following.name,
    ]


def test_drop_removes_only_the_item_with_the_requested_sequence(
    tmp_path: pathlib.Path,
) -> None:
    """現状固定。指定した連番の項目だけを取り除く。"""
    paths = [_write_item(tmp_path, seq) for seq in range(1, 4)]
    queue = post_queue.Queue(tmp_path)

    assert queue.drop(2) is True
    assert [path.name for path in queue.paths()] == [paths[0].name, paths[2].name]


@pytest.mark.parametrize("seq", [None, 99])
def test_drop_keeps_items_when_the_sequence_does_not_match(
    tmp_path: pathlib.Path, seq: int | None
) -> None:
    """現状固定。連番が無い場合は何も取り除かない。"""
    paths = [_write_item(tmp_path, item_seq) for item_seq in range(1, 3)]
    queue = post_queue.Queue(tmp_path)

    assert queue.drop(seq) is False
    assert [path.name for path in queue.paths()] == [path.name for path in paths]


def test_post_succeeds_directly_when_queue_is_empty(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """現状固定。待ち行列が空で送信成功なら posted を返し、待ち行列は空のまま。"""
    q = post_queue.Queue(tmp_path)
    sent_items: list[dict[str, Any]] = []

    def fake_send(item):
        sent_items.append(item)
        return post_queue.Attempt(0, '{"id": 123}', "")

    monkeypatch.setattr(post_queue, "send", fake_send)

    outcome, attempt = post_queue.post(
        q,
        "pr-comment",
        "devbasex/ai-plugins",
        757,
        {"body": "direct-post"},
        actor="octocat",
    )

    assert outcome == post_queue.POSTED
    assert attempt is not None
    assert attempt.ok is True
    assert attempt.stdout == '{"id": 123}'
    assert q.count() == 0
    assert len(sent_items) == 1
    assert sent_items[0]["kind"] == "pr-comment"
    assert sent_items[0]["actor"] == "octocat"


def test_post_enqueues_and_returns_queued_on_rate_limit(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """現状固定。待ち行列が空でも上限失敗なら queued を返し、項目を保存する。"""
    q = post_queue.Queue(tmp_path)

    def fake_send(item):
        return post_queue.Attempt(1, "", "API rate limit exceeded (HTTP 429)")

    monkeypatch.setattr(post_queue, "send", fake_send)

    outcome, attempt = post_queue.post(
        q,
        "pr-comment",
        "devbasex/ai-plugins",
        757,
        {"body": "rate-limited-post"},
        actor="octocat",
        extra={"ident": "test-ident"},
    )

    assert outcome == post_queue.QUEUED
    assert attempt is not None
    assert attempt.ok is False
    assert post_queue.is_rate_limited(attempt) is True
    assert q.count() == 1
    items = q.items()
    assert len(items) == 1
    path, saved_item = items[0]
    assert saved_item["seq"] == 1
    assert saved_item["kind"] == "pr-comment"
    assert saved_item["actor"] == "octocat"
    assert saved_item["attempts"] == 1
    assert "rate limit" in saved_item["last_error"]
    assert saved_item["match"]["body"] == "rate-limited-post"
    assert "test-ident" in path.name


def test_post_returns_failed_and_does_not_enqueue_on_normal_failure(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """現状固定。通常失敗は failed を返し、待ち行列へ保存しない。"""
    q = post_queue.Queue(tmp_path)

    def fake_send(item):
        return post_queue.Attempt(1, "", "permission denied (HTTP 403)")

    monkeypatch.setattr(post_queue, "send", fake_send)
    monkeypatch.setattr(post_queue, "quota_remaining", lambda: 100)

    outcome, attempt = post_queue.post(
        q,
        "pr-comment",
        "devbasex/ai-plugins",
        757,
        {"body": "forbidden-post"},
        actor="octocat",
    )

    assert outcome == post_queue.FAILED
    assert attempt is not None
    assert attempt.ok is False
    assert post_queue.is_rate_limited(attempt) is False
    assert q.count() == 0


def test_post_enqueues_behind_unflushed_items_preserving_order(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """現状固定。先客を流せない場合は直接送信せず後ろへ積み、順序を維持する。"""
    existing_path = _write_item(tmp_path, 1)
    q = post_queue.Queue(tmp_path)

    monkeypatch.setattr(post_queue, "posted_match", lambda item: (False, None))
    sent_items: list[dict[str, Any]] = []

    def fake_send(item):
        sent_items.append(item)
        return post_queue.Attempt(1, "", "API rate limit exceeded (HTTP 429)")

    monkeypatch.setattr(post_queue, "send", fake_send)

    outcome, attempt = post_queue.post(
        q,
        "pr-comment",
        "devbasex/ai-plugins",
        757,
        {"body": "new-post"},
        actor="octocat",
        extra={"ident": "followup"},
    )

    assert outcome == post_queue.QUEUED
    assert attempt is None
    assert q.count() == 2
    paths = q.paths()
    assert len(paths) == 2
    assert paths[0].name == existing_path.name
    assert paths[1].name == "0002-pr-comment-followup.json"
    items = q.items()
    assert items[0][1]["seq"] == 1
    assert items[1][1]["seq"] == 2
    assert items[1][1]["match"]["body"] == "new-post"
    assert items[1][1]["attempts"] == 0
    assert len(sent_items) == 1
    assert sent_items[0]["seq"] == 1



# ---------------- 拒まれ方の区別（#730） ----------------

# 実測した応答（2026-09-22、Pull Request #794）。要求ごとに全件が拒まれ、
# `errors` は語をつないだ 1 つの文字列で、どの項目かは指さない。
_UNRESOLVED_LINE = json.dumps({
    "message": "Unprocessable Entity",
    "errors": ["Line could not be resolved"],
    "status": "422",
})
_UNRESOLVED_MANY = json.dumps({
    "message": "Unprocessable Entity",
    "errors": ["Line could not be resolved, Path could not be resolved,"
               " and Line could not be resolved"],
    "status": "422",
})
_BAD_EVENT = json.dumps({
    "message": "Unprocessable Entity",
    "errors": ["Variable $event of type PullRequestReviewEvent"
               " was provided invalid value"],
    "status": "422",
})
_BAD_COMMIT = json.dumps({
    "message": "Unprocessable Entity",
    "errors": ["The commitOID is not part of the pull request"],
    "status": "422",
})
_STDERR_422 = "gh: Unprocessable Entity (HTTP 422)\n"


def _attempt(stdout: str, stderr: str = _STDERR_422) -> Any:
    return post_queue.Attempt(1, stdout, stderr)


@pytest.mark.parametrize("stdout", [_UNRESOLVED_LINE, _UNRESOLVED_MANY])
def test_a_rejection_that_cannot_resolve_the_position_is_told_apart(stdout: str) -> None:
    """行やファイルを解決できない拒まれ方だけを、退避の契機として見分ける。"""
    assert post_queue.is_position_unresolved(_attempt(stdout)) is True


@pytest.mark.parametrize("stdout", [_BAD_EVENT, _BAD_COMMIT])
def test_another_rejection_of_the_same_status_is_not_a_reason_to_move(stdout: str) -> None:
    """判定の値の誤りと基準のコミットの誤りは、退避せず失敗として残す。"""
    assert post_queue.is_position_unresolved(_attempt(stdout)) is False


def test_a_rejection_of_another_status_is_not_a_reason_to_move() -> None:
    assert post_queue.is_position_unresolved(
        post_queue.Attempt(1, '{"message":"Not Found"}', "gh: Not Found (HTTP 404)")
    ) is False


def test_a_success_is_not_a_rejection() -> None:
    assert post_queue.is_position_unresolved(post_queue.Attempt(0, "{}", "")) is False


@pytest.mark.parametrize("stdout", [_UNRESOLVED_LINE, _BAD_EVENT])
def test_the_words_of_the_rejection_are_readable(stdout: str) -> None:
    """応答の `errors` が文字列の列でも、失敗の説明に語が残る。"""
    assert "could not be resolved" in _attempt(_UNRESOLVED_LINE).message
    assert _attempt(stdout).message != ""


def test_a_rejection_that_cannot_resolve_the_position_is_not_a_rate_limit() -> None:
    assert post_queue.is_rate_limited(_attempt(_UNRESOLVED_LINE)) is False


@pytest.mark.parametrize("item, expected", [
    ({"last_status": 422, "last_error": "Line could not be resolved"}, True),
    ({"last_status": 422, "last_error": "Invalid event"}, False),
    ({"last_status": 404, "last_error": "Line could not be resolved"}, False),
    ({"last_status": None, "last_error": "Line could not be resolved"}, False),
])
def test_a_queued_item_is_told_apart_by_its_status_and_words(
        item: dict[str, Any], expected: bool) -> None:
    """流した後に残った項目も、422 と位置の語がそろうときだけ位置の拒否と見る。"""
    assert post_queue.rejected_by_position(item) is expected


# ---------------- 上限のときに待って再実行する（R1-005） ----------------

_OK = post_queue.Attempt(0, '{"id": 1}', "")
_RATE = post_queue.Attempt(1, "", "API rate limit exceeded (HTTP 429)")
_NORMAL_FAIL = post_queue.Attempt(1, "", "permission denied (HTTP 403)")


def _run_returning(responses: list[Any], calls: list[list[str]]):
    """`run` の代わりに、応答列を順に返す疑似実装。呼ばれた cmd を記録する。"""
    queue = list(responses)

    def fake_run(cmd, stdin=None):
        calls.append(cmd)
        return queue.pop(0)

    return fake_run


def _recording_sleep(waits: list[float]):
    """時間を進めず、待った秒数だけ記録する疑似 sleep。"""

    def sleep(seconds):
        waits.append(seconds)

    return sleep


def test_retry_returns_immediately_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """現状固定。最初の実行が成功したら、待たずにその結果を返す。"""
    calls: list[list[str]] = []
    waits: list[float] = []
    monkeypatch.setattr(post_queue, "run", _run_returning([_OK], calls))
    monkeypatch.setattr(post_queue, "quota_remaining", lambda: 0)

    result = post_queue.retry(["gh", "pr", "create"], sleep=_recording_sleep(waits))

    assert result is _OK
    assert calls == [["gh", "pr", "create"]]
    assert waits == []


def test_retry_returns_immediately_on_a_normal_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """現状固定。上限でない失敗は、待たずにそのまま返す。"""
    calls: list[list[str]] = []
    waits: list[float] = []
    monkeypatch.setattr(post_queue, "run", _run_returning([_NORMAL_FAIL], calls))
    monkeypatch.setattr(post_queue, "quota_remaining", lambda: 100)

    result = post_queue.retry(["gh", "pr", "create"], sleep=_recording_sleep(waits))

    assert result is _NORMAL_FAIL
    assert calls == [["gh", "pr", "create"]]
    assert waits == []


def test_retry_waits_and_re_runs_until_it_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """現状固定。上限のあいだ待って再実行し、成功したらその結果を返す。"""
    calls: list[list[str]] = []
    waits: list[float] = []
    monkeypatch.setattr(
        post_queue, "run", _run_returning([_RATE, _RATE, _OK], calls)
    )

    result = post_queue.retry(
        ["gh", "pr", "create"],
        max_wait=900.0,
        interval=30.0,
        sleep=_recording_sleep(waits),
    )

    assert result is _OK
    assert len(calls) == 3
    assert waits == [30.0, 30.0]
    assert sum(waits) <= 900.0


def test_retry_returns_the_last_rate_limited_attempt_when_the_wait_cap_is_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """現状固定。待機の上限に達したら、最後の上限応答を返す。"""
    calls: list[list[str]] = []
    waits: list[float] = []
    last_rate = post_queue.Attempt(1, "", "API rate limit exceeded (HTTP 429)")
    responses = [_RATE, _RATE, _RATE, last_rate]
    monkeypatch.setattr(post_queue, "run", _run_returning(responses, calls))

    result = post_queue.retry(
        ["gh", "pr", "create"],
        max_wait=90.0,
        interval=30.0,
        sleep=_recording_sleep(waits),
    )

    assert result is last_rate
    assert len(calls) == 4
    assert waits == [30.0, 30.0, 30.0]
    assert sum(waits) <= 90.0


def test_retry_returns_the_first_rate_limited_attempt_when_interval_is_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """現状固定。待機間隔が 0 なら、待機も再実行もせず最初の応答を返す。"""
    calls: list[list[str]] = []
    waits: list[float] = []
    first_rate = post_queue.Attempt(1, "", "API rate limit exceeded (HTTP 429)")
    monkeypatch.setattr(post_queue, "run", _run_returning([first_rate], calls))

    result = post_queue.retry(
        ["gh", "pr", "create"],
        interval=0,
        sleep=_recording_sleep(waits),
    )

    assert result is first_rate
    assert calls == [["gh", "pr", "create"]]
    assert waits == []


def _write_kind(directory: pathlib.Path, seq: int, kind: str) -> pathlib.Path:
    path = directory / f"{seq:04d}-{kind}-{seq}.json"
    path.write_text(json.dumps({
        "seq": seq, "kind": kind, "repo": "devbasex/ai-plugins", "pr": 951,
        "attempts": 0, "request": {"method": "POST", "path": f"items/{seq}"},
        "match": {"body": f"body-{seq}"},
    }), encoding="utf-8")
    return path


_PARENT_NOT_FOUND = post_queue.Attempt(
    1, '{"message": "Parent comment not found"}', "gh: Not Found (HTTP 404)")
_UNPROCESSABLE = post_queue.Attempt(
    1, '{"message": "Validation Failed"}', "gh: Unprocessable Entity (HTTP 422)")


@pytest.mark.parametrize("failure", [_PARENT_NOT_FOUND, _UNPROCESSABLE])
def test_flush_sets_aside_a_permanent_failure_and_sends_the_rest(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, failure
) -> None:
    """送れない返信が先頭にあっても、後ろの決着とまとめを送る（#962）。

    レビューの ID へ返信すると GitHub は恒久的な 4xx を返す。先頭で止めると
    何度流しても `PENDING_REMAINING` が減らない。
    """
    _write_kind(tmp_path, 2, "review-reply")
    _write_kind(tmp_path, 3, "thread-resolve")
    _write_kind(tmp_path, 4, "pr-comment")
    sent_sequences: list[int] = []

    def send(item):
        sent_sequences.append(item["seq"])
        if item["seq"] == 2:
            return failure
        return post_queue.Attempt(0, '{"id": 1}', "")

    monkeypatch.setattr(post_queue, "posted_match", lambda item: (False, None))
    monkeypatch.setattr(post_queue, "send", send)

    result = post_queue.Queue(tmp_path).flush()

    assert sent_sequences == [2, 3, 4]
    assert [i["seq"] for i in result.sent] == [3, 4]
    assert [i["seq"] for i in result.dropped] == [2]
    assert result.failed is None
    assert result.remaining == 0
    # 飛ばした項目は捨てずに脇へ置き、理由を残す。
    kept = json.loads((tmp_path / "dropped" / "0002-review-reply-2.json").read_text("utf-8"))
    assert "exit=1" in kept["last_error"] and kept["last_status"] in (404, 422)


def test_flush_still_stops_at_a_rejected_review(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """レビューの拒否は飛ばさない。呼び出し側が退避か失敗かを決める。"""
    _write_kind(tmp_path, 1, "review-post")
    _write_kind(tmp_path, 2, "pr-comment")
    monkeypatch.setattr(post_queue, "posted_match", lambda item: (False, None))
    monkeypatch.setattr(post_queue, "send", lambda item: _UNPROCESSABLE)

    result = post_queue.Queue(tmp_path).flush()

    assert result.failed["seq"] == 1
    assert result.dropped == []
    assert result.remaining == 2


def test_flush_cli_prints_the_dropped_count(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    _write_kind(tmp_path, 1, "review-reply")
    monkeypatch.setattr(post_queue, "posted_match", lambda item: (False, None))
    monkeypatch.setattr(post_queue, "send", lambda item: _PARENT_NOT_FOUND)

    post_queue.cmd_flush(type("A", (), {"dir": str(tmp_path)})())

    out = capsys.readouterr().out
    assert "PENDING_DROPPED=1" in out and "PENDING_REMAINING=0" in out
