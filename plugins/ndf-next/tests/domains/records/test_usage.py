"""使用量の帳簿: 応答ごとの数え方（#1979）・JST の両端（#1978）・置き場（#2575）・停止の差（#2576）。"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import git_sandbox
import pytest

from ndf import cli
from ndf.adapters import github
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


def test_state_place_dir_prefers_env_then_state_and_never_falls_back_to_tmp(tmp_path: Path) -> None:
    assert ud.state_place_dir({"V": str(tmp_path / "x")}, "V", tmp_path, "usage") == tmp_path / "x"
    assert ud.state_place_dir({}, "V", tmp_path, "usage") == tmp_path / "usage"
    with pytest.raises(LookupError):
        ud.state_place_dir({}, "V", None, "usage")


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


# 現状固定（I-003 の前）: reconcile の分岐ごとの振る舞い
_RECONCILE_ROWS = [
    {"at": "2026-11-14T01:00:00Z", "agent_id": "a", "usage": {"output_tokens": 100}},
    {"at": "2026-11-14T01:00:00Z", "agent_id": "b", "usage": {"output_tokens": 2}},
    {"at": "2026-11-13T01:00:00Z", "agent_id": "b", "usage": {"output_tokens": 50}},
    {"at": "2026-11-13T01:00:00Z", "usage": "unknown"},
    {"usage": {"output_tokens": 7}},
    {"at": "2026-11-14T02:00:00Z", "agent_id": 7, "usage": {"input_tokens": 1}},
    {"at": "2026-11-14T03:00:00Z", "usage": None},
]
_RECONCILE_TRANSCRIPTS = {"a": ud.Usage(0, 3, 0, 0), "b": ud.UNKNOWN, "": ud.Usage(9, 9, 9, 9), "z": ud.Usage(1, 0, 0, 0)}


def test_reconcile_within_a_period_skips_rows_outside_or_without_time_and_ignores_the_empty_agent() -> None:
    a = datetime(2026, 11, 14, 0, 0, tzinfo=timezone.utc)
    b = datetime(2026, 11, 14, 23, 0, tzinfo=timezone.utc)
    assert ud.reconcile(_RECONCILE_ROWS, _RECONCILE_TRANSCRIPTS, a, b) == (ud.Usage(2, 5, 0, 0), 1)


def test_reconcile_without_a_period_counts_every_row_not_covered_by_a_transcript() -> None:
    assert ud.reconcile(_RECONCILE_ROWS, _RECONCILE_TRANSCRIPTS) == (ud.Usage(2, 62, 0, 0), 2)


def test_reconcile_with_no_transcripts_counts_rows_since_the_start_only() -> None:
    a = datetime(2026, 11, 14, 0, 0, tzinfo=timezone.utc)
    assert ud.reconcile(_RECONCILE_ROWS, {}, a, None) == (ud.Usage(1, 102, 0, 0), 1)
    assert ud.reconcile([], {}) == (ud.Usage(), 0)


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
    assert second is not None and second["usage"]["output_tokens"] == 6 and second["usage"]["input_tokens"] == 1
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


class _SearchApi:
    """GraphQL の検索だけに答えるクライアント（件数を文字列で返す）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def graphql(self, query: str, variables: dict) -> dict:
        self.calls.append((query, variables))
        return {"search": {"issueCount": "4"}}


def test_usage_report_counts_closed_issues_of_the_origin_repo_with_one_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], last_result
) -> None:
    """現状固定（I-005 の前）: `ndf records usage report` が閉じた課題を数える検索の形と、件数の読み方。"""
    git_sandbox.isolate_git(tmp_path, monkeypatch)
    root = tmp_path / "r"
    git_sandbox.sh(tmp_path, "init", "-q", str(root))
    git_sandbox.sh(root, "remote", "add", "origin", "https://github.com/o/r.git")
    monkeypatch.setenv("NDF_USAGE_DIR", str(tmp_path / "ledger"))
    api = _SearchApi()
    monkeypatch.setattr(github, "client", lambda *a, **k: api)
    code = cli.main(["records", "--cwd", str(root), "usage", "report", "--since", "2026-11-14", "--until", "2026-11-14"])
    got = last_result(capsys.readouterr().out)
    assert code == 0 and got["status"] == "ok"
    assert got["items"][0]["closed_issues"] == 4
    assert api.calls == [
        (
            "query($q: String!) { search(query: $q, type: ISSUE) { issueCount } }",
            {"q": "repo:o/r is:issue closed:2026-11-14T00:00:00+09:00..2026-11-14T23:59:59+09:00"},
        )
    ]


# 現状固定（I-013 の前）: transcript_usage が会話の記録を読むときの行の扱い（usage._records）
def test_transcript_usage_returns_unknown_when_the_file_cannot_be_read(tmp_path: Path) -> None:
    assert usage.transcript_usage(tmp_path / "none.jsonl") == ud.UNKNOWN
    assert usage.transcript_usage(tmp_path) == ud.UNKNOWN


def test_transcript_usage_skips_blank_broken_and_non_object_lines(tmp_path: Path) -> None:
    lines = [
        json.dumps(_line("m1", "2026-11-14T01:00:00Z", 4)),
        "",
        '["usage"]',
        '{書きかけ "usage"',
        '"usage"',
        json.dumps(_line("m2", "2026-11-14T01:00:00Z", 6)),
        json.dumps({"message": {"id": "m5"}}),
    ]
    path = tmp_path / "t.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert usage.transcript_usage(path) == ud.Usage(2, 10, 0, 0)


# NOTE: 現状固定。行の文字列に `"usage"` が無ければ JSON として読まない。キーをエスケープで書いた行（`"usage"`）は数えない
def test_transcript_usage_does_not_read_lines_without_the_literal_usage_key(tmp_path: Path) -> None:
    escaped = '{"timestamp": "2026-11-14T01:00:00Z", "message": {"id": "m3", "\\u0075sage": {"output_tokens": 100}}}'
    assert json.loads(escaped)["message"]["usage"] == {"output_tokens": 100}
    path = tmp_path / "t.jsonl"
    path.write_text(json.dumps(_line("m1", "2026-11-14T01:00:00Z", 4)) + "\n" + escaped + "\n", encoding="utf-8")
    assert usage.transcript_usage(path) == ud.Usage(1, 4, 0, 0)


def test_transcript_usage_raises_on_bytes_that_are_not_utf8(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_bytes(b'\xff\xfe "usage"\n')
    with pytest.raises(UnicodeDecodeError):
        usage.transcript_usage(path)
