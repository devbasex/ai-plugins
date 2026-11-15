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
    compact = {"type": "system", "timestamp": "2026-11-15T01:10:00Z", "subtype": "compact_boundary", "compactMetadata": {"trigger": "auto"}}
    t = _transcript(tmp_path / "t.jsonl", [_call(0, "2026-11-15T01:00:00Z"), compact, _call(1, "2026-11-15T02:00:00Z")])
    rows = [
        {"at": "2026-11-15T00:59:00Z", "session_id": "s", "transcript": t, "work": "#1"},
        {"at": "2026-11-15T01:30:00Z", "session_id": "s", "work": "#2"},
    ]
    one = context_report.report("#1", rows, [], None)
    two = context_report.report("#2", rows, [], None)
    assert one["parts"]["conductor"]["calls"] == 1 and two["parts"]["conductor"]["calls"] == 1
    assert (one["auto_compacts"], two["auto_compacts"]) == (1, 0)  # 圧縮は起きた作業の区間でだけ数える
    assert context_report.works(rows) == ["#1", "#2"]


def test_span_cuts_calls_and_rows(tmp_path):
    compact = {"type": "system", "subtype": "compact_boundary", "compactMetadata": {"trigger": "auto"}}  # 時刻が無ければ直前の呼び出し
    t = _transcript(tmp_path / "t.jsonl", [_call(0, "2026-11-15T01:00:00Z"), compact, _call(1, "2026-11-15T03:00:00Z")])
    rows = [{"at": "2026-11-15T00:59:00Z", "session_id": "s", "transcript": t, "work": "#1"}]
    lo, hi = context_records.parse_ts("2026-11-15T02:00:00Z"), context_records.parse_ts("2026-11-15T04:00:00Z")
    got = context_report.report("#1", rows, [], (lo, hi))
    assert got["parts"]["conductor"]["calls"] == 1
    assert got["auto_compacts"] == 0


def test_ledger_rows_go_to_the_ndf_bucket_and_missing_ledger_is_empty(tmp_path):
    t = _transcript(tmp_path / "t.jsonl", [_call(0, "2026-11-15T01:00:00Z"), _call(1, "2026-11-15T01:30:00Z")])
    rows = [{"at": "2026-11-15T00:59:00Z", "session_id": "s", "transcript": t, "work": "#1"}]
    sub = str(tmp_path / "t" / "subagents" / "agent-a1.jsonl")
    other = str(tmp_path / "u" / "subagents" / "agent-b1.jsonl")
    ledger = [
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


def _read_call(i: int, ts: str, read: int) -> dict:
    usage = {"input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": read}
    return {"type": "assistant", "timestamp": ts, "message": {"id": f"r{i}", "model": "claude-x", "usage": usage}}


def test_sum_sessions_adds_parts_rounds_and_counts_unknowns(tmp_path):
    """現状固定（I-029）: セッションごとの区分の合算・費用と秒の丸め・自動圧縮・復元・印の無い区間の数え方。"""
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
