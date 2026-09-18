"""`retry` の現状固定テスト（R6-003）。

待って再実行するかの各経路を固定する。`run` と `is_rate_limited` はネットワークを
叩くためスタブ化し、`sleep` は呼ばれた秒数だけを記録する。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

POST_QUEUE = Path(__file__).resolve().parents[1] / "lib" / "post_queue.py"

CMD = ["gh", "pr", "create"]


@pytest.fixture(scope="module")
def post_queue():
    spec = importlib.util.spec_from_file_location("ndf_lib_post_queue_retry", POST_QUEUE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def stub(post_queue, monkeypatch):
    """`run` が順に返す結果を差し込み、呼び出しと待ちを記録する。"""
    calls: list[tuple[list[str], str | None]] = []
    slept: list[float] = []

    def install(*attempts):
        queue = list(attempts)

        def fake_run(cmd, stdin=None):
            calls.append((cmd, stdin))
            return queue.pop(0)

        monkeypatch.setattr(post_queue, "run", fake_run)
        monkeypatch.setattr(post_queue, "is_rate_limited",
                            lambda a: not a.ok and "rate" in a.stderr)
        return calls, slept

    install.sleep = slept.append
    return install


def _ok(post_queue):
    return post_queue.Attempt(0, "done", "")


def _limited(post_queue):
    return post_queue.Attempt(1, "", "rate limit")


def _other(post_queue):
    return post_queue.Attempt(1, "", "not found")


def test_success_returns_immediately(post_queue, stub):
    """成功なら 1 回で返し、待たない。"""
    calls, slept = stub(_ok(post_queue))
    got = post_queue.retry(CMD, stdin="body", sleep=stub.sleep)
    assert got.ok
    assert calls == [(CMD, "body")]
    assert slept == []


def test_other_failure_returns_immediately(post_queue, stub):
    """上限以外の失敗はそのまま返し、待たない。"""
    calls, slept = stub(_other(post_queue))
    got = post_queue.retry(CMD, sleep=stub.sleep)
    assert got.stderr == "not found"
    assert len(calls) == 1
    assert slept == []


def test_non_positive_interval_returns_without_waiting(post_queue, stub, capsys):
    """間隔が 0 以下なら上限でも待たずに返す。"""
    calls, slept = stub(_limited(post_queue))
    got = post_queue.retry(CMD, interval=0, sleep=stub.sleep)
    assert got.stderr == "rate limit"
    assert len(calls) == 1
    assert slept == []
    assert capsys.readouterr().err == ""


def test_budget_exceeded_returns_without_waiting(post_queue, stub):
    """次の待ちで上限の予算を超えるなら待たずに返す。"""
    calls, slept = stub(_limited(post_queue))
    got = post_queue.retry(CMD, max_wait=10, interval=30, sleep=stub.sleep)
    assert got.stderr == "rate limit"
    assert len(calls) == 1
    assert slept == []


def test_waits_once_then_succeeds(post_queue, stub, capsys):
    """上限なら 1 回待って再実行し、成功を返す。待つ旨を標準エラーへ出す。"""
    calls, slept = stub(_limited(post_queue), _ok(post_queue))
    got = post_queue.retry(CMD, max_wait=60, interval=30, sleep=stub.sleep)
    assert got.ok
    assert len(calls) == 2
    assert slept == [30]
    assert capsys.readouterr().err == (
        "⏳ 上限のため 30 秒待って再実行します: gh pr create\n")


def test_waits_until_budget_is_spent(post_queue, stub):
    """予算ちょうどまでは待ち、超える手前で最後の失敗を返す。"""
    calls, slept = stub(*[_limited(post_queue)] * 3)
    got = post_queue.retry(CMD, max_wait=60, interval=30, sleep=stub.sleep)
    assert got.stderr == "rate limit"
    assert len(calls) == 3
    assert slept == [30, 30]
