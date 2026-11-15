"""作業ごとの集計（`ndf relay context report`。#3569）。"""

from __future__ import annotations

import json

from ndf.domains.relay import cli, context_records, context_report


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
    assert got["no_policy_rows"] == 1
    assert got["quality"]["rework"] == context_records.UNKNOWN


def test_work_windows_do_not_double_count_two_works_in_one_session(tmp_path):
    t = _transcript(tmp_path / "t.jsonl", [_call(0, "2026-11-15T01:00:00Z"), _call(1, "2026-11-15T02:00:00Z")])
    rows = [
        {"at": "2026-11-15T00:59:00Z", "session_id": "s", "transcript": t, "work": "#1"},
        {"at": "2026-11-15T01:30:00Z", "session_id": "s", "work": "#2"},
    ]
    one = context_report.report("#1", rows, [], None)
    two = context_report.report("#2", rows, [], None)
    assert one["parts"]["conductor"]["calls"] == 1 and two["parts"]["conductor"]["calls"] == 1
    assert context_report.works(rows) == ["#1", "#2"]


def test_span_cuts_calls_and_rows(tmp_path):
    t = _transcript(tmp_path / "t.jsonl", [_call(0, "2026-11-15T01:00:00Z"), _call(1, "2026-11-15T03:00:00Z")])
    rows = [{"at": "2026-11-15T00:59:00Z", "session_id": "s", "transcript": t, "work": "#1"}]
    lo, hi = context_records.parse_ts("2026-11-15T02:00:00Z"), context_records.parse_ts("2026-11-15T04:00:00Z")
    got = context_report.report("#1", rows, [], (lo, hi))
    assert got["parts"]["conductor"]["calls"] == 1


def test_ledger_rows_go_to_the_ndf_bucket_and_missing_ledger_is_empty(tmp_path):
    t = _transcript(tmp_path / "t.jsonl", [_call(0, "2026-11-15T01:00:00Z"), _call(1, "2026-11-15T01:30:00Z")])
    rows = [{"at": "2026-11-15T00:59:00Z", "session_id": "s", "transcript": t, "work": "#1"}]
    ledger = [{"at": "2026-11-15T01:10:00Z", "caller": "subagent", "agent_id": "a1", "usage": {"input_tokens": 5, "output_tokens": 1}}]
    got = context_report.report("#1", rows, ledger, None)
    assert got["parts"]["ndf"]["calls"] == 1 and got["parts"]["ndf"]["inp"] == 5
    assert got["parts"]["conductor"]["calls"] == 2
    assert context_report.report("#1", rows, [{"usage": "unknown"}], None)["parts"]["ndf"]["calls"] == 0
    assert context_report.load_ledger({"NDF_USAGE_DIR": str(tmp_path / "none")}) == []


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
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["metrics"]["works"] == 0


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
    """現状固定（I-002）: 復元の終わりは最初の「復元完了」、引継ぎの始まりは最後の「引継ぎ開始」。読めない時刻の印は無視する。"""
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
    """現状固定（I-002）: 引継ぎ文書で始まらないセッションでは「復元完了」の印を使わない。"""
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
    """現状固定（I-002）: 引継ぎ文書で始まり印が無ければ、復元の区間は不明・再起動したのに引継ぎの印が無ければ不明。"""
    t = _session(tmp_path, "続きは .ndf/handoff/x.md を読む")
    rows = [
        {"at": "2026-11-15T00:59:00Z", "session_id": "s", "transcript": t, "work": "#1"},
        {"at": "2026-11-15T01:50:00Z", "session_id": "s", "work": "#1", "verdict": "再起動", "reason": "手動"},
    ]
    got = context_report.report("#1", rows, [], None)
    assert got["parts"]["conductor"]["calls"] == 5
    assert got["unknown_parts"] == {"restore": 1, "handoff": 1}
