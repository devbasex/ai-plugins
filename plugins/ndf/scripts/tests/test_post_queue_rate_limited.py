"""`is_rate_limited` の現状固定テスト（R6-002）。

上限判定の各経路を固定する。`quota_remaining` はネットワークを叩くため
スタブ化する。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

POST_QUEUE = Path(__file__).resolve().parents[1] / "lib" / "post_queue.py"


@pytest.fixture(scope="module")
def post_queue():
    spec = importlib.util.spec_from_file_location("ndf_lib_post_queue", POST_QUEUE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _attempt(post_queue, code=1, stdout="", stderr=""):
    return post_queue.Attempt(code, stdout, stderr)


def test_ok_is_not_rate_limited(post_queue):
    """終了ステータスが 0 なら上限ではない。"""
    a = _attempt(post_queue, code=0, stdout='{"message": "ok"}', stderr="")
    assert post_queue.is_rate_limited(a) is False


def test_403_with_rate_words_in_body(post_queue):
    """403 で本文に rate 語があれば上限。残り枠は引かない。"""
    a = _attempt(
        post_queue,
        code=1,
        stdout='{"message": "API rate limit exceeded"}',
        stderr="gh: (HTTP 403)",
    )
    assert post_queue.is_rate_limited(a) is True


def test_403_quota_zero_is_rate_limited(post_queue, monkeypatch):
    """403 で rate 語が無くても残り 0 なら上限。"""
    monkeypatch.setattr(post_queue, "quota_remaining", lambda: 0)
    a = _attempt(
        post_queue,
        code=1,
        stdout='{"message": "Resource not accessible"}',
        stderr="gh: (HTTP 403)",
    )
    assert post_queue.is_rate_limited(a) is True


def test_403_quota_remaining_is_not_rate_limited(post_queue, monkeypatch):
    """403 で rate 語が無く残りがあるなら上限ではない（権限の誤り）。"""
    monkeypatch.setattr(post_queue, "quota_remaining", lambda: 42)
    a = _attempt(
        post_queue,
        code=1,
        stdout='{"message": "Resource not accessible"}',
        stderr="gh: (HTTP 403)",
    )
    assert post_queue.is_rate_limited(a) is False


def test_403_quota_unknown_is_not_rate_limited(post_queue, monkeypatch):
    """403 で残り枠が読めない（None）なら上限ではない（止める側へ倒す）。"""
    monkeypatch.setattr(post_queue, "quota_remaining", lambda: None)
    a = _attempt(
        post_queue,
        code=1,
        stdout='{"message": "Resource not accessible"}',
        stderr="gh: (HTTP 403)",
    )
    assert post_queue.is_rate_limited(a) is False


def test_500_permission_error_is_not_rate_limited(post_queue):
    """403/429 以外の状態（500 系）は上限ではない。"""
    a = _attempt(
        post_queue,
        code=1,
        stdout='{"message": "Server Error"}',
        stderr="gh: (HTTP 500)",
    )
    assert post_queue.is_rate_limited(a) is False


def test_graphql_no_status_line_with_rate_words(post_queue):
    """GraphQL の失敗は状態行を持たない。語で上限と決める。"""
    a = _attempt(
        post_queue,
        code=1,
        stdout="",
        stderr="gh: GraphQL: API rate limit already exceeded for user",
    )
    assert post_queue.is_rate_limited(a) is True


def test_no_status_line_without_rate_words_is_not_rate_limited(post_queue):
    """状態行も rate 語も無ければ上限ではない。"""
    a = _attempt(
        post_queue,
        code=1,
        stdout="",
        stderr="gh: some other failure",
    )
    assert post_queue.is_rate_limited(a) is False
