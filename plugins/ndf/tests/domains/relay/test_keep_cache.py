"""キャッシュの起床の判定（`keep_cache`。#4068・#4199 の詳細設計の「判定」の「単体テストの観点」）。

会話の記録は実測の形から作った見本の行で、時刻は固定の値。実際には待たない。
"""

from __future__ import annotations

import json

import pytest

from ndf.domains.hooks import tables
from ndf.domains.hooks.tables import TableError
from ndf.domains.relay import keep_cache
from ndf.domains.relay.keep_cache import LastTurn, Opening, Ratios, Reply, Step, TurnRead, Usage

T0 = 1_795_000_000.0  # 2026-11-17 頃の UNIX 秒


def stamp(at: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(at, timezone.utc).isoformat().replace("+00:00", "Z")


def use(read=0, w1h=0, w5m=0, inp=1, out=7, total=None, split=True):
    u = {"input_tokens": inp, "cache_read_input_tokens": read, "cache_creation_input_tokens": w1h + w5m if total is None else total, "output_tokens": out}
    if split:
        u["cache_creation"] = {"ephemeral_1h_input_tokens": w1h, "ephemeral_5m_input_tokens": w5m}
    return u


def call(mid, *content, usage=None, at=T0, model="claude-opus-5-5", **kw):
    msg = {"id": mid, "model": model, "content": list(content) or [{"type": "text", "text": "x"}], "usage": usage or use()}
    return json.dumps({"type": "assistant", "timestamp": stamp(at), "message": msg, **kw})


def text(s):
    return {"type": "text", "text": s}


def tool(i):
    return {"type": "tool_use", "id": i, "name": "Bash", "input": {}}


def said(s, at=T0, **kw):
    return json.dumps({"type": "user", "timestamp": stamp(at), "message": {"role": "user", "content": s}, **kw})


def result(i, at=T0):
    return json.dumps({"type": "user", "timestamp": stamp(at), "message": {"content": [{"type": "tool_result", "tool_use_id": i}]}})


BOUNDARY = json.dumps({"type": "system", "subtype": "compact_boundary"})


def tail(*lines):
    return "\n".join(lines) + "\n"


def opus(ttl="1h", read=0.05, d=2000):
    return Ratios(2.0 if ttl == "1h" else 1.25, 2.0, 1.25, read, d, 5, 600, 60, 0.9)


# ---------------------------------------------------------------- last_turn


def test_last_turn_reads_the_current_reply_and_its_ttl():
    rows = tail(said("やって"), call("m1", text("done"), usage=use(read=100, w1h=300, inp=2, out=9), at=T0))
    got = keep_cache.last_turn(rows, "done", T0)
    assert got == TurnRead(LastTurn("m1", T0, 402, "1h", "claude-opus-5-5", 100, 300, 9), None)
    assert keep_cache.last_turn(tail(said("x"), call("m1", text("done"), usage=use(w5m=5))), "done", T0).turn.ttl == "5m"
    assert keep_cache.last_turn(tail(said("x"), call("m1", text("done"), usage=use())), "done", T0).turn.ttl is None


def test_last_turn_uses_only_the_adopted_line():
    rows = tail(said("x"), call("m1", text("a"), usage=use(read=5, w5m=9)), call("m1", text("b"), usage=use(read=50, w1h=30)))
    turn = keep_cache.last_turn(rows, "ab", T0).turn
    assert (turn.tokens, turn.cache_read, turn.cache_write, turn.ttl) == (81, 50, 30, "1h")


def test_last_turn_form_newer_and_unrecorded():
    no_usage = json.loads(call("m1", text("b"), at=T0))
    del no_usage["message"]["usage"]
    rows = tail(said("x"), call("m1", text("a")), json.dumps(no_usage))
    assert keep_cache.last_turn(rows, "ab", T0) == TurnRead(None, "form", ("m1", T0))
    assert keep_cache.last_turn(rows, "ab", T0 - 1) == TurnRead(None, "newer")
    no_time = json.loads(call("m1", text("a")))
    del no_time["timestamp"]
    assert keep_cache.last_turn(tail(said("x"), json.dumps(no_time)), "a", T0) == TurnRead(None, "form", None)
    no_out = use()
    del no_out["output_tokens"]
    assert keep_cache.last_turn(tail(said("x"), call("m1", text("a"), usage=no_out)), "a", T0) == TurnRead(None, "form", ("m1", T0))
    for bad in (use(split=False), {**use(), "cache_creation": {"ephemeral_1h_input_tokens": -1, "ephemeral_5m_input_tokens": 0}}):
        assert keep_cache.last_turn(tail(said("x"), call("m1", text("a"), usage=bad)), "a", T0).reason == "form"
    ok = tail(said("x"), call("m1", text("done")))
    assert keep_cache.last_turn(ok, "done", T0 - 1) == TurnRead(None, "newer")
    assert keep_cache.last_turn(ok, "done", T0).turn is not None
    assert keep_cache.last_turn(tail(call("m0", text("前")), said("次")), "今回", T0) == TurnRead(None, "unrecorded")
    assert keep_cache.last_turn(ok, None, T0) == TurnRead(None, "unrecorded")
    assert keep_cache.last_turn("", "done", T0) == TurnRead(None, "unrecorded")


# ---------------------------------------------------------------- wake_reply・opening（is_wake_input を差し替える）


@pytest.fixture
def wake_head(monkeypatch):
    """起床の文の頭で始まる入力を起床の文と見分ける（有効化の PR で測る形の代わり）。`isMeta` でないほかの入力は通常の入力。"""

    def judge(line):
        content = line["message"].get("content")
        if isinstance(content, str) and content.startswith(keep_cache.WAKE_HEAD):
            return True
        return None if line.get("isMeta") else False

    monkeypatch.setattr(keep_cache, "is_wake_input", judge)


WAKE = keep_cache.WAKE_HEAD + "起床"


def test_wake_reply_sums_every_call_of_the_wake(wake_head):
    rows = tail(
        said("やって"),
        call("m0", text("待つ")),
        said(WAKE),
        call("w1", tool("t1"), usage=use(read=1000, w1h=300)),
        result("t1"),
        call("w2", tool("t2"), usage=use(read=1100, w5m=1000)),
        result("t2"),
        call("w3", text("待機を続ける"), usage=use(read=1200)),
    )
    got = keep_cache.wake_reply(rows, "m0")
    assert got == Reply("wake", 1000, Usage(3, 3300, 21, 300, 1000, True))


def test_wake_reply_first_call_with_no_writes_is_still_a_wake(wake_head):
    rows = tail(said("x"), call("m0"), said(WAKE), call("w1", usage=use(read=500)))
    assert keep_cache.wake_reply(rows, "m0") == Reply("wake", 500, Usage(1, 500, 7, 0, 0, True))


@pytest.mark.parametrize(
    "bad",
    [use(split=False), use(w1h=1, total=5), {**use(), "cache_creation": {"ephemeral_1h_input_tokens": 1.5, "ephemeral_5m_input_tokens": 0}}],
)
def test_wake_reply_unpriced_when_a_split_is_missing_or_wrong(wake_head, bad):
    rows = tail(said("x"), call("m0"), said(WAKE), call("w1", usage=use(read=10)), call("w2", usage=bad))
    got = keep_cache.wake_reply(rows, "m0")
    assert got.kind == "wake" and got.usage.priced is False and got.usage.cache_write_1h is None and got.usage.cache_write_5m is None


def test_wake_reply_stops_at_the_next_input_and_sees_preempted_and_unknown(wake_head, monkeypatch):
    rows = tail(said("x"), call("m0"), said(WAKE), call("w1", usage=use(read=10)), said("次のリクエスト"), call("u1", usage=use(read=99)))
    assert keep_cache.wake_reply(rows, "m0").usage.calls == 1
    pre = tail(said("x"), call("m0"), said("割り込み"), call("u1"), said(WAKE), call("w1"))
    assert keep_cache.wake_reply(pre, "m0") == Reply("preempted", None, None)
    monkeypatch.setattr(keep_cache, "is_wake_input", lambda line: None)
    assert keep_cache.wake_reply(tail(said("x"), call("m0"), said("?"), call("w1")), "m0") == Reply("unknown", None, None)


def test_wake_reply_truncated_when_after_id_or_a_boundary_cuts_the_range(wake_head):
    assert keep_cache.wake_reply(tail(said(WAKE), call("w1")), "m0") == Reply("truncated", None, None)
    kept = tail(said("x"), call("m0"), said(WAKE), call("w1", tool("t"), usage=use(read=1)), result("t"), call("w2", usage=use(read=2)))
    assert keep_cache.wake_reply(kept, "m0").usage.calls == 2
    for cut in (
        tail(said("x"), call("m0"), BOUNDARY, said("要約"), said(WAKE), call("w1")),
        tail(said("x"), call("m0"), said(WAKE), call("w1"), BOUNDARY, said("要約"), call("w2")),
    ):
        assert keep_cache.wake_reply(cut, "m0") == Reply("truncated", None, None)


def test_wake_reply_counts_one_call_per_message_id_and_skips_sidechains_and_synthetic(wake_head):
    rows = tail(
        said("x"),
        call("m0"),
        said(WAKE),
        call("s0", usage=use(read=0), model="<synthetic>"),
        call("w1", {"type": "thinking", "thinking": "."}, usage=use(read=1, w1h=1)),
        call("c1", usage=use(read=777), isSidechain=True),
        call("w1", text("a"), usage=use(read=5, w1h=2)),
        call("w1", tool("t"), usage=use(read=400, w1h=3)),
    )
    got = keep_cache.wake_reply(rows, "m0")
    assert got.first_read == 400 and got.usage == Usage(1, 400, 7, 3, 0, True)


def test_opening_finds_the_input_that_started_the_reply(wake_head):
    rows = tail(
        said("前"),
        call("m0"),
        said("やって", at=T0 + 10),
        said("Skill の本文", at=T0 + 11, isMeta=True),
        call("s0", model="<synthetic>"),
        call("c0", usage=use(read=999), isSidechain=True),
        call("m1", tool("t1"), usage=use(read=10), at=T0 + 12),
        call("m1", tool("t2"), usage=use(read=20_000), at=T0 + 13),
        result("t1"),
        result("t2"),
        call("m2", text("done"), usage=use(read=101_000), at=T0 + 20),
    )
    assert keep_cache.wake_opening(rows, "m2") == Opening("normal", T0 + 10, 20_000)
    assert keep_cache.wake_opening(tail(said(WAKE), call("w1")), "w1") == Opening("wake")
    assert keep_cache.wake_opening(tail(call("m1")), "m1") == Opening("unknown")
    assert keep_cache.wake_opening(tail(said("x"), call("m0")), "zz") == Opening("unknown")


def test_opening_skips_a_pushback_and_stops_at_a_boundary(wake_head):
    pushed = tail(said("元の入力", at=T0 + 5), call("m1"), said("Stop の hook の変更要求", isMeta=True), call("m2"))
    assert keep_cache.wake_opening(pushed, "m2").at == T0 + 5
    assert keep_cache.wake_opening(tail(said("x"), call("m0"), BOUNDARY, said("要約"), call("m1")), "m1") == Opening("unknown")
    after = tail(said("x"), call("m0"), BOUNDARY, said("要約"), said("続けて", at=T0 + 9), call("m1", usage=use(read=3)))
    assert keep_cache.wake_opening(after, "m1") == Opening("normal", T0 + 9, 3)


def test_meta_lines_are_inputs_only_when_is_wake_input_decides(monkeypatch):
    rows = tail(said("x"), call("m0"), said(WAKE, isMeta=True), call("w1", usage=use(read=4)))
    assert keep_cache.wake_reply(rows, "m0") == Reply("unknown", None, None)  # 差し替えない実装は isMeta の行を飛ばす
    monkeypatch.setattr(keep_cache, "is_wake_input", lambda line: line["message"]["content"] == WAKE or None)
    assert keep_cache.wake_reply(rows, "m0").kind == "wake"
    assert keep_cache.wake_opening(rows, "w1") == Opening("wake")
    notice = tail(said("x"), call("m0"), said("作業の知らせ", at=T0 + 3, isMeta=True), call("m1", usage=use(read=8)))
    monkeypatch.setattr(keep_cache, "is_wake_input", lambda line: False if line.get("isMeta") else None)
    assert keep_cache.wake_opening(notice, "m1") == Opening("normal", T0 + 3, 8)


def test_is_wake_input_is_always_none_in_the_implementation_pr():
    assert keep_cache.is_wake_input(json.loads(said(WAKE))) is None


# ---------------------------------------------------------------- ratios・n_max・wake_at・hit・next_step


def _get(over):
    def get(name, key):
        if (name, key) in over:
            return over[(name, key)]
        return tables.policy_value(name, key)

    return get


def test_ratios_reads_the_shipped_policies():
    q = keep_cache.ratios(tables.policy_value, "claude-opus-5-5", "1h")
    assert q == Ratios(2.0, 2.0, 1.25, 0.05, 2000.0, 5.0, 600.0, 60.0, 0.9)
    assert keep_cache.ratios(tables.policy_value, "unknown-model", "5m").r == 0.1
    assert keep_cache.ratios(tables.policy_value, "unknown-model", "5m").w == 1.25


@pytest.mark.parametrize(
    "over",
    [
        {("cache.wake", "interval"): 0},
        {("cache.wake", "interval"): -1},
        {("cache.wake", "interval"): 0.01},
        {("cache.wake", "interval"): 9},
        {("cache.wake", "seconds"): 550, ("cache.wake", "interval"): 10},
        {("cache.wake", "seconds"): -1},
        {("cache.wake", "seconds"): 3600},
        {("cache.read-ratio", "default"): 0},
        {("cache.read-ratio", "ratios"): {"claude-opus-5-5": -0.05}},
        {("cache.read-ratio", "ratios"): {"claude-opus-5-5": 0}, ("cache.wake-cost", "count"): 0},
        {("cache.write-ratio", "ratios"): {"1h": 0, "5m": 1.25}},
        {("cache.wake-cost", "count"): 0},
        {("cache.wake-cost", "count"): -1},
        {("cache.write-ratio", "ratios"): [2.0]},
        {("cache.hit", "ratio"): "0.9"},
    ],
)
def test_ratios_refuses_values_that_break_the_formula_or_the_checks(over):
    model = "claude-opus-5-5" if ("cache.read-ratio", "ratios") in over else "other-model"
    with pytest.raises(TableError):
        keep_cache.ratios(_get(over), model, "1h")


@pytest.mark.parametrize("interval", [10, 60])
def test_ratios_accepts_the_edge_of_the_check_count(interval):
    assert keep_cache.ratios(_get({("cache.wake", "interval"): interval}), "x", "1h").interval == interval


def test_n_max():
    assert [keep_cache.n_max(s, opus()) for s in (400_000, 500_000, 20_000)] == [35, 36, 13]
    assert keep_cache.n_max(400_000, opus(read=0.1)) == 18
    assert keep_cache.n_max(10, Ratios(0.05, 2, 1.25, 0.1, 2000, 5, 600, 60, 0.9)) == 0


def test_wake_at():
    assert keep_cache.wake_at(T0, "1h", 600) == T0 + 3000
    assert keep_cache.wake_at(T0, "5m", 600) is None
    assert keep_cache.wake_at(T0, "5m", 240) is None and keep_cache.wake_at(T0, "1h", 240) == T0 + 3360


def test_hit():
    assert keep_cache.cache_hit(90, 100, 0.9) and not keep_cache.cache_hit(89, 100, 0.9)


def test_next_step_count_and_cost():
    q = opus()
    assert keep_cache.wake_step(32, 33, 0, 250_000, q) == Step("wake")
    assert keep_cache.wake_step(33, 33, 0, 250_000, q) == Step("stop", "count")
    assert keep_cache.wake_step(0, 0, 0, 250_000, q).kind == "stop"
    assert keep_cache.wake_step(35, 36, 953_925, 510_500, q) == Step("wake")
    assert keep_cache.wake_step(36, 36, 981_450, 510_800, q).kind == "stop"
    cheaper = opus(read=0.1)
    assert keep_cache.wake_step(17, 36, 859_000, 500_000, cheaper) == Step("wake")
    assert keep_cache.wake_step(18, 36, 911_000, 500_000, cheaper) == Step("stop", "cost")
    assert keep_cache.wake_step(20, 33, 280_000, 150_000, q) == Step("wake")
    assert keep_cache.wake_step(20, 33, 290_000, 150_000, q) == Step("stop", "cost")
