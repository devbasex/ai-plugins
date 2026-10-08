"""待ち行列を流す公開入口に対する現状固定テスト。"""

from __future__ import annotations

import json
import os
import pathlib
import signal
import subprocess
import sys
import time
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


def test_flush_skips_posted_sends_success_and_stops_at_rate_limit(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_flush_ignores_json_files_without_a_sequence_name(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """待ち行列のディレクトリに置かれた連番でない JSON は項目として読まず、触らない。"""
    other = tmp_path / "fix-pr1500-decisions.json"
    other.write_text(json.dumps({"decisions": []}), encoding="utf-8")
    _write_item(tmp_path, 1)
    seen: list[int] = []

    def posted_match(item):
        seen.append(item["seq"])
        return False, None

    monkeypatch.setattr(post_queue, "posted_match", posted_match)
    monkeypatch.setattr(post_queue, "send", lambda item: post_queue.Attempt(0, '{"id": 1}', ""))

    queue = post_queue.Queue(tmp_path)
    result = queue.flush()

    assert seen == [1]
    assert [item["seq"] for item in result.sent] == [1]
    assert result.failed is None and result.remaining == 0
    assert queue.paths() == [] and queue.count() == 0
    assert json.loads(other.read_text(encoding="utf-8")) == {"decisions": []}


def test_flush_stops_at_a_normal_send_failure(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert [path.name for path in post_queue.Queue(tmp_path).paths()] == [path.name for path in paths]


def test_flush_stops_at_corrupt_json_and_keeps_following_items(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
def test_drop_keeps_items_when_the_sequence_does_not_match(tmp_path: pathlib.Path, seq: int | None) -> None:
    """現状固定。連番が無い場合は何も取り除かない。"""
    paths = [_write_item(tmp_path, item_seq) for item_seq in range(1, 3)]
    queue = post_queue.Queue(tmp_path)

    assert queue.drop(seq) is False
    assert [path.name for path in queue.paths()] == [path.name for path in paths]


def test_post_succeeds_directly_when_queue_is_empty(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_post_enqueues_and_returns_queued_on_rate_limit(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_post_returns_failed_and_does_not_enqueue_on_normal_failure(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_post_enqueues_behind_unflushed_items_preserving_order(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
_UNRESOLVED_LINE = json.dumps(
    {
        "message": "Unprocessable Entity",
        "errors": ["Line could not be resolved"],
        "status": "422",
    }
)
_UNRESOLVED_MANY = json.dumps(
    {
        "message": "Unprocessable Entity",
        "errors": ["Line could not be resolved, Path could not be resolved, and Line could not be resolved"],
        "status": "422",
    }
)
_BAD_EVENT = json.dumps(
    {
        "message": "Unprocessable Entity",
        "errors": ["Variable $event of type PullRequestReviewEvent was provided invalid value"],
        "status": "422",
    }
)
_BAD_COMMIT = json.dumps(
    {
        "message": "Unprocessable Entity",
        "errors": ["The commitOID is not part of the pull request"],
        "status": "422",
    }
)
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
    assert post_queue.is_position_unresolved(post_queue.Attempt(1, '{"message":"Not Found"}', "gh: Not Found (HTTP 404)")) is False


def test_a_success_is_not_a_rejection() -> None:
    assert post_queue.is_position_unresolved(post_queue.Attempt(0, "{}", "")) is False


@pytest.mark.parametrize("stdout", [_UNRESOLVED_LINE, _BAD_EVENT])
def test_the_words_of_the_rejection_are_readable(stdout: str) -> None:
    """応答の `errors` が文字列の列でも、失敗の説明に語が残る。"""
    assert "could not be resolved" in _attempt(_UNRESOLVED_LINE).message
    assert _attempt(stdout).message != ""


def test_a_rejection_that_cannot_resolve_the_position_is_not_a_rate_limit() -> None:
    assert post_queue.is_rate_limited(_attempt(_UNRESOLVED_LINE)) is False


@pytest.mark.parametrize(
    "item, expected",
    [
        ({"last_status": 422, "last_error": "Line could not be resolved"}, True),
        ({"last_status": 422, "last_error": "Invalid event"}, False),
        ({"last_status": 404, "last_error": "Line could not be resolved"}, False),
        ({"last_status": None, "last_error": "Line could not be resolved"}, False),
    ],
)
def test_a_queued_item_is_told_apart_by_its_status_and_words(item: dict[str, Any], expected: bool) -> None:
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
    monkeypatch.setattr(post_queue, "run", _run_returning([_RATE, _RATE, _OK], calls))

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
    path.write_text(
        json.dumps(
            {
                "seq": seq,
                "kind": kind,
                "repo": "devbasex/ai-plugins",
                "pr": 951,
                "attempts": 0,
                "request": {"method": "POST", "path": f"items/{seq}"},
                "match": {"body": f"body-{seq}"},
            }
        ),
        encoding="utf-8",
    )
    return path


_PARENT_NOT_FOUND = post_queue.Attempt(1, '{"message": "Parent comment not found"}', "gh: Not Found (HTTP 404)")
_UNPROCESSABLE = post_queue.Attempt(1, '{"message": "Validation Failed"}', "gh: Unprocessable Entity (HTTP 422)")


@pytest.mark.parametrize("failure", [_PARENT_NOT_FOUND, _UNPROCESSABLE])
def test_flush_sets_aside_a_permanent_failure_and_sends_the_rest(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, failure) -> None:
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


def test_flush_still_stops_at_a_rejected_review(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """レビューの拒否は飛ばさない。呼び出し側が退避か失敗かを決める。"""
    _write_kind(tmp_path, 1, "review-post")
    _write_kind(tmp_path, 2, "pr-comment")
    monkeypatch.setattr(post_queue, "posted_match", lambda item: (False, None))
    monkeypatch.setattr(post_queue, "send", lambda item: _UNPROCESSABLE)

    result = post_queue.Queue(tmp_path).flush()

    assert result.failed["seq"] == 1
    assert result.dropped == []
    assert result.remaining == 2


def test_flush_cli_prints_the_dropped_count(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    _write_kind(tmp_path, 1, "review-reply")
    monkeypatch.setattr(post_queue, "posted_match", lambda item: (False, None))
    monkeypatch.setattr(post_queue, "send", lambda item: _PARENT_NOT_FOUND)

    post_queue.cmd_flush(type("A", (), {"dir": str(tmp_path)})())

    out = capsys.readouterr().out
    assert "PENDING_DROPPED=1" in out and "PENDING_REMAINING=0" in out


# ---------------- 未解決のスレッドの識別子（I-011 の現状固定） ----------------


def test_unresolved_thread_ids_returns_stripped_nonblank_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """現状固定。gh の出力の行を前後の空白を除いて返し、空行は捨てる。"""
    calls: list[list[str]] = []
    out = post_queue.Attempt(0, "T_1\n  T_2  \n\n   \nT_3\n", "")
    monkeypatch.setattr(post_queue, "run", _run_returning([out], calls))

    assert post_queue.unresolved_thread_ids("octo/repo", 12) == ["T_1", "T_2", "T_3"]
    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[:5] == ["gh", "api", "graphql", "--paginate", "-F"]
    assert "owner=octo" in cmd
    assert "name=repo" in cmd
    assert "pr=12" in cmd
    assert f"query={post_queue._UNRESOLVED_QUERY}" in cmd
    assert cmd[-2:] == ["--jq", post_queue._UNRESOLVED_JQ]


def test_unresolved_thread_ids_empty_output_is_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """現状固定。成功して出力が空なら、`None` ではなく空の一覧を返す。"""
    calls: list[list[str]] = []
    monkeypatch.setattr(post_queue, "run", _run_returning([post_queue.Attempt(0, "", "")], calls))

    assert post_queue.unresolved_thread_ids("octo/repo", "7") == []
    assert "pr=7" in calls[0]


def test_unresolved_thread_ids_failure_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """現状固定。gh が失敗したら `None`（0 件と区別する）。"""
    calls: list[list[str]] = []
    monkeypatch.setattr(post_queue, "run", _run_returning([_NORMAL_FAIL], calls))

    assert post_queue.unresolved_thread_ids("octo/repo", 1) is None
    assert len(calls) == 1


@pytest.mark.parametrize("repo", ["", None, "octo", "octo/", "/repo"])
def test_unresolved_thread_ids_bad_repo_is_none_without_calling_gh(monkeypatch: pytest.MonkeyPatch, repo: Any) -> None:
    """現状固定。`owner/name` の形でない repo は gh を呼ばずに `None`。"""
    calls: list[list[str]] = []
    monkeypatch.setattr(post_queue, "run", _run_returning([], calls))

    assert post_queue.unresolved_thread_ids(repo, 1) is None
    assert calls == []


# ---------------- 耐久の記録（#1142 の Q2。送りの試行 1 回 = 耐久ワークフロー 1 つ） ----------------


def _ids(queue: post_queue.Queue) -> list[str]:
    with queue._session() as fl:
        return sorted(fl["durable"].workflow_ids("post-"))


def test_an_empty_queue_answers_without_opening_the_record(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """積んでいない待ち行列の count は耐久の記録を作らず、DBOS の読み込みと耐久ワークフローの登録（`_flows`）も起こさない。"""
    queue = post_queue.Queue(tmp_path / "pending")

    def no_flows() -> None:
        raise AssertionError("空の待ち行列で _flows を呼んだ")

    monkeypatch.setattr(post_queue, "_flows", no_flows)
    assert queue.count() == 0 and queue.paths() == [] and queue.items() == [] and queue.flush().remaining == 0
    assert not queue.drop(1)
    assert not post_queue.durable_keys.record_path("posts", str(queue.dir.absolute())).exists()


def test_an_empty_queue_does_not_import_dbos(tmp_path: pathlib.Path) -> None:
    """空の待ち行列の count / paths は、新しいプロセスで dbos も durable も読み込まない（空キューの高速経路）。"""
    code = (
        "import sys, pathlib; sys.path.insert(0, sys.argv[1]); import post_queue;"
        "q = post_queue.Queue(pathlib.Path(sys.argv[2])); assert q.count() == 0 and q.paths() == [];"
        "print('dbos' in sys.modules, 'durable' in sys.modules)"
    )
    env = {**os.environ, "NDF_DBOS_DIR": str(tmp_path / "db")}
    out = subprocess.run(
        [sys.executable, "-c", code, str(LIB), str(tmp_path / "pending")], capture_output=True, text=True, env=env, check=True
    )
    assert out.stdout.split() == ["False", "False"]


def test_legacy_items_are_imported_with_their_sequence(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """移行の前のファイルは最初の flush で同じ連番のまま送られ、`imported/` へ移る。"""
    for seq in (3, 7):
        _write_item(tmp_path, seq)
    sent: list[int] = []
    monkeypatch.setattr(post_queue, "posted_match", lambda item: (False, None))
    monkeypatch.setattr(post_queue, "send", lambda item: sent.append(item["seq"]) or post_queue.Attempt(0, "{}", ""))

    queue = post_queue.Queue(tmp_path)
    result = queue.flush()

    assert sent == [3, 7] and result.remaining == 0
    assert sorted(p.name for p in (tmp_path / "imported").iterdir()) == ["0003-pr-comment-3.json", "0007-pr-comment-7.json"]
    assert not list(tmp_path.glob("*.json"))
    assert post_queue.enqueue(queue, "pr-comment", "o/r", 1, {"body": "次"})["seq"] == 8


def test_an_unsent_item_is_retried_in_order_by_the_next_flush(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """上限の項目の後ろは送られず、次の flush が次の試行で順に送る。試行ごとに耐久ワークフローが 1 つ増える。"""
    queue = post_queue.Queue(tmp_path)
    for body in ("一", "二"):
        post_queue.enqueue(queue, "pr-comment", "o/r", 5, {"body": body}, extra={"ident": body})
    limited = {"on": True}
    sent: list[str] = []

    def send(item):
        if limited["on"]:
            return post_queue.Attempt(1, "", "API rate limit exceeded (HTTP 429)")
        sent.append(item["match"]["body"])
        return post_queue.Attempt(0, '{"id": 1}', "")

    monkeypatch.setattr(post_queue, "posted_match", lambda item: (False, None))
    monkeypatch.setattr(post_queue, "send", send)

    first = queue.flush()
    limited["on"] = False
    second = queue.flush()

    assert first.rate_limited is True and first.failed["seq"] == 1 and first.remaining == 2
    assert [i["seq"] for i in second.sent] == [1, 2] and second.remaining == 0 and sent == ["一", "二"]
    assert second.sent[0]["attempts"] == 1
    assert _ids(queue) == ["post-0001-pr-comment-一-a0", "post-0001-pr-comment-一-a1", "post-0001-pr-comment-一-a2"] + [
        "post-0002-pr-comment-二-a0",
        "post-0002-pr-comment-二-a1",
    ]


def test_flush_drop_flush_run_in_one_process_that_exits_normally(tmp_path: pathlib.Path) -> None:
    """result_posts の順（flush → drop → 積み直し → flush）を 1 つのプロセスで打ち、プロセスが普通に終わる。"""
    script = tmp_path / "run.py"
    script.write_text(
        f"""
import sys
sys.path.insert(0, {str(LIB)!r})
import post_queue
calls = []
def send(item):
    calls.append(item["seq"])
    if len(calls) == 1:
        return post_queue.Attempt(1, '{{"message": "Line could not be resolved"}}', "gh: Unprocessable Entity (HTTP 422)")
    return post_queue.Attempt(0, '{{"id": 9}}', "")
post_queue.send = send
post_queue.posted_match = lambda item: (False, None)
q = post_queue.Queue({str(tmp_path / "pending")!r})
seq = post_queue.enqueue(q, "review-post", "o/r", 1, {{"body": "b", "event": "COMMENT"}})["seq"]
first = q.flush()
assert post_queue.rejected_by_position(first.failed), first
assert q.drop(first.failed["seq"]) is True
seq = post_queue.enqueue(q, "review-post", "o/r", 1, {{"body": "b2", "event": "COMMENT"}})["seq"]
second = q.flush()
print(seq, [i["seq"] for i in second.sent], second.remaining, calls)
""",
        encoding="utf-8",
    )
    out = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=120)

    assert out.returncode == 0, out.stderr
    assert out.stdout.split("\n")[0] == "2 [2] 0 [1, 2]"


def test_an_attempt_cut_by_kill_is_sent_again_after_checking_it_was_not_posted(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """送りの途中で kill -9 された試行は次に開くときに止め、次の flush が既投稿の照合から次の試行で送る。"""
    started = tmp_path / "started"
    script = tmp_path / "hang.py"
    script.write_text(
        f"""
import pathlib, sys, time
sys.path.insert(0, {str(LIB)!r})
import post_queue
def send(item):
    pathlib.Path({str(started)!r}).write_text("x")
    time.sleep(60)
post_queue.send = send
post_queue.posted_match = lambda item: (False, None)
q = post_queue.Queue({str(tmp_path / "pending")!r})
post_queue.enqueue(q, "pr-comment", "o/r", 1, {{"body": "b"}})
q.flush()
""",
        encoding="utf-8",
    )
    proc = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 60
        while not started.exists() and time.monotonic() < deadline and proc.poll() is None:
            time.sleep(0.05)
        assert started.exists(), proc.communicate(timeout=5)
    finally:
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=10)
    checked: list[int] = []
    monkeypatch.setattr(post_queue, "posted_match", lambda item: checked.append(item["seq"]) or (False, None))
    monkeypatch.setattr(post_queue, "send", lambda item: post_queue.Attempt(0, '{"id": 3}', ""))

    queue = post_queue.Queue(tmp_path / "pending")
    result = queue.flush()

    assert checked == [1] and [i["seq"] for i in result.sent] == [1] and result.remaining == 0
    assert _ids(queue) == ["post-0001-pr-comment-1-a0", "post-0001-pr-comment-1-a1", "post-0001-pr-comment-1-a2"]


# ---------------- 一時的な失敗（#1843） ----------------

# 前提 1 の 3 つの形。500 の本文なしは PR 1842 の実例、ネットワークの語は gh 2.101.0 の実測。
_TRANSIENT_FAILURES = [
    post_queue.Attempt(1, '{"message": "Server Error"}', "gh: Server Error (HTTP 500)"),
    post_queue.Attempt(1, "", "gh: Bad Gateway (HTTP 502)"),
    post_queue.Attempt(1, "", "gh: Service Unavailable (HTTP 503)"),
    post_queue.Attempt(1, "", "gh: Gateway Timeout (HTTP 504)"),
    post_queue.Attempt(1, "", "unexpected end of JSON input"),
    post_queue.Attempt(1, "", "error connecting to nonexistent.invalid"),
    post_queue.Attempt(1, "", 'Post "https://api.github.com/graphql": dial tcp 127.0.0.1:9: connect: connection refused'),
]
_NOT_TRANSIENT = [
    post_queue.Attempt(0, "", ""),
    post_queue.Attempt(1, "", "gh: Bad Request (HTTP 400)"),
    post_queue.Attempt(1, "", "gh: Not Found (HTTP 404)"),
    post_queue.Attempt(1, "", "gh: Gone (HTTP 410)"),
    post_queue.Attempt(1, "", "gh: Unprocessable Entity (HTTP 422)"),
    post_queue.Attempt(1, "", "API rate limit exceeded (HTTP 429)"),
    post_queue.Attempt(1, "", "API rate limit exceeded (HTTP 403)"),
    post_queue.Attempt(1, "", "gh: Server Error rate limit (HTTP 500)"),
    # 本文がある応答は「読めなかった」に当たらない
    post_queue.Attempt(1, '{"message": "x"}', "unexpected end of JSON input"),
    post_queue.Attempt(1, "", "permission denied"),
]


@pytest.mark.parametrize("attempt", _TRANSIENT_FAILURES)
def test_a_transient_failure_is_told_apart(attempt: post_queue.Attempt) -> None:
    assert post_queue.is_transient_failure(attempt) is True
    assert post_queue.is_rate_limited(attempt) is False


@pytest.mark.parametrize("attempt", _NOT_TRANSIENT)
def test_other_failures_are_not_transient(attempt: post_queue.Attempt, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(post_queue, "quota_remaining", lambda: pytest.fail("一時的な失敗の判定は残り回数を引かない"))
    assert post_queue.is_transient_failure(attempt) is False


def test_the_wait_for_transient_failures_stays_within_the_rate_limit_retry() -> None:
    assert 0 < post_queue.TRANSIENT_INTERVAL <= post_queue.TRANSIENT_MAX_WAIT <= 900.0


@pytest.mark.parametrize("failure", _TRANSIENT_FAILURES[:1] + _TRANSIENT_FAILURES[4:6])
@pytest.mark.parametrize("kind", ["pr-comment", "review-post", "review-reply"])
def test_flush_keeps_an_item_on_a_transient_failure(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, failure: post_queue.Attempt, kind: str
) -> None:
    """一時的な失敗は控えず、待てば流れる印を立てて先頭に残す（AC1）。"""
    path = _write_kind(tmp_path, 1, kind)
    monkeypatch.setattr(post_queue, "posted_match", lambda item: (False, None))
    monkeypatch.setattr(post_queue, "send", lambda item: failure)

    result = post_queue.Queue(tmp_path).flush()

    assert result.failed["seq"] == 1 and result.failed["last_transient"] is True
    assert result.transient is True and result.rate_limited is False and result.waitable is True
    assert result.dropped == [] and result.remaining == 1
    assert [p.name for p in post_queue.Queue(tmp_path).paths()] == [path.name]
    assert not (tmp_path / "dropped").exists()


@pytest.mark.parametrize(
    "failure, rate_limited",
    [
        (post_queue.Attempt(1, "", "API rate limit exceeded (HTTP 429)"), True),
        (post_queue.Attempt(1, "", "gh: Bad Request (HTTP 400)"), False),
    ],
)
def test_other_failures_do_not_raise_the_transient_mark(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, failure: post_queue.Attempt, rate_limited: bool
) -> None:
    _write_kind(tmp_path, 1, "review-post")
    monkeypatch.setattr(post_queue, "posted_match", lambda item: (False, None))
    monkeypatch.setattr(post_queue, "send", lambda item: failure)

    result = post_queue.Queue(tmp_path).flush()

    assert result.transient is False and result.rate_limited is rate_limited and result.waitable is rate_limited


def test_an_item_left_by_a_transient_failure_is_not_sent_while_it_cannot_be_checked(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """届いたか分からない項目は、照会ができないうちは送らない（決定 3・AC7）。"""
    _write_kind(tmp_path, 1, "review-post")
    sends: list[int] = []
    monkeypatch.setattr(post_queue, "posted_match", lambda item: (False, None))
    monkeypatch.setattr(post_queue, "send", lambda item: sends.append(item["seq"]) or _TRANSIENT_FAILURES[0])
    post_queue.Queue(tmp_path).flush()
    assert sends == [1]

    monkeypatch.setattr(post_queue, "posted_match", lambda item: (None, None))
    result = post_queue.Queue(tmp_path).flush()

    assert sends == [1]
    assert result.transient is True and result.remaining == 1

    monkeypatch.setattr(post_queue, "posted_match", lambda item: (True, {"id": 9, "body": "x"}))
    result = post_queue.Queue(tmp_path).flush()

    assert sends == [1]
    assert [i["seq"] for i in result.skipped] == [1] and result.remaining == 0


def test_an_item_left_by_a_rate_limit_is_still_sent_when_it_cannot_be_checked(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_kind(tmp_path, 1, "pr-comment")
    sends: list[int] = []
    monkeypatch.setattr(post_queue, "posted_match", lambda item: (None, None))
    monkeypatch.setattr(
        post_queue, "send", lambda item: sends.append(item["seq"]) or post_queue.Attempt(1, "", "API rate limit exceeded (HTTP 429)")
    )
    post_queue.Queue(tmp_path).flush()
    post_queue.Queue(tmp_path).flush()

    assert sends == [1, 1]


def test_post_enqueues_and_returns_queued_on_a_transient_failure(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`pr-comment` の送りが一時的な失敗なら積んで先へ進む（AC10）。"""
    q = post_queue.Queue(tmp_path)
    monkeypatch.setattr(post_queue, "send", lambda item: post_queue.Attempt(1, "", "unexpected end of JSON input"))

    outcome, attempt = post_queue.post(q, "pr-comment", "devbasex/ai-plugins", 757, {"body": "b"}, extra={"ident": "t"})

    assert outcome == post_queue.QUEUED and attempt is not None
    [(_, saved)] = q.items()
    assert saved["last_transient"] is True and saved["attempts"] == 1


def test_post_cli_exits_zero_on_a_transient_failure(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    body = tmp_path / "body.md"
    body.write_text("b", encoding="utf-8")
    monkeypatch.setattr(post_queue, "send", lambda item: post_queue.Attempt(1, "", "gh: Server Error (HTTP 500)"))
    args = type("A", (), {"dir": str(tmp_path / "q"), "kind": "pr-comment", "repo": "o/r", "pr": 1, "body_file": str(body), "actor": ""})()

    assert post_queue.cmd_post(args) == 0
    assert "QUEUED=1" in capsys.readouterr().out


def test_flush_cli_prints_the_transient_mark(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    _write_kind(tmp_path, 1, "pr-comment")
    monkeypatch.setattr(post_queue, "posted_match", lambda item: (False, None))
    monkeypatch.setattr(post_queue, "send", lambda item: _TRANSIENT_FAILURES[1])

    post_queue.cmd_flush(type("A", (), {"dir": str(tmp_path)})())

    assert "PENDING_TRANSIENT=1" in capsys.readouterr().out.splitlines()
