"""内部データの読み書きの核（`ndf.contracts.internal`）: バージョンの見分け・移行・型の確かめ・時刻の書式。#2339 の受け入れ条件 1・3・5。"""

from __future__ import annotations

import json

import pytest
from jsonschema import Draft202012Validator

from ndf import contracts
from ndf.contracts import internal
from ndf.domains.records import usage, usage_decide
from ndf.domains.records import mvv_decide as md
from ndf.domains.relay import context_records as cr
from ndf.domains.relay.context_thresholds import Thresholds

AT = "2026-11-23T01:00:00+00:00"
TOKENS = usage_decide.Usage(1, 2, 3, 4).as_dict()
APPLIED = Thresholds(250_000, 500_000, {"decide_from": "default", "limit": "default"})
USAGE = {"at": AT, "caller": usage.SUBAGENT, "agent_id": "a", "transcript": "/t.jsonl", "usage": TOKENS}
SIGNAL = {"at": AT, "kind": md.OVERRIDE_PASS, "repo": "/m", "state": "s", "gate": "g", "pr": 1}
VALIDATION = md.judged_row("k", md.FOLLOW, AT, "/m", {"usd": None, "reasons": ["r"], "boundary": [], "basis": ["Value 1"]})
DECISION = {**cr.record_row("s1", "#1", APPLIED, cr.MARK_HANDOFF, None), "at": AT}
ANSWERED = {"kind": "gate-answered", "lap": 2, "pr": 4301, "answer": "approved", "head": "abc", "note": ""}
LEASE = {"kind": "lease-taken", "session": "s1", "until": 1795400600.0}
# 旧いプラグインの hook が書いた行（バージョン 0）
OLD_DECISION = {
    **DECISION,
    "schema": 1,
    "at": "2026-11-08T03:25:53Z",
    "source": "hook",
    "event": "判定",
    "stage": "plan",
    "context": 120_000,
    "effective_limit": 250_000,
    "band": "継続帯",
    "verdict": "継続",
    "basis": "判断開始の値以下",
    "model": "claude-x",
    "transcript": "/t.jsonl",
    "policy": "current",
    "margin": 50_000,
    "config_error": None,
}
OLD_COLUMNS = ("schema", "policy", "margin", "config_error")


def kind_of(exc: pytest.ExceptionInfo[internal.InternalError]) -> str:
    return exc.value.kind


# ---------------------------------------------------------------- 移行と読み取り


@pytest.mark.parametrize(
    ("kind", "old", "new"),
    [
        ("usage", USAGE, USAGE),
        ("usage", {**USAGE, "usage": usage_decide.UNKNOWN}, {**USAGE, "usage": usage_decide.UNKNOWN}),
        # バージョン 0 の兆候の `version` は MVV の設定のバージョン（整数）で、形のバージョンではない
        ("mvv-signals", {**SIGNAL, "version": 1}, {**SIGNAL, "mvv_version": 1}),
        ("mvv-signals", {**SIGNAL, "version": 7}, {**SIGNAL, "mvv_version": 7}),
        ("mvv-signals", {**SIGNAL, "version": None}, {**SIGNAL, "mvv_version": None}),
        ("mvv-signals", SIGNAL, {**SIGNAL, "mvv_version": None}),
        ("mvv-validation", VALIDATION, VALIDATION),
        (
            "mvv-validation",
            {"ts" if k == "at" else k: v for k, v in VALIDATION.items()},
            VALIDATION,
        ),
        ("context-records", OLD_DECISION, {k: v for k, v in OLD_DECISION.items() if k not in OLD_COLUMNS}),
        ("context-records", {k: v for k, v in DECISION.items() if k != "event"}, {**DECISION, "event": cr.EVENT_DECISION}),
        ("loop-event", {"at": 1795400000.123456, **ANSWERED}, {"at": "2026-11-23T02:13:20.123456+00:00", **ANSWERED}),
        ("loop-event", {"at": 1795400000, **LEASE}, {"at": "2026-11-23T02:13:20.000000+00:00", **LEASE}),
        # バージョン 0 の出荷のイベントの `version` は製品のバージョン（文字列）で、形のバージョンではない
        (
            "loop-event",
            {"at": 1795400000.0, "kind": "version-shipped", "lap": 2, "version": "11.0.23"},
            {"at": "2026-11-23T02:13:20.000000+00:00", "kind": "version-shipped", "lap": 2, "product_version": "11.0.23"},
        ),
    ],
)
def test_read_moves_a_version_zero_row_to_version_one(kind, old, new):
    assert internal.read_record(kind, old) == {**new, "version": 1}


