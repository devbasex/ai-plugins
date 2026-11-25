"""キャッシュの起床の判定（`keep_cache`。#4068・#4199 の詳細設計の「判定」の「単体テストの観点」）。

会話の記録は実測の形から作った見本の行で、時刻は固定の値。実際には待たない。
"""

from __future__ import annotations

import json

import pytest

from ndf import identity
from ndf.contracts import causes
from ndf.domains.hooks import tables
from ndf.domains.hooks.tables import TableError
from ndf.domains.relay import cli, cli_cache, keep_cache, registry
from ndf.domains.relay.keep_cache import LastTurn, Opening, Ratios, Reply, Step, TurnRead, Usage

T0 = 1_795_000_000.0  # 2026-11-17 頃の UNIX 秒


def stamp(at: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(at, timezone.utc).isoformat().replace("+00:00", "Z")


def use(read=0, w1h=0, w5m=0, inp=1, out=7, total=None, split=True):
    u = {
        "input_tokens": inp,
        "cache_read_input_tokens": read,
        "cache_creation_input_tokens": w1h + w5m if total is None else total,
        "output_tokens": out,
    }
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
    [
        use(split=False),
        use(w1h=1, total=5),
        {**use(), "cache_creation": {"ephemeral_1h_input_tokens": 1.5, "ephemeral_5m_input_tokens": 0}},
        {k: v for k, v in use().items() if k != "output_tokens"},
        {**use(), "output_tokens": "7"},
        {**use(), "cache_read_input_tokens": -1},
        {**use(), "cache_creation_input_tokens": None},
        {**use(), "cache_creation_input_tokens": "0"},
    ],
)
def test_wake_reply_unpriced_when_a_split_is_missing_or_wrong(wake_head, bad):
    rows = tail(said("x"), call("m0"), said(WAKE), call("w1", usage=use(read=10)), call("w2", usage=bad))
    got = keep_cache.wake_reply(rows, "m0")
    assert got.kind == "wake" and got.usage.priced is False and got.usage.cache_write_1h is None and got.usage.cache_write_5m is None


def test_wake_reply_missing_read_counts_as_zero_and_stays_priced(wake_head):
    # 読み出しの量の項目が無い呼び出しは 0 として数え、費用は確定のまま（会話の記録の読み取りと同じ扱い）
    no_read = {k: v for k, v in use(w1h=5).items() if k != "cache_read_input_tokens"}
    rows = tail(said("x"), call("m0"), said(WAKE), call("w1", usage=use(read=10)), call("w2", usage=no_read))
    assert keep_cache.wake_reply(rows, "m0").usage == Usage(2, 10, 14, 5, 0, True)


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


# 起床 1 回 = r × S + d、書き直し 1 回 = (w − r) × S。S = 1000 で起床 1250・書き直し 1750（2 進で割り切れる値）。
EXACT = Ratios(2.0, 2.0, 1.25, 0.25, 1000, 5, 600, 60, 0.9)


def test_wake_step_wakes_when_the_total_reaches_the_rewrite_cost_exactly():
    assert keep_cache.wake_step(0, 9, 500, 1000, EXACT) == Step("wake")
    assert keep_cache.wake_step(0, 9, 500.5, 1000, EXACT) == Step("stop", "cost")


def test_wake_step_checks_the_count_before_the_cost():
    assert keep_cache.wake_step(9, 9, 10**9, 1000, EXACT) == Step("stop", "count")


def test_wake_step_cost_uses_the_tokens_and_d_of_the_next_wake():
    q = EXACT._replace(d=1750)
    assert keep_cache.wake_step(0, 9, 0, 1000, q) == Step("stop", "cost")
    assert keep_cache.wake_step(0, 9, 0, 1000, q._replace(d=1500)) == Step("wake")
    assert keep_cache.wake_step(0, 9, 0, 1000, EXACT._replace(w=1.5)) == Step("wake")
    assert keep_cache.wake_step(0, 9, 0, 1000, EXACT._replace(w=1.25)) == Step("stop", "cost")


def test_n_max_counts_wakes_whose_total_stays_within_one_rewrite():
    assert keep_cache.n_max(1000, EXACT) == 1
    assert keep_cache.n_max(1000, EXACT._replace(d=250)) == 3
    assert keep_cache.n_max(1000, EXACT._replace(d=1751)) == 0
    assert keep_cache.n_max(1000, EXACT._replace(w=0.1)) == 0


