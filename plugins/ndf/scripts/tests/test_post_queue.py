"""post_queue.py の post 現状固定テスト。"""
from __future__ import annotations

import pathlib
import sys

import pytest

LIB = pathlib.Path(__file__).resolve().parents[1] / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import post_queue
from post_queue import FAILED, POSTED, QUEUED, Attempt, Queue, post


def test_post_success(tmp_path, monkeypatch):
    """先客がなく送信成功時は POSTED と attempt を返す。"""
    q = Queue(tmp_path / "pending")
    sent_items = []

    def fake_send(item):
        sent_items.append(item)
        return Attempt(0, '{"id": 123}', "")

    monkeypatch.setattr(post_queue, "send", fake_send)

    outcome, attempt = post(
        q,
        "pr-comment",
        "owner/repo",
        42,
        {"body": "LGTM"},
        actor="test-user",
        extra={"ident": "c1"},
    )

    assert outcome == POSTED
    assert attempt is not None
    assert attempt.ok
    assert q.count() == 0
    assert len(sent_items) == 1
    assert sent_items[0]["kind"] == "pr-comment"
    assert sent_items[0]["repo"] == "owner/repo"
    assert sent_items[0]["pr"] == 42
    assert sent_items[0]["actor"] == "test-user"


def test_post_queued_when_pending_remains(tmp_path, monkeypatch):
    """先客がいて flush 後も残る場合は送らずに積んで QUEUED を返す。"""
    q = Queue(tmp_path / "pending")
    # 先客を 1 件追加しておく
    post_queue.enqueue(q, "pr-comment", "owner/repo", 42, {"body": "prior comment"})
    assert q.count() == 1

    # flush しても消化されないようにする
    monkeypatch.setattr(q, "flush", lambda: None)
    send_called = False

    def fake_send(item):
        nonlocal send_called
        send_called = True
        return Attempt(0, "{}", "")

    monkeypatch.setattr(post_queue, "send", fake_send)

    outcome, attempt = post(
        q,
        "pr-comment",
        "owner/repo",
        42,
        {"body": "new comment"},
        actor="test-user",
    )

    assert outcome == QUEUED
    assert attempt is None
    assert not send_called
    assert q.count() == 2


def test_post_queued_when_rate_limited(tmp_path, monkeypatch):
    """送信時にレートリミットされた場合は積んで QUEUED を返す。"""
    q = Queue(tmp_path / "pending")
    rate_limit_attempt = Attempt(1, '{"message": "API rate limit exceeded"}', "(HTTP 403)")

    monkeypatch.setattr(post_queue, "send", lambda item: rate_limit_attempt)

    outcome, attempt = post(
        q,
        "pr-comment",
        "owner/repo",
        42,
        {"body": "rate limited comment"},
        actor="test-user",
    )

    assert outcome == QUEUED
    assert attempt is rate_limit_attempt
    assert q.count() == 1
    _, item = q.items()[0]
    assert item["attempts"] == 1
    assert "rate limit exceeded" in item["last_error"]


def test_post_failed_on_other_error(tmp_path, monkeypatch):
    """レートリミット以外の送信失敗時は積まずに FAILED を返す。"""
    q = Queue(tmp_path / "pending")
    error_attempt = Attempt(1, '{"message": "Not Found"}', "(HTTP 404)")

    monkeypatch.setattr(post_queue, "send", lambda item: error_attempt)

    outcome, attempt = post(
        q,
        "pr-comment",
        "owner/repo",
        42,
        {"body": "failed comment"},
        actor="test-user",
    )

    assert outcome == FAILED
    assert attempt is error_attempt
    assert q.count() == 0