@pytest.mark.parametrize(
    ("kind", "row"),
    [
        ("usage", USAGE),
        ("usage", {"at": AT, "caller": usage.CLAUDE_CLI, "launch_id": "l", "run_id": "r", "step": "s", "attempt": 1, "launched_at": AT,
                   "repo": "o/r", "session_id": "x", "model_usage": {"m": TOKENS}, "usage": TOKENS}),
        ("mvv-signals", {**SIGNAL, "mvv_version": 3}),
        ("mvv-validation", VALIDATION),
        ("mvv-validation", md.reuse_row(VALIDATION, AT, "/m")),
        ("context-records", DECISION),
        ("context-records", cr.record_row(None, "", APPLIED, None, "手動")),
    ],
)  # fmt: skip
def test_a_row_of_each_writer_is_written_and_read_back_unchanged(kind, row):
    written = internal.write_record(kind, row)
    assert written == {**row, "version": 1}
    assert internal.read_record(kind, json.loads(json.dumps(written, ensure_ascii=False))) == written


@pytest.mark.parametrize("version", [2, 99])
def test_read_stops_on_a_newer_version(version):
    with pytest.raises(internal.InternalError) as exc:
        internal.read_record("usage", {**USAGE, "version": version})
    assert kind_of(exc) == "precondition" and "プラグインを更新する" in str(exc.value)
    with pytest.raises(internal.InternalError):
        internal.read_record("mvv-signals", {**SIGNAL, "mvv_version": 1, "version": version})


@pytest.mark.parametrize("fn", [internal.read_record, internal.write_record, internal.migrate, internal.readable_version])
def test_an_unknown_kind_is_config_broken(fn):
    with pytest.raises(internal.InternalError) as exc:
        fn("usege", USAGE)
    assert kind_of(exc) == "config_broken"


@pytest.mark.parametrize("version", [0, -1])
def test_a_version_below_one_is_a_broken_row(version):
    with pytest.raises(internal.InternalError) as exc:
        internal.read_record("usage", {**USAGE, "version": version})
    assert kind_of(exc) == "invalid"


def test_migrate_runs_the_steps_from_the_version_of_the_row(contracts_copy, monkeypatch):
    def rename(row):
        return {"new" if k == "old" else k: v for k, v in row.items()}

    schema = {"type": "object", "additionalProperties": False, "properties": {"version": {"enum": [2]}, "new": {"type": "string"}}}
    contracts_copy("schemas/internal/sample.schema.json", lambda _: schema)
    monkeypatch.setattr(internal, "version", lambda: 2)
    monkeypatch.setitem(internal.KINDS, "sample", internal.Kind(contracts.schema_path("internal/sample"), (dict, rename)))
    assert internal.read_record("sample", {"version": 1, "old": "x"}) == {"version": 2, "new": "x"}
    assert internal.read_record("sample", {"old": "x"}) == {"version": 2, "new": "x"}
    assert internal.migrate("sample", {"version": 1}) == {"version": 2}
    assert internal.write_record("sample", {"new": "x"}) == {"version": 2, "new": "x"}  # 時刻の欄を持たない種類に at を足さない
    with pytest.raises(internal.InternalError) as exc:
        internal.migrate("sample", {"version": 3})
    assert kind_of(exc) == "precondition"


@pytest.mark.parametrize("line", ["{not json", "[1]", "1", "null"])
def test_read_line_refuses_a_line_that_is_not_an_object(line):
    with pytest.raises(internal.InternalError) as exc:
        internal.read_line("usage", line)
    assert kind_of(exc) == "invalid"