@pytest.mark.parametrize("tokens", [20_000, 150_000, 400_000, 510_500])
@pytest.mark.parametrize("q", [opus(), opus(read=0.1), opus(ttl="5m"), opus(d=50_000)])
def test_wake_step_stops_by_cost_right_after_n_max_wakes(tokens, q):
    limit = keep_cache.n_max(tokens, q)
    k, spent = 0, 0.0
    while keep_cache.wake_step(k, 10**6, spent, tokens, q) == Step("wake"):
        k, spent = k + 1, spent + q.r * tokens + q.d
    assert k == limit
    assert keep_cache.wake_step(k, 10**6, spent, tokens, q) == Step("stop", "cost")


# ---------------------------------------------------------------- 起床の状態と記録（signals）

from ndf.domains.relay import signals  # noqa: E402
from ndf.domains.relay.signals import Generation  # noqa: E402

SID = "0f0e0d0c-0b0a-4908-8706-050403020100"
LAUNCH = "a" * 32


@pytest.fixture
def relay_dir(tmp_path):
    signals.set_launch(tmp_path, LAUNCH, rewake=True, claude_version="2.1.330")
    return tmp_path


def _log(directory) -> list:
    path = directory / signals.CACHE_LOG
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_launch_record_holds_rewake_and_version(relay_dir):
    assert signals.rewake_on(relay_dir, LAUNCH) and not signals.rewake_on(relay_dir, "b" * 32)
    assert signals.launch_version(relay_dir) == "2.1.330" and signals.current_launch(relay_dir) == LAUNCH
    signals.set_launch(relay_dir, LAUNCH)
    assert not signals.rewake_on(relay_dir, LAUNCH) and signals.launch_version(relay_dir) is None


def test_keep_path_needs_a_uuid_session(relay_dir):
    assert signals.keep_path(relay_dir, SID) == relay_dir / f"keep-{SID}.json"
    for bad in ("s1", "../x", None, SID.upper()):
        assert signals.keep_path(relay_dir, bad) is None and signals.read_keep(relay_dir, bad) is None
    with pytest.raises(signals.SignalError):
        signals.update_keep(relay_dir, "s1", {})


def test_bump_generation_takes_only_newer_times(relay_dir):
    assert signals.bump_generation(relay_dir, LAUNCH, SID, Generation(T0, "turn"))[0] == "taken"
    assert signals.read_keep(relay_dir, SID) == {"generation": {"at": T0, "by": "turn", "message_id": None, "sleeps": False}}
    assert signals.bump_generation(relay_dir, LAUNCH, SID, Generation(T0, "h1", "m1"))[0] == "older"
    assert signals.bump_generation(relay_dir, LAUNCH, SID, Generation(T0 - 1, "h1", "m1"))[0] == "older"
    assert _log(relay_dir) == []
    got, keep = signals.bump_generation(relay_dir, LAUNCH, SID, Generation(T0 + 1, "h1", "m1"), works=1)
    assert got == "taken" and keep["generation"]["by"] == "h1"
    rows = _log(relay_dir)
    assert [r["kind"] for r in rows] == ["work_stop"] and rows[0]["message_id"] == "m1"
    assert {k: rows[0][k] for k in ("launch", "session_id", "claude_version", "wait_id")} == {
        "launch": LAUNCH,
        "session_id": SID,
        "claude_version": "2.1.330",
        "wait_id": None,
    }


def test_contested_reply_voids_a_wait_that_has_not_woken(relay_dir):
    signals.bump_generation(relay_dir, LAUNCH, SID, Generation(T0, "h1", "m1"))
    keep = signals.read_keep(relay_dir, SID)
    signals.update_keep(relay_dir, SID, {**keep, "wait_id": T0, "k": 0, "n_max": 33, "spent": 0, "ttl": "1h", "last": {"x": 1}})
    got, keep = signals.bump_generation(relay_dir, LAUNCH, SID, Generation(T0, "h2", "m1"), works=0)
    assert got == "contested" and keep["generation"] == {"at": T0, "by": "contested", "message_id": "m1", "sleeps": False}
    assert "wait_id" not in keep and keep["ttl"] == "1h" and keep["last"] == {"x": 1}
    assert [(r["kind"], r.get("reason")) for r in _log(relay_dir)] == [("wait_void", "contested"), ("contest", None)]
    # 3 件目の Stop: 世代と待ちはそのまま（取り消しは 1 回だけ）で、集計のために contest の行だけを書く
    got, after = signals.bump_generation(relay_dir, LAUNCH, SID, Generation(T0 + 5, "h3", "m1"), works=2)
    assert got == "contested" and after == keep and signals.read_keep(relay_dir, SID) == keep
    rows = _log(relay_dir)
    assert [r["kind"] for r in rows] == ["wait_void", "contest", "contest"] and rows[2]["works"] == 2


