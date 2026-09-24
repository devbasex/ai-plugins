"""Jev への問い（#933 の AC22 AC23 の部品）。

HTTP は偽の応答へ差し替える。鍵の値はどこにも書かない（`AI_GATEWAY_API_KEY` は
テストの中だけで偽の値を置く）。
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

LIB = pathlib.Path(__file__).resolve().parents[1] / "lib"


@pytest.fixture(scope="module")
def jev():
    spec = importlib.util.spec_from_file_location("ndf_lib_jev", LIB / "jev.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ndf_lib_jev"] = mod
    spec.loader.exec_module(mod)
    return mod


def _replying(answers):
    sent = []

    def post(body, timeout):
        sent.append((body, timeout))
        return None if answers is None else {"answers": answers}

    post.sent = sent
    return post


def test_boolean_returns_the_answer_and_its_probability(jev):
    post = _replying({"q": {"type": "boolean", "probability": 0.9}})
    assert jev.ask_boolean("s", "i", post=post) == (True, 0.9)


def test_boolean_false_reports_the_confidence_of_the_false_side(jev):
    post = _replying({"q": {"type": "boolean", "probability": 0.2}})
    value, confidence = jev.ask_boolean("s", "i", post=post)
    assert value is False and confidence == pytest.approx(0.8)


def test_score_picks_the_most_probable_criterion_and_the_reported_confidence(jev):
    post = _replying({"q": {"type": "score", "probabilities": {"0": 0.1, "1": 0.2, "2": 0.7},
                            "confidence": 0.55}})
    assert jev.ask_score("s", "i", ["low", "medium", "high"], post=post) == ("high", 0.55)
    body, _ = post.sent[0]
    assert body["questions"]["q"]["criteria"] == ["low", "medium", "high"]
    assert body["model"] == "typesafe-ai/jev"


@pytest.mark.parametrize("answers", [None, {}, {"q": {"type": "score"}},
                                     {"q": {"type": "score", "probabilities": {"9": 1.0}}}])
def test_a_failed_or_malformed_answer_is_none(jev, answers):
    assert jev.ask_score("s", "i", ["low", "high"], post=_replying(answers)) is None
    assert jev.ask_boolean("s", "i", post=_replying(answers)) is None


def test_decide_without_a_key_uses_the_runtime(jev):
    called = []
    judge = jev.decide(lambda: called.append(1) or True, env={}, post=_replying({}))
    assert judge == {"kind": "runtime", "reason": "no_key", "failures": 0}
    assert called == []  # 公開かを問い合わせない


def test_decide_respects_the_switch(jev):
    judge = jev.decide(lambda: True, env={"AI_GATEWAY_API_KEY": "x", "NDF_JEV": "0"})
    assert judge["reason"] == "disabled"


@pytest.mark.parametrize("public", [False, None])
def test_decide_does_not_use_jev_for_a_private_or_unknown_repository(jev, public):
    judge = jev.decide(lambda: public, env={"AI_GATEWAY_API_KEY": "x"})
    assert (judge["kind"], judge["reason"]) == ("runtime", "private_repo")


def test_decide_falls_back_when_the_probe_fails(jev):
    judge = jev.decide(lambda: True, env={"AI_GATEWAY_API_KEY": "x"}, post=_replying(None))
    assert (judge["kind"], judge["reason"]) == ("runtime", "probe_failed")


def test_decide_uses_jev_when_all_conditions_hold(jev):
    post = _replying({"q": {"type": "boolean", "probability": 0.99}})
    judge = jev.decide(lambda: True, env={"AI_GATEWAY_API_KEY": "x"}, post=post)
    assert judge == {"kind": "jev", "reason": None, "failures": 0}
    assert post.sent[0][1] == jev.PROBE_TIMEOUT


def test_the_default_post_does_not_send_without_a_key(jev, monkeypatch):
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    assert jev._post({"model": "x"}, 1) is None