# ---------------------------------------------------------------- 型の確かめ


@pytest.mark.parametrize(
    "row",
    [
        {**USAGE, "extra": 1},
        {**USAGE, "agent_id": 1},
        {**USAGE, "usage": {"input_tokens": 1}},
        {k: v for k, v in USAGE.items() if k != "caller"},
        {**USAGE, "version": "11.0.22"},
        {**USAGE, "at": "2026-11-23T01:00:00"},
        {**USAGE, "at": "2026-11-23T10:00:00+09:00"},
        {**USAGE, "at": "2026-02-30T00:00:00Z"},
        {**USAGE, "at": "2026-13-01T00:00:00Z"},
        {**USAGE, "launched_at": "2026-02-30T00:00:00Z"},
        {**USAGE, "at": 1795400000.0},
    ],
)
def test_write_refuses_a_row_that_does_not_fit(row):
    with pytest.raises(internal.InternalError) as exc:
        internal.write_record("usage", row)
    assert kind_of(exc) == "invalid"


@pytest.mark.parametrize(
    ("change", "where"),
    [
        ({"agent_id": ["sk-test-secret"]}, "properties/agent_id/type"),
        ({"usage": "sk-test-secret"}, "properties/usage/oneOf"),
        ({"at": "sk-test-secret"}, "properties/at/format"),
        ({"sk-test-secret": 1}, "additionalProperties"),
        ({"model_usage": {"sk-test-secret": 1}}, "properties/model_usage/additionalProperties/type"),
        ({"model_usage": {"m": {"sk-test-secret": -1}}}, "properties/model_usage/additionalProperties/additionalProperties/minimum"),
    ],
)
def test_the_refusal_shows_the_place_in_the_schema_and_not_the_row(change, where):
    with pytest.raises(internal.InternalError) as exc:
        internal.write_record("usage", {**USAGE, **change})
    assert "sk-test-secret" not in str(exc.value) and f"{where} に合わない" in str(exc.value)


@pytest.mark.parametrize("at", [AT, "2026-11-23T01:00:00Z", "2026-11-23T01:00:00.123456+00:00"])
def test_write_keeps_the_time_of_the_caller(at):
    assert internal.write_record("usage", {**USAGE, "at": at})["at"] == at


def test_write_fills_the_time_when_the_caller_gives_none(monkeypatch):
    monkeypatch.setattr(internal.time, "time", lambda: 1795400000.5)
    got = internal.write_record("usage", {k: v for k, v in USAGE.items() if k != "at"})
    assert got["at"] == internal.stamp(1795400000.5) and got["version"] == 1


@pytest.mark.parametrize("path", sorted((contracts.DEFAULT_DIR / "schemas" / "internal").glob("*.json")), ids=lambda p: p.name)
def test_the_schemas_are_schemas(path):
    Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))


# ---------------------------------------------------------------- 秒と時刻の文字列


@pytest.mark.parametrize("seconds", [1795400000.0, 1795400000.123456, 0.0])
def test_epoch_takes_back_the_stamp_to_the_microsecond(seconds):
    assert internal.epoch(internal.stamp(seconds)) == seconds


def test_stamp_keeps_the_order_within_a_second():
    assert internal.stamp(1795400000.1) < internal.stamp(1795400000.100001)
    assert internal.stamp(1795400000.0).endswith(".000000+00:00")


@pytest.mark.parametrize("seconds", [float("nan"), float("inf"), 1e300])
def test_stamp_refuses_seconds_that_are_not_a_time(seconds):
    with pytest.raises(internal.InternalError) as exc:
        internal.stamp(seconds)
    assert kind_of(exc) == "invalid"


@pytest.mark.parametrize("at", ["2026-11-23T10:00:00+09:00", "2026-11-23T01:00:00", "2026-02-30T00:00:00Z", "読めない"])
def test_epoch_refuses_a_time_that_is_not_utc(at):
    with pytest.raises(internal.InternalError) as exc:
        internal.epoch(at)
    assert kind_of(exc) == "invalid"
