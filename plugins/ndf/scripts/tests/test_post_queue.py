"""待ち行列を流す公開入口に対する現状固定テスト。"""
from __future__ import annotations

import json
import pathlib
import sys

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
