"""計測の 1 行・古い記録の片付け・`ndf hooks stats`（M3-H10・IH9）。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from ndf.domains.hooks import cli, metrics

from ._hooks import bash, call_entry, deny_reason, records, sample, state_dir, stop


def test_every_entry_writes_one_line(tmp_path):
    call_entry("claude", "PreToolUse", bash(tmp_path, "sleep 999"))
    call_entry("codex", "Stop", stop(tmp_path, "完了", runtime="codex"))
    call_entry("claude", "Nope", "{}")
    lines = records()
    assert [(r["runtime"], r["event"], r["path"]) for r in lines] == [("claude", "PreToolUse", "entry"), ("codex", "Stop", "entry"), ("claude", "Nope", "entry")]
    first = lines[0]
    assert first["verdict"] == "deny" and first["tool"] == "Bash" and first["total_ms"] >= 0
    assert {"merge-production", "sleep"} <= {g["name"] for g in first["guards"]}
    assert first["inject_chars"] > 0 and first["pushbacks"] == 0


def test_only_session_start_prunes(tmp_path, monkeypatch):
    pruned = []
    monkeypatch.setattr(metrics, "prune", lambda *a, **k: pruned.append(a) or 0)
    call_entry("claude", "PreToolUse", bash(tmp_path, "ls"))
    call_entry("claude", "Stop", stop(tmp_path, "完了"))
    assert pruned == []
    call_entry("claude", "SessionStart", sample("claude", "SessionStart", tmp_path))
    assert len(pruned) == 1 and pruned[0][1] == 14


def test_prune_removes_days_older_than_the_window(tmp_path):
    now = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    for days in (0, 13, 14, 15, 30):
        metrics.write_line(tmp_path, {"event": "x"}, now - timedelta(days=days))
    (tmp_path / "hooks" / "notes").mkdir()
    (tmp_path / "hooks" / "other.jsonl").write_text("", encoding="utf-8")
    assert metrics.prune(tmp_path, 14, now) == 2
    left = sorted(p.name for p in (tmp_path / "hooks").glob("*.jsonl"))
    assert left == ["2026-10-31.jsonl", "2026-11-01.jsonl", "2026-11-14.jsonl", "other.jsonl"]


def test_metrics_write_failure_does_not_change_the_verdict(tmp_path, monkeypatch):
    blocker = tmp_path / "blocker"
    blocker.write_text("ファイルの下には書けない", encoding="utf-8")
    monkeypatch.setattr(metrics, "_day_file", lambda state, now: blocker / "x.jsonl")
    assert deny_reason(call_entry("claude", "PreToolUse", bash(tmp_path, "sleep 999"))) is not None
    assert metrics.write_line(state_dir(), {"x": 1}) is False


def test_stats_reports_percentiles_counts_and_injection():
    recs = [
        {"runtime": "claude", "event": "Stop", "total_ms": ms, "verdict": v, "inject_chars": c, "guards": [{"name": "stop-wait", "ms": ms / 2, "kind": v}]}
        for ms, v, c in [(10, "pass", 0), (20, "pushback", 100), (30, "limited", 0), (40, "pass", 0)]
    ]
    got = metrics.stats(recs)
    stop_row = got["events"]["claude:Stop"]
    assert stop_row["p50_ms"] == 20 and stop_row["p95_ms"] == 40 and stop_row["pushbacks"] == 1 and stop_row["limited"] == 1
    assert stop_row["inject_max"] == 100 and got["guards"]["stop-wait"]["p95_ms"] == 20


def test_stats_command_reads_the_records(tmp_path, capsys):
    call_entry("claude", "PreToolUse", bash(tmp_path, "sleep 999"))
    assert cli.main(["stats", "--days", "1"]) == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["status"] == "ok" and out["items"][0]["events"]["claude:PreToolUse"]["denies"] == 1


def test_percentile_nearest_rank():
    assert metrics.percentile([], 95) is None
    assert metrics.percentile(range(1, 21), 95) == 19 and metrics.percentile([5], 50) == 5