@pytest.mark.parametrize("change", [{"k": 1}, {"pending": {"wake_id": "w"}}, {"wait_id": T0 - 9}])
def test_contested_keeps_a_wait_that_already_woke_or_started_elsewhere(relay_dir, change):
    signals.bump_generation(relay_dir, LAUNCH, SID, Generation(T0, "h1", "m1"))
    keep = {**signals.read_keep(relay_dir, SID), "wait_id": T0, "k": 0, "n_max": 33, "spent": 0, **change}
    signals.update_keep(relay_dir, SID, keep)
    _, after = signals.bump_generation(relay_dir, LAUNCH, SID, Generation(T0, "h2", "m1"))
    assert after["wait_id"] == keep["wait_id"] and [r["kind"] for r in _log(relay_dir)] == ["contest"]


def test_revoke_generation_only_for_a_live_reservation():
    sleeping = {"generation": Generation(T0, "h1", "m1", True)._asdict()}
    assert not signals.revoke_generation(dict(sleeping), T0)
    keep = dict(sleeping)
    assert signals.revoke_generation(keep, T0 + 1) and keep["generation"] == {"at": T0, "by": "arrival", "message_id": "m1", "sleeps": True}
    for by, sleeps in (("h1", False), ("turn", False), ("contested", True), ("arrival", True)):
        assert not signals.revoke_generation({"generation": Generation(T0, by, "m1", sleeps)._asdict()}, T0 + 1)
    assert not signals.revoke_generation({}, T0 + 1)


# --- 集計（`ndf relay cache report`。詳細設計の「集計」、設計書の「テスト設計」の 12・11・8 の行） ---

W1H, W5M, RD, OUT = 2.0, 1.25, 0.1, 5.0


def crow(kind, s, wait=T0, sid=SID, ver="2.1.330", **kw):
    """起床の記録の 1 行（`at` は `T0 + s` 秒の時刻の文字列、`wait_id` は待ちの始まりの応答の時刻）。"""
    return {"at": stamp(T0 + s), "kind": kind, "launch": LAUNCH, "session_id": sid, "claude_version": ver, "wait_id": wait, **kw}


def start(wait=T0, ttl="1h", tokens=100_000, **kw):
    return crow("wait_start", wait - T0, wait, tokens=tokens, ttl=ttl, model="claude-opus-5-5", n_max=9, **kw)


def wake(wid, s, wait=T0, **kw):
    return crow("wake", s, wait, wake_id=wid, tokens=100_000, age=s - (wait - T0), k=1, n_max=9, spent=1, **kw)


def woke(wid, s, wait=T0, hit=True, read=100_000, w1h=0, w5m=0, out=10, priced=True, outcome="woke", ratios=None):
    ratio = {"w_1h": W1H, "w_5m": W5M, "r": RD, "out": OUT, **(ratios or {})}
    row = crow("wake_result", s, wait, wake_id=wid, outcome=outcome, hit=hit, tokens=100_000, first_read=read, calls=1)
    row.update(cache_read=read, output=out, priced=priced, model="claude-opus-5-5", ttl="1h", **ratio)
    if priced:
        row.update(cache_write_1h=w1h, cache_write_5m=w5m)
    return row


def end(opened, wait=T0, hit=True, tokens=100_000, ttl="1h", w=W1H, r=RD):
    """待ちを終えた入力が待ちの始まりから `opened` 秒（Stop はその 60 秒後）。"""
    return crow(
        "wait_end",
        wait - T0 + opened + 60,
        wait,
        opened_at=wait + opened,
        age=1,
        k=1,
        hit=hit,
        cache_read=1,
        tokens=tokens,
        ttl=ttl,
        model="m",
        w=w,
        r=r,
    )


def test_report_counts_wakes_by_distinct_wake_id_including_unmatched_and_orphans():
    rows = [start(), wake("a", 3000), woke("a", 3100), wake("b", 3200), woke("c", 3300)]
    got = cli_cache.report(rows)
    assert (got["waits"], got["wakes"], got["unmatched_wakes"], got["orphan_results"]) == (1, 3, 1, 1)
    # `wake_result` の欠けた `wake` を持つ待ちは費用が分からない
    assert got["unpriced_waits"] == 1


