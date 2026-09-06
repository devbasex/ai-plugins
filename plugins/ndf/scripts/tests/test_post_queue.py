from __future__ import annotations

import importlib.util
import pathlib
import sys


POST_QUEUE_PATH = pathlib.Path(__file__).resolve().parents[1] / "lib" / "post_queue.py"


def load_post_queue():
    spec = importlib.util.spec_from_file_location("ndf_post_queue", POST_QUEUE_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def item(kind: str, match: dict, actor: str | None = "bot") -> dict:
    return {"kind": kind, "repo": "devbasex/ai-plugins", "pr": 435,
            "actor": actor, "match": match}


def test_posted_match_finds_a_pr_comment(monkeypatch):
    post_queue = load_post_queue()
    row = {"body": "done", "user": {"login": "bot"}}
    monkeypatch.setattr(post_queue, "_list_all", lambda path: [row])

    found, matched = post_queue.posted_match(item("pr-comment", {"body": "done"}))

    assert found is True
    assert matched == row


def test_posted_match_finds_a_review_post(monkeypatch):
    post_queue = load_post_queue()
    row = {"state": "CHANGES_REQUESTED", "body": "x" * 100, "user": {"login": "bot"}}
    monkeypatch.setattr(post_queue, "_list_all", lambda path: [row])

    found, matched = post_queue.posted_match(
        item("review-post", {"event": "REQUEST_CHANGES", "body": "x" * 120})
    )

    assert found is True
    assert matched == row


def test_posted_match_finds_a_review_reply(monkeypatch):
    post_queue = load_post_queue()
    row = {"in_reply_to_id": 123, "body": "reply", "user": {"login": "someone"}}
    monkeypatch.setattr(post_queue, "_list_all", lambda path: [row])

    found, matched = post_queue.posted_match(
        item("review-reply", {"in_reply_to": 123, "body": "reply"})
    )

    assert found is True
    assert matched == row


def test_posted_match_treats_missing_unresolved_thread_as_resolved(monkeypatch):
    post_queue = load_post_queue()
    monkeypatch.setattr(post_queue, "unresolved_thread_ids", lambda repo, pr: ["other"])

    found, matched = post_queue.posted_match(
        item("thread-resolve", {"thread_id": "thread-1"})
    )

    assert found is True
    assert matched is None


def test_posted_match_returns_none_for_unknown_kind():
    post_queue = load_post_queue()

    assert post_queue.posted_match(item("unknown", {})) == (None, None)
