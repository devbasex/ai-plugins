"""判定の子の `_read`（1 回の回答を判定の返りにする）の振る舞いを固定する。

失敗の応答の組み立てを 1 か所へ寄せ、判定の語を `reply_judge.KINDS` から得る作り直しの前後で、
返りの形（キー・`calls`・`error`）と失敗の種類の優先順が変わらないことを見る。
"""

from __future__ import annotations

import pytest

from ndf.adapters import claude
from ndf.domains.hooks import judge_child as rj
from ndf.domains.hooks import reply_judge


def _ok(raw: str, error=None) -> claude.ClaudeReply:
    return claude.ClaudeReply(True, None, raw, 0.0, error)


@pytest.mark.parametrize("word", reply_judge.KINDS)
def test_read_returns_the_kind_and_one_call_without_an_error_key(word):
    assert rj._read(_ok(word)) == {"kind": word, "calls": 1}


@pytest.mark.parametrize("word", reply_judge.KINDS)
def test_read_drops_every_whitespace_around_and_inside_the_word(word):
    spaced = f"  {word[:2]} \n{word[2:]}\t\n"
    assert rj._read(_ok(spaced)) == {"kind": word, "calls": 1}


@pytest.mark.parametrize("failure", list(claude.Failure))
def test_read_passes_the_failure_with_its_error(failure):
    reply = claude.ClaudeReply.failed("Not logged in", "理由の文", failure=failure)
    assert rj._read(reply) == {"failure": str(failure), "calls": 1, "error": "理由の文"}


def test_read_puts_the_failure_before_the_ok_flag_and_the_word():
    # ok が真で語が正しくても failure があれば失敗として返す
    reply = claude.ClaudeReply(True, None, "wait", 0.0, "理由", claude.Failure.TIMEOUT)
    assert rj._read(reply) == {"failure": "timeout", "calls": 1, "error": "理由"}


def test_read_returns_auth_with_the_error_when_the_raw_has_a_marker():
    marker = rj.setting("auth_markers")[0]
    reply = claude.ClaudeReply.failed(f"前置き {marker} 後書き", "誤りの文")
    assert rj._read(reply) == {"failure": "auth", "calls": 1, "error": "誤りの文"}


def test_read_returns_exit_with_the_error_when_the_raw_has_no_marker():
    reply = claude.ClaudeReply.failed("wait", "誤りの文")
    assert rj._read(reply) == {"failure": "exit", "calls": 1, "error": "誤りの文"}


def test_read_returns_exit_with_a_none_error():
    reply = claude.ClaudeReply(False, None, "", 0.0)
    assert rj._read(reply) == {"failure": "exit", "calls": 1, "error": None}


@pytest.mark.parametrize("raw", ["Wait", "wait.", "", "wait report", "待ち"])
def test_read_returns_shape_with_the_error_of_the_reply(raw):
    assert rj._read(_ok(raw, "残りの文")) == {"failure": "shape", "calls": 1, "error": "残りの文"}
    assert rj._read(_ok(raw)) == {"failure": "shape", "calls": 1, "error": None}


def test_read_ignores_the_structured_answer():
    reply = claude.ClaudeReply(True, {"kind": "wait"}, "report", 0.0)
    assert rj._read(reply) == {"kind": "report", "calls": 1}
