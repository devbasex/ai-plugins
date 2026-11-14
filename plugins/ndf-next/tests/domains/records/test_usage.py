"""使用量の帳簿: 応答ごとの数え方（#1979）・JST の両端（#1978）・置き場（#2575）・停止の差（#2576）。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from ndf.domains.records import jsonl, usage
from ndf.domains.records import usage_decide as ud


def _line(mid: str, at: str, out: int) -> dict:
    return {"timestamp": at, "message": {"id": mid, "usage": {"input_tokens": 1, "output_tokens": out}}}


def test_same_message_id_counts_the_last_line_once() -> None:
    recs = [_line("m1", "2026-11-14T01:00:00Z", 3), _line("m1", "2026-11-14T01:00:01Z", 9), _line("m2", "2026-11-14T02:00:00Z", 1)]
    assert ud.transcript_total(recs) == ud.Usage(2, 10, 0, 0)


def test_period_is_applied_per_response_not_per_conversation() -> None:
    recs = [_line("m1", "2026-11-13T14:59:59Z", 5), _line("m2", "2026-11-13T15:00:00Z", 7)]
    a, b = ud.jst_bounds(date(2026, 11, 14), date(2026, 11, 14))
    assert ud.transcript_total(recs, a, b) == ud.Usage(1, 7, 0, 0)
    assert ud.transcript_total(recs, a.replace(year=2030), None) == ud.UNKNOWN


def test_closed_query_uses_both_jst_ends() -> None:
    q = ud.closed_query(date(2026, 11, 1), date(2026, 11, 14))
    assert q == "closed:2026-11-01T00:00:00+09:00..2026-11-14T23:59:59+09:00"


def test_ledger_dir_prefers_env_then_state_and_never_falls_back_to_tmp(tmp_path: Path) -> None:
    assert ud.ledger_dir({"V": str(tmp_path / "x")}, "V", tmp_path, "usage") == tmp_path / "x"
    assert ud.ledger_dir({}, "V", tmp_path, "usage") == tmp_path / "usage"
    with pytest.raises(LookupError):
        ud.ledger_dir({}, "V", None, "usage")


def test_stop_delta_subtracts_the_earlier_rows_of_the_same_agent() -> None:
    prior = [{"agent_id": "a", "usage": {"output_tokens": 4}}, {"agent_id": "b", "usage": {"output_tokens": 100}}]
    assert ud.stop_delta(prior, "a", ud.Usage(0, 10, 0, 0)) == ud.Usage(0, 6, 0, 0)


def test_reconcile_takes_the_transcript_over_rows_and_counts_unknown_rows() -> None:
    rows = [
        {"at": "2026-11-14T00:00:00+00:00", "agent_id": "a", "usage": {"output_tokens": 1}},
        {"at": "2026-11-14T00:00:00+00:00", "caller": "claude-p", "usage": {"output_tokens": 5}},
        {"at": "2026-11-14T00:00:00+00:00", "agent_id": "b", "usage": "unknown"},
    ]
    total, unknown = ud.reconcile(rows, {"a": ud.Usage(0, 3, 0, 0), "b": ud.UNKNOWN})
    assert total == ud.Usage(0, 8, 0, 0) and unknown == 1


def _transcript(path: Path, lines: list[dict]) -> Path:
    path.write_text("".join(json.dumps(x) + "\n" for x in lines) + "{書きかけ\n", encoding="utf-8")
    return path


def test_subagent_and_cli_rows_share_one_ledger_and_second_stop_writes_the_delta(tmp_path: Path) -> None:
    env = {"NDF_USAGE_DIR": str(tmp_path / "ledger")}
    tr = _transcript(tmp_path / "t.jsonl", [_line("m1", "2026-11-14T01:00:00Z", 4)])
    first = usage.record_subagent({"agent_id": "a", "agent_transcript_path": str(tr)}, env)
    assert first is not None and first["usage"]["output_tokens"] == 4
    _transcript(tr, [_line("m1", "2026-11-14T01:00:00Z", 4), _line("m2", "2026-11-14T01:05:00Z", 6)])
    second = usage.record_subagent({"agent_id": "a", "agent_transcript_path": str(tr)}, env)
    assert second is not None and second["usage"]["output_tokens"] == 6 and second["usage"]["input_tokens"] == 0
    assert usage.record_cli({"output_tokens": 2}, {"run": "x"}, env) is not None
    rows, broken = jsonl.read_rows(usage.ledger_path(env))
    assert [r["caller"] for r in rows] == [usage.SUBAGENT, usage.SUBAGENT, usage.CLAUDE_CLI] and broken == 0


def test_unreadable_transcript_writes_unknown_without_raising(tmp_path: Path) -> None:
    env = {"NDF_USAGE_DIR": str(tmp_path)}
    row = usage.record_subagent({"agent_id": "a", "agent_transcript_path": str(tmp_path / "missing.jsonl")}, env)
    assert row is not None and row["usage"] == ud.UNKNOWN
    assert usage.record_subagent({}, env) is None


def test_ledger_path_defaults_to_the_state_dir(tmp_path: Path) -> None:
    got = usage.ledger_path({})
    assert got.name == "usage.jsonl" and got.parent.name == "usage" and str(tmp_path / "state") in str(got)


def test_report_counts_transcript_once_and_the_last_response_missed_by_the_hook(tmp_path: Path) -> None:
    env = {"NDF_USAGE_DIR": str(tmp_path)}
    tr = _transcript(tmp_path / "t.jsonl", [_line("m1", "2026-11-14T01:00:00Z", 4)])
    usage.record_subagent({"agent_id": "a", "agent_transcript_path": str(tr)}, env)
    # 停止の hook の後に最後の応答が書かれた
    _transcript(tr, [_line("m1", "2026-11-14T01:00:00Z", 4), _line("m2", "2026-11-14T01:01:00Z", 6)])
    queries: list[str] = []
    got = usage.report(usage.ledger_path(env), date(2026, 11, 14), date(2026, 11, 14), lambda q: queries.append(q) or 3)
    assert got["usage"]["output_tokens"] == 10 and got["closed_issues"] == 3 and queries[0].startswith("closed:2026-11-14T00")
    assert got["since"].endswith("+09:00") and got["unknown_rows"] == 0
