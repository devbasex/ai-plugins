"""作業ごとの集計・判定の記録・見込みの較正（`ndf relay context report`・`record`・`calibrate`。#3569）。

記録の語（「継続」「再起動」）は記録に残ったデータの値なので、字面で書く（#3995 の Task 1）。
"""

from __future__ import annotations

import json

import pytest

from ndf import identity
from ndf.contracts.internal import InternalError
from ndf.domains.relay import cli, context_records, context_report, registry
from ndf.domains.relay.context_thresholds import Thresholds

CONTINUE = "継続"
# record_row に渡す 2 つの値（#3995 の決定 1）
APPLIED = Thresholds(450_000, 600_000, {"decide_from": "default", "limit": "default"})
# 方針を消す前の行だけが持つ列（#3995 の決定 1）
POLICY_COLUMNS = ("policy", "margin", "config_error")


def _call(i: int, ts: str, inp: int = 100, out: int = 10, mid: str | None = None) -> dict:
    usage = {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": 0}
    return {"type": "assistant", "timestamp": ts, "message": {"id": mid if mid else f"m{i}", "model": "claude-x", "usage": usage}}


def _transcript(path, entries) -> str:
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return str(path)


def test_buckets_split_by_handoff_mark_and_dedupe_message_ids(tmp_path):
    t = _transcript(
        tmp_path / "t.jsonl",
        [
            {"type": "user", "message": {"content": "始める"}},
            _call(0, "2026-11-15T01:00:00Z"),
            _call(1, "2026-11-15T01:00:00Z", out=20, mid="m0"),  # 同じ id は最後の usage を 1 回だけ
            _call(2, "2026-11-15T01:10:00Z"),
            {"type": "system", "subtype": "compact_boundary", "compactMetadata": {"trigger": "auto"}},
        ],
    )
    rows = [
        {"at": "2026-11-15T00:59:00Z", "session_id": "s", "transcript": t, "work": "#1", "policy": "current"},
        {"at": "2026-11-15T01:05:00Z", "session_id": "s", "work": "#1", "event": context_records.MARK_HANDOFF, "policy": "current"},
        {"at": "2026-11-15T01:20:00Z", "session_id": "s", "work": "#1", "verdict": "再起動", "reason": "手動"},
    ]
    got = context_report.report("#1", rows, [], None)
    assert got["sessions"] == 1
    assert got["parts"]["conductor"]["calls"] == 1 and got["parts"]["conductor"]["out"] == 20
    assert got["parts"]["handoff"]["calls"] == 1
    assert got["restarts"] == {"手動": 1} and got["restart_count"] == 1
    assert got["auto_compacts"] == 1
    assert "no_policy_rows" not in got  # 方針の列を数えない（#3995 の決定 1）
    assert got["quality"]["rework"] == context_records.UNKNOWN


def test_work_windows_do_not_double_count_two_works_in_one_session(tmp_path):
    compact = {"type": "system", "timestamp": "2026-11-15T01:10:00Z", "subtype": "compact_boundary", "compactMetadata": {"trigger": "auto"}}
    t = _transcript(tmp_path / "t.jsonl", [_call(0, "2026-11-15T01:00:00Z"), compact, _call(1, "2026-11-15T02:00:00Z")])
    rows = [
        {"at": "2026-11-15T00:59:00Z", "session_id": "s", "transcript": t, "work": "#1"},
        {"at": "2026-11-15T01:30:00Z", "session_id": "s", "work": "#2"},
    ]
    one = context_report.report("#1", rows, [], None)
    two = context_report.report("#2", rows, [], None)
    assert one["parts"]["conductor"]["calls"] == 1 and two["parts"]["conductor"]["calls"] == 1
    assert (one["auto_compacts"], two["auto_compacts"]) == (1, 0)  # 圧縮は起きた作業の期間でだけ数える
    assert context_report.works(rows) == ["#1", "#2"]


def test_span_cuts_calls_and_rows(tmp_path):
    compact = {"type": "system", "subtype": "compact_boundary", "compactMetadata": {"trigger": "auto"}}  # 時刻が無ければ直前の呼び出し
    t = _transcript(tmp_path / "t.jsonl", [_call(0, "2026-11-15T01:00:00Z"), compact, _call(1, "2026-11-15T03:00:00Z")])
    rows = [{"at": "2026-11-15T00:59:00Z", "session_id": "s", "transcript": t, "work": "#1"}]
    lo, hi = context_records.parse_ts("2026-11-15T02:00:00Z"), context_records.parse_ts("2026-11-15T04:00:00Z")
    got = context_report.report("#1", rows, [], (lo, hi))
    assert got["parts"]["conductor"]["calls"] == 1
    assert got["auto_compacts"] == 0


def test_usage_log_rows_go_to_the_ndf_bucket_and_missing_usage_log_is_empty(tmp_path):
    t = _transcript(tmp_path / "t.jsonl", [_call(0, "2026-11-15T01:00:00Z"), _call(1, "2026-11-15T01:30:00Z")])
    rows = [{"at": "2026-11-15T00:59:00Z", "session_id": "s", "transcript": t, "work": "#1"}]
    sub = str(tmp_path / "t" / "subagents" / "agent-a1.jsonl")
    other = str(tmp_path / "u" / "subagents" / "agent-b1.jsonl")
    usage_log = [
        {
            "at": "2026-11-15T01:10:00Z",
            "caller": "subagent",
            "agent_id": "a1",
            "transcript": sub,
            "usage": {"input_tokens": 5, "output_tokens": 1},
        },
        # 別のセッション（別のリポジトリやレーン）の行は、時刻が重なっても数えない
        {
            "at": "2026-11-15T01:15:00Z",
            "caller": "subagent",
            "agent_id": "b1",
            "transcript": other,
            "usage": {"input_tokens": 7, "output_tokens": 1},
        },
        {"at": "2026-11-15T01:20:00Z", "caller": "claude-p", "run": "x", "usage": {"input_tokens": 9, "output_tokens": 1}},
    ]
    got = context_report.report("#1", rows, usage_log, None)
    assert got["parts"]["ndf"]["calls"] == 1 and got["parts"]["ndf"]["inp"] == 5
    assert got["parts"]["conductor"]["calls"] == 2
    assert context_report.report("#1", rows, [{"usage": "unknown"}], None)["parts"]["ndf"]["calls"] == 0
    assert context_report.load_usage_log({"NDF_USAGE_DIR": str(tmp_path / "none")}) == ([], 0)


def test_cost_uses_weights_and_read_rate():
    call = {"inp": 1, "out": 1, "w5": 1, "w1h": 1, "read": 10, "model": "claude-opus-5-5-x"}
    assert context_report.call_cost(call) == 1.0 + 5.0 + 1.25 + 2.0 + 0.5
    assert context_report.read_rate(None) == context_report.READ_RATE_DEFAULT


def test_cli_report_rejects_bad_times(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NDF_CONTEXT_DIR", str(tmp_path))
    assert cli.main(["context", "report", "--work", "#1", "--since", "昨日"]) == 2
    assert cli.main(["context", "report", "--all", "--from", "朝"]) == 2
    assert cli.main(["context", "report", "--work", "#1", "--all"]) == 2
    capsys.readouterr()
    assert cli.main(["context", "report", "--all"]) == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["metrics"] == {"works": 0, "skipped": 0, "usage_skipped": 0}


def _session(tmp_path, first_user: str) -> str:
    return _transcript(
        tmp_path / "t.jsonl",
        [
            {"type": "user", "message": {"content": first_user}},
            _call(0, "2026-11-15T01:00:00Z"),
            _call(1, "2026-11-15T01:10:00Z"),
            _call(2, "2026-11-15T01:20:00Z"),
            _call(3, "2026-11-15T01:30:00Z"),
            _call(4, "2026-11-15T01:40:00Z"),
        ],
    )


def _mark(at: str, event: str) -> dict:
    return {"at": at, "session_id": "s", "work": "#1", "event": event}


def test_mark_bounds_take_first_restored_and_last_handoff_and_skip_bad_times(tmp_path):
    """現状固定（I-002）: 復元の終わりは最初の「復元完了」、引継ぎの始まりは最後の「引継ぎ開始」。読めない時刻のマーカーは無視する。"""
    t = _session(tmp_path, "続きは .ndf/handoff/x.md を読む")
    rows = [
        {"at": "2026-11-15T00:59:00Z", "session_id": "s", "transcript": t, "work": "#1"},
        _mark("2026-11-15T01:05:00Z", context_records.MARK_RESTORED),
        _mark("2026-11-15T01:15:00Z", context_records.MARK_RESTORED),
        _mark("2026-11-15T01:25:00Z", context_records.MARK_HANDOFF),
        _mark("2026-11-15T01:35:00Z", context_records.MARK_HANDOFF),
        _mark("読めない", context_records.MARK_RESTORED),
        _mark("読めない", context_records.MARK_HANDOFF),
    ]
    got = context_report.report("#1", rows, [], None)
    parts = got["parts"]
    assert (parts["restore"]["calls"], parts["conductor"]["calls"], parts["handoff"]["calls"]) == (1, 3, 1)
    assert got["restores"] == [{"context": 100, "calls": 1}]
    assert got["unknown_parts"] == {"restore": 0, "handoff": 0}


def test_mark_bounds_ignore_restored_when_not_started_by_handoff(tmp_path):
    """現状固定（I-002）: 引継ぎ文書で始まらないセッションでは「復元完了」のマーカーを使わない。"""
    t = _session(tmp_path, "始める")
    rows = [
        {"at": "2026-11-15T00:59:00Z", "session_id": "s", "transcript": t, "work": "#1"},
        _mark("2026-11-15T01:05:00Z", context_records.MARK_RESTORED),
        _mark("2026-11-15T01:35:00Z", context_records.MARK_HANDOFF),
    ]
    got = context_report.report("#1", rows, [], None)
    parts = got["parts"]
    assert (parts["restore"]["calls"], parts["conductor"]["calls"], parts["handoff"]["calls"]) == (0, 4, 1)
    assert got["restores"] == []
    assert got["unknown_parts"] == {"restore": 0, "handoff": 0}


def test_mark_bounds_unknown_when_started_by_handoff_without_marks(tmp_path):
    """現状固定（I-002）: 引継ぎ文書で始まりマーカーが無ければ、復元の期間は不明・再起動したのに引継ぎのマーカーが無ければ不明。"""
    t = _session(tmp_path, "続きは .ndf/handoff/x.md を読む")
    rows = [
        {"at": "2026-11-15T00:59:00Z", "session_id": "s", "transcript": t, "work": "#1"},
        {"at": "2026-11-15T01:50:00Z", "session_id": "s", "work": "#1", "verdict": "再起動", "reason": "手動"},
    ]
    got = context_report.report("#1", rows, [], None)
    assert got["parts"]["conductor"]["calls"] == 5
    assert got["unknown_parts"] == {"restore": 1, "handoff": 1}


def _read_call(i: int, ts: str, read: int) -> dict:
    usage = {"input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": read}
    return {"type": "assistant", "timestamp": ts, "message": {"id": f"r{i}", "model": "claude-x", "usage": usage}}


def test_sum_sessions_adds_parts_rounds_and_counts_unknowns(tmp_path):
    """現状固定（I-029）: セッションごとの区分の合算・費用と秒の丸め・自動圧縮・復元・マーカーの無いセグメントの数え方。"""
    compact = {"type": "system", "subtype": "compact_boundary", "compactMetadata": {"trigger": "auto"}}
    a = _transcript(
        tmp_path / "a.jsonl",
        [
            {"type": "user", "message": {"content": "続きは .ndf/handoff/x.md を読む"}},
            _call(0, "2026-11-15T01:00:00Z"),
            _call(1, "2026-11-15T01:00:10Z"),
            _call(2, "2026-11-15T01:00:25Z"),
            compact,
        ],
    )
    b = _transcript(
        tmp_path / "b.jsonl",
        [
            _read_call(0, "2026-11-15T02:00:00Z", 3),
            _read_call(1, "2026-11-15T02:00:00.400Z", 3),
            compact,
            compact,
        ],
    )
    rows = [
        {"at": "2026-11-15T00:59:00Z", "session_id": "a", "transcript": a, "work": "#1"},
        {"at": "2026-11-15T01:00:05Z", "session_id": "a", "work": "#1", "event": context_records.MARK_RESTORED},
        {"at": "2026-11-15T01:59:00Z", "session_id": "b", "transcript": b, "work": "#1"},
        {"at": "2026-11-15T02:30:00Z", "session_id": "b", "work": "#1", "verdict": "再起動", "reason": "手動"},
    ]
    got = context_report.report("#1", rows, [], None)
    assert got["sessions"] == 2
    assert got["parts"] == {
        "conductor": {"inp": 202, "out": 22, "read": 6, "w5": 0, "w1h": 0, "calls": 4, "seconds": 15, "cost": 313},
        "ndf": {"inp": 0, "out": 0, "read": 0, "w5": 0, "w1h": 0, "calls": 0, "seconds": 0, "cost": 0},
        "handoff": {"inp": 0, "out": 0, "read": 0, "w5": 0, "w1h": 0, "calls": 0, "seconds": 0, "cost": 0},
        "restore": {"inp": 100, "out": 10, "read": 0, "w5": 0, "w1h": 0, "calls": 1, "seconds": 0, "cost": 150},
    }
    assert got["restores"] == [{"context": 100, "calls": 1}]
    assert got["unknown_parts"] == {"restore": 0, "handoff": 1}
    assert got["auto_compacts"] == 3
    rows.append({"at": "2026-11-15T03:00:00Z", "session_id": "c", "transcript": str(tmp_path / "missing.jsonl"), "work": "#1"})
    missing = context_report.report("#1", rows, [], None)
    assert missing["sessions"] == 3 and missing["auto_compacts"] == context_records.UNKNOWN
    assert context_report.report("#9", rows, [], None)["auto_compacts"] == context_records.UNKNOWN


# --- 現状固定: context_records._Calls.take（I-017。read_calls から通す。圧縮の時刻・重複の id・最初の発言） ---


def _take_assistant(mid, ts: str, inp: int = 100, out: int = 10, uuid=None) -> dict:
    e = {"type": "assistant", "timestamp": ts, "message": {"model": "m", "usage": {"input_tokens": inp, "output_tokens": out}}}
    if mid:
        e["message"]["id"] = mid
    if uuid:
        e["uuid"] = uuid
    return e


def _take_call(ts: float, inp: int, out: int = 10) -> dict:
    return {"ts": ts, "model": "m", "inp": inp, "out": out, "read": 0, "w5": 0, "w1h": 0, "context": inp}


_AUTO = {"type": "system", "subtype": "compact_boundary", "compactMetadata": {"trigger": "auto"}}
_T0 = 1767225600.0  # 2026-01-01T00:00:00Z


def _take_entries() -> list:
    return [
        _AUTO,  # 呼び出しの前の圧縮は時刻 None
        {"type": "user", "isMeta": True, "message": {"content": "meta"}},
        {"type": "user", "message": {"content": [{"type": "text", "text": "read .ndf/handoff/x.md"}]}},
        _take_assistant("m1", "2026-01-01T00:00:00Z", 100),
        _take_assistant("m1", "2026-01-01T00:00:05Z", 200, 20),  # 同じ id は最後の usage・最初の時刻
        _take_assistant(None, "2026-01-01T00:01:00Z", 50, uuid="u1"),  # id が無ければ uuid
        {"type": "assistant", "timestamp": "2026-01-01T00:02:00Z", "message": {"id": "m3"}},  # usage が無い
        _AUTO,  # 時刻が無ければ直前の呼び出しの時刻
        {**_AUTO, "timestamp": "2026-01-01T00:03:00Z"},
        {**_AUTO, "compactMetadata": {"trigger": "manual"}},  # 手動の圧縮は数えない
        _take_assistant("m4", "2026-01-01T00:04:00Z", 1),
        {"type": "assistant", "timestamp": "2026-01-01T00:05:00Z", "message": "str"},
    ]


def test_take_current_behavior_collects_calls_compacts_and_handoff(tmp_path):
    t = _transcript(tmp_path / "t.jsonl", _take_entries())
    calls, compacts, handoff = context_records.read_calls(t)
    assert calls == [_take_call(_T0, 200, 20), _take_call(_T0 + 60, 50), _take_call(_T0 + 240, 1)]
    assert compacts == [None, _T0 + 60, _T0 + 180]
    assert handoff is True


def test_take_current_behavior_skips_ids_seen_in_earlier_files(tmp_path):
    t = _transcript(tmp_path / "t.jsonl", _take_entries())
    seen = {"m1"}
    calls, _, _ = context_records.read_calls(t, seen)
    assert calls == [_take_call(_T0 + 60, 50), _take_call(_T0 + 240, 1)]
    assert seen == {"m1", "m4", "u1"}
    calls, _, _ = context_records.read_calls(t, {"m4"})
    assert calls == [_take_call(_T0, 200, 20), _take_call(_T0 + 60, 50)]


def test_take_current_behavior_plain_first_user_and_unreadable(tmp_path):
    t = _transcript(tmp_path / "t.jsonl", [{"type": "user", "message": {"content": "plain"}}, _take_assistant("x", "2026-01-01T00:00:00Z")])
    assert context_records.read_calls(t) == ([_take_call(_T0, 100)], [], False)
    assert context_records.read_calls(None) == ([], None, False)
    assert context_records.read_calls(str(tmp_path / "missing.jsonl")) == ([], None, False)


# --- 判定の記録・見込みの較正・record（#3995 の Task 1 で test_context_policy から移した） ---


def test_context_dir_follows_the_legacy_state_root(tmp_path):
    assert context_records.context_dir({"NDF_CONTEXT_DIR": str(tmp_path)}) == tmp_path
    env = {"NDF_STATE_HOME": str(tmp_path / "s")}
    assert context_records.context_dir(env) == tmp_path / "s" / "context"
    assert registry.legacy_root(env) == identity.legacy_state_root(env) / "relay"
    assert identity.legacy_state_root({"XDG_STATE_HOME": str(tmp_path)}) == tmp_path / "ndf"


def test_recorded_work_normalizes_issue_numbers():
    assert context_records.recorded_work("#830 と #829（2026-11-15・v1.2.3）", None) == "#829 #830"
    assert context_records.recorded_work("設計", "sid") == ""
    assert context_records.recorded_work("設計", None) == "設計"


def test_append_and_read_skip_broken_lines(tmp_path):
    env = {"NDF_CONTEXT_DIR": str(tmp_path)}
    late = context_records.record_row("s1", "#1", APPLIED, context_records.MARK_HANDOFF, None) | {"at": "2026-11-15T02:00:00+00:00"}
    early = context_records.record_row("s1", "#1", APPLIED, None, "手動") | {"at": "2026-11-15T01:00:00+00:00"}
    path = context_records.append("o__r", late, env)
    with path.open("a", encoding="utf-8") as f:
        f.write('{"cut\n')
    context_records.append("o__r", early, env)
    rows, skipped = context_records.read("o__r", env)
    assert [r["at"] for r in rows] == [early["at"], late["at"]]
    assert skipped == 1
    assert {r["version"] for r in rows} == {1}
    assert rows[0]["verdict"] == "再起動" and rows[0]["reason"] == "手動" and rows[0]["limit"] == 600_000


def test_append_refuses_a_row_outside_the_shape_and_writes_nothing(tmp_path):
    env = {"NDF_CONTEXT_DIR": str(tmp_path)}
    row = context_records.record_row("s1", "#1", APPLIED, None, "手動")
    for bad in (row | {"created_at": row["at"]}, row | {"limit": "500000"}, {k: v for k, v in row.items() if k != "work"}):
        with pytest.raises(InternalError):
            context_records.append("o__r", bad, env)
    assert not context_records.decisions_path("o__r", env).exists()


def _calls_transcript(path, calls) -> str:
    lines = [{"type": "user", "message": {"content": "続きは .ndf/handoff/a.md から"}}]
    for i, (ts, ctx) in enumerate(calls):
        usage = {"input_tokens": ctx, "output_tokens": 10, "cache_read_input_tokens": 0}
        lines.append({"type": "assistant", "timestamp": ts, "message": {"id": f"m{i}", "model": "x", "usage": usage}})
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    return str(path)


def test_estimate_counts_samples_and_needs_three(tmp_path):
    rows = []
    for n in range(3):
        t = _calls_transcript(tmp_path / f"t{n}.jsonl", [("2026-11-15T01:00:00Z", 100 + n), ("2026-11-15T01:10:00Z", 200 + n)])
        rows.append({"at": "2026-11-15T01:05:00Z", "session_id": f"s{n}", "transcript": t, "event": context_records.MARK_RESTORED})
        rows.append({"at": "2026-11-15T01:06:00Z", "session_id": f"s{n}", "transcript": t, "event": context_records.MARK_HANDOFF})
    got = context_records.estimate(rows, None)
    assert got["samples"]["restore"] == 3 and got["samples"]["handoff"] == 3
    assert got["restore_context"] == 101.0 and got["restore_calls"] == 1.0
    assert got["handoff_calls"] == 1.0 and got["handoff_output"] == 10.0
    assert context_records.estimate(rows[:4], None)["restore_context"] is None


def test_calibrate_writes_estimates_and_keeps_old_on_failure(tmp_path, monkeypatch):
    env = {"NDF_CONTEXT_DIR": str(tmp_path)}
    monkeypatch.setattr(context_records, "repo_key", lambda root: "o__r")
    body, _ = context_records.calibrate(None, 30, 0.0, env)
    path = context_records.estimates_path("o__r", env)
    assert json.loads(path.read_text(encoding="utf-8"))["samples"] == body["samples"]
    path.parent.chmod(0o500)
    try:
        with pytest.raises(OSError):
            context_records.calibrate(None, 30, 0.0, env)
    finally:
        path.parent.chmod(0o700)
    assert path.is_file()


def _last(capsys) -> dict:
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_cli_record_rejects_a_reason_outside_the_table(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NDF_CONTEXT_DIR", str(tmp_path))
    assert cli.main(["context", "record", "--verdict", "再起動", "--reason", "気分", "--work", "#1"]) == 2
    assert cli.main(["context", "record", "--verdict", "再起動", "--work", "#1"]) == 2
    assert cli.main(["context", "record", "--event", "引継ぎ開始", "--reason", "手動", "--work", "#1"]) == 2
    capsys.readouterr()
    monkeypatch.setenv("NDF_CONTEXT_LIMIT", "500000")
    assert cli.main(["context", "record", "--event", "引継ぎ開始", "--work", "#1"]) == 0
    row = _last(capsys)["items"][0]
    assert row["event"] == "引継ぎ開始"
    # record は resolve と同じ 2 つの値を行に書く（#3995 の決定 1）
    assert (row["decide_from"], row["limit"], row["sources"]["limit"]) == (450_000, 500_000, "env")
    assert not set(POLICY_COLUMNS) & set(row)


def test_cli_record_fails_when_it_cannot_write(tmp_path, monkeypatch, capsys):
    blocked = tmp_path / "file"
    blocked.write_text("", encoding="utf-8")
    monkeypatch.setenv("NDF_CONTEXT_DIR", str(blocked))
    assert cli.main(["context", "record", "--event", "引継ぎ開始", "--work", "#1"]) == 1
    assert _last(capsys)["status"] == "stopped"


def test_estimate_samples_restore_handoff_and_stage_segments(tmp_path):
    """現状固定（I-005）: 復元・引継ぎ・工程のセグメントの標本の集め方と、見込みの値の形。"""
    calls = ["2026-11-15T01:00:00Z", "2026-11-15T01:10:00Z", "2026-11-15T01:20:00Z", "2026-11-15T03:00:00Z"]
    rows = []
    for n in range(3):
        sid = f"s{n}"
        t = _calls_transcript(tmp_path / f"t{n}.jsonl", [(ts, 100 + n) for ts in calls])
        rows += [
            {"at": "2026-11-15T00:59:00Z", "session_id": sid, "transcript": t, "stage": "design", "verdict": CONTINUE},
            {"at": "2026-11-15T01:05:00Z", "session_id": sid, "event": context_records.MARK_RESTORED},
            {"at": "2026-11-15T01:12:00Z", "session_id": sid, "event": context_records.MARK_HANDOFF},
            {"at": "2026-11-15T01:15:00Z", "session_id": sid, "stage": "impl", "verdict": CONTINUE},
            {"at": "2026-11-15T05:00:00Z", "session_id": sid, "stage": "review", "verdict": CONTINUE},
        ]
    plain = tmp_path / "plain.jsonl"
    plain.write_text(json.dumps({"type": "user", "message": {"content": "始める"}}) + "\n", encoding="utf-8")
    rows += [
        {"at": "2026-11-15T00:59:00Z", "session_id": "s9", "transcript": str(plain)},
        {"at": "2026-11-15T01:05:00Z", "session_id": "s9", "event": context_records.MARK_RESTORED},
    ]
    rows.sort(key=lambda r: r["at"])
    got = context_records.estimate(rows, None)
    got.pop("at")
    assert got == {
        "restore_context": 101.0,
        "restore_calls": 1.0,
        "handoff_calls": 2.0,
        "handoff_output": 20.0,
        "stage_calls": {"design": 2.0, "*": 2.0, "impl": 2.0},
        "stage_expiries": {"design": 0.0, "*": 0.5, "impl": 1.0},
        "samples": {"restore": 3, "handoff": 3, "stage:design": 3, "stage:*": 6, "stage:impl": 3},
    }
    late = context_records.estimate(rows, context_records.parse_ts("2026-11-15T06:00:00Z"))
    late.pop("at")
    assert late == {
        "restore_context": None,
        "restore_calls": None,
        "handoff_calls": None,
        "handoff_output": None,
        "stage_calls": {},
        "stage_expiries": {},
        "samples": {"restore": 0, "handoff": 0},
    }


def test_estimate_stage_segments_current_behavior_edges(tmp_path):
    """現状固定（I-026）: 工程の期間の切り方の端。根拠は仕様ではなく今の出力。

    - 区切りは工程つきの「継続」の行だけ（工程の無い行・別の判定・読めない時刻は区切らない）。行の並びは時刻で並べ直す
    - 期間は [始まり, 次の区切り) で、始まりちょうどの呼び出しは入る。時刻の無い呼び出しは数えない
    - 間隔がちょうど 1 時間はキャッシュ切れに数えず、1 秒でも超えれば数える
    - 呼び出しの無い期間と最後の期間は標本にしない。同じ工程の期間は別の標本として足す
    """
    calls = [
        "2026-11-15T01:00:00Z",
        "2026-11-15T01:10:00Z",
        "2026-11-15T02:10:00Z",
        "2026-11-15T03:10:00Z",
        "2026-11-15T04:10:01Z",
        None,
        "2026-11-15T04:20:00Z",
        "2026-11-15T06:00:00Z",
    ]
    lines: list[dict] = [{"type": "user", "message": {"content": "始める"}}]
    for i, ts in enumerate(calls):
        usage = {"input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": 0}
        entry: dict = {"type": "assistant", "message": {"id": f"m{i}", "model": "x", "usage": usage}}
        if ts is not None:
            entry["timestamp"] = ts
        lines.append(entry)
    t = tmp_path / "t.jsonl"
    t.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    go = CONTINUE
    rows = [
        {"at": "2026-11-15T04:30:00Z", "session_id": "s", "stage": "empty", "verdict": go},
        {"at": "2026-11-15T00:59:00Z", "session_id": "s", "transcript": str(t), "stage": "a", "verdict": go},
        {"at": "2026-11-15T01:05:00Z", "session_id": "s", "verdict": go},
        {"at": "2026-11-15T01:06:00Z", "session_id": "s", "stage": "x", "verdict": "再起動"},
        {"at": "not a time", "session_id": "s", "stage": "y", "verdict": go},
        {"at": "2026-11-15T02:10:00Z", "session_id": "s", "stage": "b", "verdict": go},
        {"at": "2026-11-15T04:40:00Z", "session_id": "s", "stage": "a", "verdict": go},
        {"at": "2026-11-15T07:00:00Z", "session_id": "s", "stage": "last", "verdict": go},
    ]
    got = context_records.estimate(rows, None)
    got.pop("at")
    # 期間: a=[00:59,02:10) 2 呼び出し・切れ 0 / b=[02:10,04:30) 4 呼び出し・切れ 1 / empty は空 / a=[04:40,07:00) 1 呼び出し / last は最後
    assert got == {
        "restore_context": None,
        "restore_calls": None,
        "handoff_calls": None,
        "handoff_output": None,
        "stage_calls": {"a": 1.5, "*": 2.0, "b": 4.0},
        "stage_expiries": {"a": 0.0, "*": 0.0, "b": 1.0},
        "samples": {"restore": 0, "handoff": 0, "stage:a": 2, "stage:*": 3, "stage:b": 1},
    }


# --- 旧い行（消した方針の列 policy・margin・config_error を持つ）だけの記録（#3995 の Task 1。決定 1 の代償の基準） ---

_OLD = {"schema": 1, "policy": "current", "margin": 50_000, "config_error": None}


def _append_old(row: dict, env: dict) -> None:
    """旧いプラグインと同じに、核を通さず判定の記録へ 1 行を足す（旧い行の列のうち `row` に無いものは新しい行の値）。"""
    path = context_records.decisions_path("o__r", env)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({**context_records.record_row(None, "", APPLIED, None, "手動"), **row, **_OLD}, ensure_ascii=False) + "\n")


def test_report_parts_of_old_rows_only(tmp_path):
    # 方針の列を持つ旧い行だけの記録で、区分・再起動・自動圧縮の数え方を縛る（U2 で新しい行と混ぜても同じであることの基準）
    t = _transcript(
        tmp_path / "t.jsonl",
        [
            {"type": "user", "message": {"content": "始める"}},
            _call(0, "2026-11-15T01:00:00Z"),
            _call(1, "2026-11-15T01:00:00Z", out=20, mid="m0"),
            _call(2, "2026-11-15T01:10:00Z"),
            {"type": "system", "subtype": "compact_boundary", "compactMetadata": {"trigger": "auto"}},
        ],
    )
    rows = [
        {"at": "2026-11-15T00:59:00Z", "session_id": "s", "transcript": t, "work": "#1", **_OLD},
        {"at": "2026-11-15T01:05:00Z", "session_id": "s", "work": "#1", "event": context_records.MARK_HANDOFF, **_OLD},
        {"at": "2026-11-15T01:20:00Z", "session_id": "s", "work": "#1", "verdict": "再起動", "reason": "手動", **_OLD},
    ]
    got = context_report.report("#1", rows, [], None)
    assert got["sessions"] == 1
    assert (got["parts"]["conductor"]["calls"], got["parts"]["conductor"]["out"], got["parts"]["handoff"]["calls"]) == (1, 20, 1)
    assert got["restarts"] == {"手動": 1} and got["restart_count"] == 1
    assert got["auto_compacts"] == 1


def test_calibrate_reports_the_rows_it_skipped(tmp_path, monkeypatch, capsys, last_result):
    monkeypatch.setenv("NDF_CONTEXT_DIR", str(tmp_path))
    monkeypatch.setattr(context_records, "toplevel", lambda cwd: None)
    path = context_records.decisions_path(context_records.LOCAL, {"NDF_CONTEXT_DIR": str(tmp_path)})
    path.parent.mkdir(parents=True)
    path.write_text("{}\n[]\n", encoding="utf-8")
    assert cli.main(["context", "calibrate"]) == 0
    assert last_result(capsys.readouterr().out)["metrics"] == {"samples": 0, "skipped": 2}


def test_calibrate_estimates_of_old_rows_only(tmp_path, monkeypatch):
    # 方針の列を持つ旧い行だけの記録から較正した見込みを縛る（U2 で新しい行と混ぜても同じであることの基準）
    env = {"NDF_CONTEXT_DIR": str(tmp_path / "ctx")}
    monkeypatch.setattr(context_records, "repo_key", lambda root: "o__r")
    for n in range(3):
        t = _calls_transcript(tmp_path / f"t{n}.jsonl", [("2026-11-15T01:00:00Z", 100 + n), ("2026-11-15T01:10:00Z", 200 + n)])
        restored = {"at": "2026-11-15T01:05:00Z", "session_id": f"s{n}", "transcript": t, "event": context_records.MARK_RESTORED}
        handoff = {"at": "2026-11-15T01:06:00Z", "session_id": f"s{n}", "transcript": t, "event": context_records.MARK_HANDOFF}
        _append_old(restored, env)
        _append_old(handoff, env)
    body, _ = context_records.calibrate(None, 30, 0.0, env)
    assert body["samples"]["restore"] == 3 and body["samples"]["handoff"] == 3
    assert (body["restore_context"], body["restore_calls"]) == (101.0, 1.0)
    assert (body["handoff_calls"], body["handoff_output"]) == (1.0, 10.0)


# --- 新しい行（2 つの値だけを持つ）と旧い行を混ぜた記録（#3995 の Task 4。決定 1 の代償） ---


@pytest.mark.parametrize(("event", "reason"), [(context_records.MARK_HANDOFF, None), (None, "手動")])
def test_record_row_writes_the_two_values_without_the_policy_columns(event, reason):
    row = context_records.record_row("s1", "#1", APPLIED, event, reason)
    assert (row["decide_from"], row["limit"], row["sources"]) == (450_000, 600_000, APPLIED.sources)
    assert not set(POLICY_COLUMNS) & set(row)


def test_record_row_refuses_a_reason_outside_the_words():
    with pytest.raises(ValueError):
        context_records.record_row("s1", "#1", APPLIED, None, "手働")


def _new(row: dict) -> dict:
    """旧い行の方針の列を外し、新しい行の 2 つの値を足した行。"""
    kept = {k: v for k, v in row.items() if k not in POLICY_COLUMNS}
    return kept | APPLIED.as_dict()


def test_report_of_mixed_rows_equals_old_rows_only(tmp_path):
    # 旧い行と新しい行を混ぜても、区分・再起動・自動圧縮は旧い行だけのとき（test_report_parts_of_old_rows_only）と同じ
    t = _transcript(
        tmp_path / "t.jsonl",
        [
            {"type": "user", "message": {"content": "始める"}},
            _call(0, "2026-11-15T01:00:00Z"),
            _call(1, "2026-11-15T01:00:00Z", out=20, mid="m0"),
            _call(2, "2026-11-15T01:10:00Z"),
            {"type": "system", "subtype": "compact_boundary", "compactMetadata": {"trigger": "auto"}},
        ],
    )
    old = [
        {"at": "2026-11-15T00:59:00Z", "session_id": "s", "transcript": t, "work": "#1", **_OLD},
        {"at": "2026-11-15T01:05:00Z", "session_id": "s", "work": "#1", "event": context_records.MARK_HANDOFF, **_OLD},
        {"at": "2026-11-15T01:20:00Z", "session_id": "s", "work": "#1", "verdict": "再起動", "reason": "手動", **_OLD},
    ]
    mixed = [old[0], _new(old[1]), _new(old[2])]
    assert context_report.report("#1", mixed, [], None) == context_report.report("#1", old, [], None)


def test_calibrate_of_mixed_rows_equals_old_rows_only(tmp_path, monkeypatch):
    # 旧い行と新しい行を混ぜても、較正した見込みは旧い行だけのとき（test_calibrate_estimates_of_old_rows_only）と同じ
    monkeypatch.setattr(context_records, "repo_key", lambda root: "o__r")
    bodies = []
    for kind in ("old", "mixed"):
        env = {"NDF_CONTEXT_DIR": str(tmp_path / kind)}
        for n in range(3):
            t = _calls_transcript(tmp_path / f"t{n}.jsonl", [("2026-11-15T01:00:00Z", 100 + n), ("2026-11-15T01:10:00Z", 200 + n)])
            restored = {"at": "2026-11-15T01:05:00Z", "session_id": f"s{n}", "transcript": t, "event": context_records.MARK_RESTORED}
            handoff = {"at": "2026-11-15T01:06:00Z", "session_id": f"s{n}", "transcript": t, "event": context_records.MARK_HANDOFF}
            _append_old(restored, env)
            if kind == "old" or n == 0:
                _append_old(handoff, env)
            else:
                context_records.append("o__r", context_records.record_row(None, "", APPLIED, handoff["event"], None) | handoff, env)
        body, _ = context_records.calibrate(None, 30, 0.0, env)
        body.pop("at")
        bodies.append(body)
    assert bodies[0] == bodies[1]
    assert bodies[1]["samples"]["handoff"] == 3