def test_report_prices_wakes_with_the_ratios_written_on_each_row():
    rows = [
        start(),
        wake("a", 1700),
        woke("a", 1710, read=100_000, w1h=1_000, w5m=400, out=20),
        wake("b", 3200),
        woke("b", 3210, read=100_000, w1h=3_000, w5m=0, out=40, ratios={"w_1h": 4.0, "r": 0.5}),
        wake("c", 3300),
        woke("c", 3310, hit=False, read=20_000, w1h=80_000, out=10),
    ]
    got = cli_cache.report(rows)
    # d は寿命別の書き込み × 倍率 + 出力 × out（行に書いた倍率。b は書いた時点の 4.0 と 0.5 で出す）
    ds = sorted([1_000 * W1H + 400 * W5M + 20 * OUT, 3_000 * 4.0 + 40 * OUT, 80_000 * W1H + 10 * OUT])
    assert got["d_median"] == ds[1]
    costs = sorted([100_000 * RD + ds[0], 100_000 * 0.5 + 3_000 * 4.0 + 40 * OUT, 20_000 * RD + 80_000 * W1H + 10 * OUT])
    assert got["wake_cost_median"] == costs[1]
    ages = got["hit_rate_by_age"]
    assert ages["30m"] == {"wakes": 1, "hits": 1, "rate": 1.0}
    assert ages["55m"] == {"wakes": 2, "hits": 1, "rate": 0.5}
    assert ages["45m"]["rate"] is None and ages["60m+"]["wakes"] == 0


def test_report_leaves_unpriced_wakes_out_of_d_and_cost_and_counts_the_wait():
    rows = [start(), wake("a", 3000), woke("a", 3010, priced=False), end(6500)]
    got = cli_cache.report(rows)
    assert got["d_median"] is None and got["wake_cost_median"] is None
    assert got["unpriced_waits"] == 1 and got["estimated_avoided_rewrites"] == 0


def _avoided(*rows):
    return cli_cache.report([start(), *rows])


def test_report_estimates_one_avoided_rewrite_per_wait_after_the_wake_cost():
    got = _avoided(wake("a", 3000), woke("a", 3010, out=10), wake("b", 5000), woke("b", 5010, out=10), end(6500))
    assert got["estimated_avoided_rewrites"] == 1
    assert got["estimated_avoided_tokens"] == 100_000
    assert got["estimated_avoided_cost"] == pytest.approx((W1H - RD) * 100_000 - 2 * (100_000 * RD + 10 * OUT))


@pytest.mark.parametrize(
    "rows",
    [
        # 最後に当たった起床から寿命を越えて当たった（3,000 秒の起床、7,000 秒の入力）
        [wake("a", 3000), woke("a", 3010), end(7000)],
        # 待ちを終えた最初の呼び出しが外れた（後の Tool の呼び出しが当たっても数えない）
        [wake("a", 3000), woke("a", 3010), end(6500, hit=False)],
        # 待ちの長さは入力の時刻まで（3,590 秒。Stop は 3,650 秒）で寿命を越えない
        [wake("a", 1500), woke("a", 1510), end(3590)],
        # 起床 0 回で寿命を越えて自然に当たった
        [end(6500)],
        # 当たった起床が無い
        [wake("a", 3000), woke("a", 3010, hit=False), end(6500)],
        # `wake` と結べない `wake_result` の当たりは起床に帰せない
        [woke("a", 3010), end(6500)],
    ],
)
def test_report_does_not_count_rewrites_it_cannot_lay_to_a_wake(rows):
    assert _avoided(*rows)["estimated_avoided_rewrites"] == 0


def test_report_counts_preempted_wakes_at_no_cost_and_keeps_the_wait_priced():
    got = _avoided(
        wake("a", 3000), woke("a", 3010, out=10), wake("b", 5000), woke("b", 5010, outcome="preempted", hit=None, read=0, out=0), end(6500)
    )
    assert (got["preempted_wakes"], got["unpriced_waits"], got["estimated_avoided_rewrites"]) == (1, 0, 1)
    assert got["estimated_avoided_cost"] == pytest.approx((W1H - RD) * 100_000 - (100_000 * RD + 10 * OUT))
    # 割り込みの起床は当たりの率と d に入らない
    assert sum(b["wakes"] for b in got["hit_rate_by_age"].values()) == 1


def test_report_measures_wait_lengths_to_the_opening_input():
    rows = []
    for i, opened in enumerate((1000, 2000, 3000, 4000, 5000)):
        wait = T0 + i * 100_000
        rows += [start(wait), end(opened, wait)]
    got = cli_cache.report(rows)
    assert got["wait_seconds"] == {"p50": 3000.0, "p75": 4000.0, "p90": 4600.0}


