"""使用量の帳簿の集計（lib/usage_aggregate.py と usage-report.py。#1953）。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
import usage_aggregate as ua  # noqa: E402
import usage_ledger  # noqa: E402

U = {"input_tokens": 10, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 20, "output_tokens": 5}


def put(d: Path, runtime: str, rows: list[dict], repo: str = "acme__w") -> None:
    p = d / runtime / f"{repo}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        for r in rows:
            f.write(json.dumps({"schema": 2, "runtime": runtime, "status": "ok", **r}) + "\n")


def report(d: Path, ledger: str, **kw) -> dict:
    return ua.usage_report(ledger, root=d, repo=kw.pop("repo", "acme__w"), directory=d, **kw)


def test_one_ledger_per_report_and_caller_groups(tmp_path):
    put(tmp_path, "claude", [{"source": "supervise", "kind": "work", "usage": U, "at": "2026-10-01T00:00:00Z"}])
    put(
        tmp_path,
        "codex",
        [
            {"source": "cross-review", "kind": "review", "usage": {"input_tokens": 7}, "at": "2026-10-01T00:00:00Z"},
            {"source": "cross-review", "kind": "review", "usage": "unknown", "at": "2026-10-01T01:00:00Z"},
            {"source": "external-ai", "kind": "run", "usage": {"input_tokens": 3}, "at": "2026-10-01T02:00:00Z"},
        ],
    )
    out = report(tmp_path, "codex")
    assert out["totals"] == {"calls": 3, "unknown_calls": 1, "usage": {"input_tokens": 10}}  # claude の行は入らない（I7）
    assert [(g["key"], g["calls"]) for g in out["groups"]] == [("cross-review:review", 2), ("external-ai:run", 1)]
    assert "claude" not in out


def test_period_includes_both_ends_in_jst(tmp_path):
    rows = [
        {"source": "s", "kind": "k", "usage": {"credits": 1}, "at": "2026-09-30T14:59:59Z"},  # JST 9/30 23:59
        {"source": "s", "kind": "k", "usage": {"credits": 2}, "at": "2026-09-30T15:00:00Z"},  # JST 10/1 0:00
        {"source": "s", "kind": "k", "usage": {"credits": 4}, "at": "2026-10-02T14:59:59Z"},  # JST 10/2 23:59
        {"source": "s", "kind": "k", "usage": {"credits": 8}, "at": "2026-10-02T15:00:00Z"},  # JST 10/3
    ]
    put(tmp_path, "kiro", rows)
    out = report(tmp_path, "kiro", since="2026-10-01", until="2026-10-02", by="day")
    assert out["totals"]["usage"] == {"credits": 6}
    assert [g["key"] for g in out["groups"]] == ["2026-10-01", "2026-10-02"]


def test_empty_ledger_is_zero(tmp_path):
    out = report(tmp_path, "agy")
    assert out["totals"] == {"calls": 0, "unknown_calls": 0, "usage": {}} and out["groups"] == []


def test_v1_rows_count_as_claude(tmp_path):
    (tmp_path / "acme__w.jsonl").write_text(
        json.dumps({"source": "supervise", "kind": "work", "usage": U, "at": "2026-10-01T00:00:00Z"}) + "\n"
    )
    out = report(tmp_path, "claude")
    assert out["totals"]["usage"]["input_tokens"] == 10 and out["claude"]["total"]["usage"]["input_tokens"] == 10


def sub(agent: str, parent: str, usage: dict, at: str = "2026-10-01T00:00:00Z") -> dict:
    return {"source": "subagent", "kind": "subagent", "agent_id": agent, "parent_session_id": parent, "usage": usage, "at": at}


def test_claude_views_count_each_call_once(tmp_path):
    """full の claude -p（usage は親だけ、modelUsage は中のサブエージェントを含む）とサブエージェントの行・会話の記録。"""
    mu = {"m": {"inputTokens": 10 + 4 + 6, "cacheReadInputTokens": 100 + 40, "cacheCreationInputTokens": 20, "outputTokens": 5 + 2 + 3}}
    full = {"source": "supervise", "kind": "full", "session_id": "S", "usage": U, "model_usage": mu, "at": "2026-10-01T00:00:00Z"}
    a1 = {"input_tokens": 4, "cache_read_input_tokens": 40, "output_tokens": 2}
    put(tmp_path, "claude", [full, sub("a1", "S", a1)])
    projects = tmp_path / "projects"
    conv = projects / "p" / "S.jsonl"
    conv.parent.mkdir(parents=True)
    conv.write_text(json.dumps({"timestamp": "2026-10-01T00:00:00Z", "cwd": "/gone/w", "entrypoint": "sdk-cli"}) + "\n")
    agents = projects / "p" / "S" / "subagents"
    agents.mkdir(parents=True)
    line = {"type": "assistant", "message": {"id": "x", "usage": {"input_tokens": 4, "cache_read_input_tokens": 40, "output_tokens": 2}}}
    (agents / "agent-a1.jsonl").write_text(json.dumps(line) + "\n")  # 帳簿にもある a1（2 回数えない）
    line2 = {"type": "assistant", "message": {"id": "y", "usage": {"input_tokens": 6, "output_tokens": 3}}}
    (agents / "agent-a2.jsonl").write_text(json.dumps(line2) + "\n")  # hook の効かなかった a2
    out = report(tmp_path, "claude", projects=projects)
    c = out["claude"]
    assert c["subagent_only"]["calls"] == 2 and c["subagent_only"]["usage"]["input_tokens"] == 10
    total = c["total"]["usage"]
    assert total["input_tokens"] == 20 and total["cache_read_input_tokens"] == 140 and total["output_tokens"] == 10
    assert c["transcript_only"]["calls"] == 1  # a2 だけ（S は帳簿にある）
    assert "より前の" in c["uncountable_note"]


def test_model_usage_residual_counts_unhooked_subagents(tmp_path):
    mu = {"m": {"inputTokens": 30, "cacheCreationInputTokens": 25, "outputTokens": 5}}
    row = {"source": "supervise", "kind": "full", "session_id": "S", "usage": U, "model_usage": mu, "at": "2026-10-01T00:00:00Z"}
    put(tmp_path, "claude", [row])
    c = report(tmp_path, "claude")["claude"]
    assert c["subagent_only"]["calls"] == 0
    assert c["total"]["usage"]["input_tokens"] == 30 and c["total"]["usage"]["cache_creation.ephemeral_5m_input_tokens"] == 5


def test_sdk_cli_transcripts_not_in_the_ledger_are_transcript_only(tmp_path):
    projects = tmp_path / "projects"
    conv = projects / "p" / "T.jsonl"
    conv.parent.mkdir(parents=True)
    rows = [
        {"timestamp": "2026-10-01T00:00:00Z", "cwd": "/gone/w", "entrypoint": "sdk-cli"},
        {"type": "assistant", "message": {"id": "z", "usage": {"input_tokens": 9}}},
    ]
    conv.write_text("".join(json.dumps(r) + "\n" for r in rows))
    (projects / "p" / "U.jsonl").write_text(json.dumps({"timestamp": "2026-10-01T00:00:00Z", "cwd": "/gone/w", "entrypoint": "cli"}) + "\n")
    c = report(tmp_path, "claude", projects=projects)["claude"]
    assert c["transcript_only"]["usage"]["input_tokens"] == 9 and c["total"]["calls"] == 1  # conductor（cli）は数えない


def fake_gh(tmp_path: Path, monkeypatch, body: str) -> None:
    b = tmp_path / "bin"
    b.mkdir(exist_ok=True)
    (b / "gh").write_text(f"#!/bin/sh\n{body}\n")
    (b / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{b}{os.pathsep}{os.environ['PATH']}")


def github_project(root: Path, primary: str = "github") -> None:
    (root / ".ndf").mkdir(exist_ok=True)
    (root / ".ndf" / "project.json").write_text(json.dumps({"issues": {"primary": primary}}))


def test_closed_issues_from_gh(tmp_path, monkeypatch):
    github_project(tmp_path)
    fake_gh(tmp_path, monkeypatch, "echo '{\"total_count\": 7}'")
    assert report(tmp_path, "codex", since="2026-10-01", until="2026-10-02")["closed_issues"] == 7


@pytest.mark.parametrize("primary,gh", [("redmine", "echo '{\"total_count\": 7}'"), ("github", "exit 1")])
def test_closed_issues_unknown(tmp_path, monkeypatch, primary, gh):
    github_project(tmp_path, primary)
    fake_gh(tmp_path, monkeypatch, gh)
    assert report(tmp_path, "codex")["closed_issues"] == "unknown"


def test_milestone_sets_the_period_and_count(tmp_path, monkeypatch):
    github_project(tmp_path)
    ms = [
        {"number": 17, "title": "トークン", "created_at": "2026-10-01T00:00:00Z", "closed_at": "2026-10-02T00:00:00Z", "closed_issues": 4}
    ]
    fake_gh(tmp_path, monkeypatch, f"echo '{json.dumps(ms)}'")
    put(tmp_path, "agy", [{"source": "s", "kind": "k", "usage": {"input_tokens": 1}, "at": "2026-10-01T03:00:00Z"}])
    put(tmp_path, "agy", [{"source": "s", "kind": "k", "usage": {"input_tokens": 2}, "at": "2026-10-05T03:00:00Z"}])
    out = report(tmp_path, "agy", milestone="17")
    assert out["period"] == {"since": "2026-10-01", "until": "2026-10-02"} and out["closed_issues"] == 4
    assert out["totals"]["usage"] == {"input_tokens": 1}


def test_cli_argument_errors_exit_2(tmp_path):
    for args in (["--ledger", "other"], ["--ledger", "codex", "--since", "2026-10"]):
        p = subprocess.run([sys.executable, str(SCRIPTS / "usage-report.py"), *args], capture_output=True, text=True)
        assert p.returncode == 2


def test_cli_table_lists_the_three_claude_values(tmp_path, monkeypatch):
    monkeypatch.setenv("NDF_USAGE_DIR", str(tmp_path / "u"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    put(tmp_path / "u", "claude", [{"source": "supervise", "kind": "work", "usage": U, "at": "2026-10-01T00:00:00Z"}], repo="unknown")
    p = subprocess.run(
        [sys.executable, str(SCRIPTS / "usage-report.py"), "--ledger", "claude", "--format", "table", "--root", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert p.returncode == 0, p.stderr
    assert "| supervise:work | 1 | 0 |" in p.stdout
    for label in ("サブエージェントだけ", "全体", "会話の記録だけ", "数えられない"):
        assert f"- {label}" in p.stdout


def test_step_totals_split_by_runtime_and_skip_supervise_claude(tmp_path):
    rows = [
        {"source": "cross-review", "kind": "review", "plan": "P", "step": "d", "usage": {"input_tokens": 5}, "at": usage_ledger.now_utc()}
    ]
    put(tmp_path, "codex", rows)
    put(tmp_path, "claude", [{**rows[0], "source": "supervise"}])
    put(tmp_path, "kiro", [{**rows[0], "step": "other", "usage": {"credits": 1}}])
    got = ua.step_totals("P", "d", 0, tmp_path)
    assert list(got) == ["codex"] and got["codex"]["usage"] == {"input_tokens": 5}
    assert ua.runtimes_text([{"llm_runtimes": got}, {}]) == "codex 1 回（input_tokens 5）"
    assert ua.runtimes_text([]) == "無し"