def test_report_since_drops_waits_started_before_and_voided_waits():
    later = T0 + 10_000
    rows = [
        start(),
        wake("a", 3000),
        start(later),
        wake("b", 13_000, later),
        woke("b", 13_010, later),
        woke("x", 20_000, T0 + 7),  # `wait_start` の無い待ち
        start(T0 + 50_000),
        crow("wait_void", 50_001, T0 + 50_000, reason="contested"),
    ]
    got = cli_cache.report(rows, since=T0 + 5_000)
    assert (got["waits"], got["wakes"], got["partial_waits"], got["voided_waits"]) == (1, 1, 2, 1)


def test_report_counts_waits_stopped_by_the_limit_and_by_unmatched_replies():
    later = T0 + 10_000
    rows = [
        start(),
        crow("stop_waking", 3000, reason="limit", scope="wait", k=9, n_max=9),
        start(later),
        wake("b", 13_000, later),
        woke("b", 13_010, later, outcome="unknown", hit=None, priced=False),
        crow("stop_waking", 13_020, later, reason="unmatched", scope="wait", k=1, n_max=9),
        start(T0 + 20_000),
        crow("stop_waking", 20_100, T0 + 20_000, reason="miss", scope="session", k=1, n_max=9),
    ]
    got = cli_cache.report(rows)
    assert (got["waits"], got["limit_stopped_waits"], got["unmatched_stopped_waits"]) == (3, 1, 1)


def _stops(ver, kind, n, mid=None, **kw):
    return [crow(kind, 0, None, ver=ver, message_id=mid or f"{ver}-{kind}-{i}", **kw) for i in range(n)]


def test_report_lost_ratio_by_version_follows_the_example_in_the_design():
    v0, v2 = "2.1.330", "2.1.332"
    rows = [
        *_stops(v0, "work_stop", 6),
        *_stops(v0, "work_stop", 1, "m1"),
        *_stops(v0, "work_stop", 1, "m2"),
        *_stops(v0, "contest", 1, "m1", works=1),
        *_stops(v0, "contest", 1, "m2", works=0),
        *_stops(v0, "contest", 1, "m3", works=0),
        *_stops(v2, "work_stop", 8),
        *_stops(v2, "work_stop", 1, "m4"),
        *_stops(v2, "work_stop", 1, "m5"),
        *_stops(v2, "contest", 1, "m4", works=1),
        *_stops(v2, "arrival", 1, "m5", works=1, revoked=True, voided=False),
        *_stops(v2, "arrival", 1, "m4", works=0, revoked=False, voided=False),
    ]
    got = cli_cache.report(rows)["by_version"]
    assert got[v0] == {
        "contests": 3,
        "arrivals": 0,
        "work_stops": 9,
        "lost_work_stops": 3,
        "contested_work_stops": 3,
        "arrival_work_stops": 0,
        "lost_ratio": pytest.approx(3 / 9),
    }
    assert got[v2] == {
        "contests": 1,
        "arrivals": 2,
        "work_stops": 12,
        "lost_work_stops": 4,
        "contested_work_stops": 2,
        "arrival_work_stops": 2,
        "lost_ratio": pytest.approx(4 / 12),
    }


def test_report_by_version_names_unknown_versions_and_applies_since_to_each_row():
    rows = [crow("work_stop", 0, None, ver=None, message_id="m1"), crow("work_stop", 9_000, None, ver=None, message_id="m2")]
    assert cli_cache.report(rows)["by_version"]["unknown"]["work_stops"] == 2
    assert cli_cache.report(rows, since=T0 + 5_000)["by_version"]["unknown"]["lost_ratio"] == 0.0


def _write_log(directory, rows, extra=()):
    directory.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r) for r in rows] + list(extra)
    (directory / signals.CACHE_LOG).write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_cli_cache_report_reads_every_wrapper_log_and_counts_broken_lines(capsys):
    root = registry.wrappers_dir(identity.load().state_dir)
    _write_log(root / "w1", [start(), wake("a", 3000), woke("a", 3010)], ["{broken", "[1]", json.dumps({"kind": "wake", "at": "x"})])
    _write_log(root / "w2", [*_stops("2.1.330", "work_stop", 2)])
    assert cli.main(["cache", "report"]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["status"] == "ok" and "推定" in out["summary"]
    m = out["metrics"]
    assert (m["waits"], m["wakes"], m["skipped"], m["logs"]) == (1, 1, 3, 2)
    assert m["by_version"]["2.1.330"]["work_stops"] == 2
    assert cli.main(["cache", "report", "--since", stamp(T0 + 5_000)]) == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["metrics"]["partial_waits"] == 1


def test_cli_cache_report_refuses_a_bad_since(capsys):
    assert cli.main(["cache", "report", "--since", "昨日"]) == causes.exit_code("invalid")
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["status"] == "stopped"
