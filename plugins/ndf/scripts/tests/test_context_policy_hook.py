"""再起動の方針の hook・記録・受け渡し・集計（#2262）。hook は `hook.py token-guard` の子プロセスで動かす。"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_context_policy import git_repo
from test_token_guard import agent, denied, relay_dir, run, skill, transcript  # noqa: F401  relay_dir は fixture

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
import context_decisions as cd  # noqa: E402

CLI = SCRIPTS / "context-policy.py"
STATUSLINE = SCRIPTS / "statusline.sh"
SKILL_STATS = SCRIPTS.parent / "skills" / "skill-stats" / "scripts" / "skill-stats.py"


@pytest.fixture()
def state(tmp_path):
    return tmp_path / "plugin-data"


@pytest.fixture()
def ctx(tmp_path):
    return tmp_path / "ctx"


def env_of(ctx: Path, **more) -> dict:
    return {"NDF_CONTEXT_DIR": str(ctx), **more}


def rows(ctx: Path) -> list[dict]:
    return cd.read_decisions(None, {"NDF_CONTEXT_DIR": str(ctx)})


def trial_repo(tmp_path, **trial) -> Path:
    return git_repo(tmp_path, {"version": 1, "policy": "trial", **({"trial": trial} if trial else {})})


# ---------------------------------------------------------------- hook（条件 1〜3・5〜7・9・11・14・15）


def test_default_policy_keeps_todays_text(tmp_path, state, ctx):
    tp = transcript(tmp_path, 200_001)
    reason = denied(run(skill(tp), state, env_of(ctx)))
    assert reason.startswith("会話の文脈が 200001 トークンで、上限 200000 を超えた。")
    assert "試行方式" not in reason
    assert denied(run(skill(transcript(tmp_path, 200_000, name="t2.jsonl"), session="s2"), state, env_of(ctx))) is None


def test_trial_passes_cut_point_below_decide_from(tmp_path, state, ctx):
    tp = transcript(tmp_path, 199_999)
    root = trial_repo(tmp_path)
    assert denied(run(skill(tp, name="ndf:cross-review", cwd=str(root)), state, env_of(ctx))) is None
    (r,) = rows(ctx)
    assert (r["policy"], r["verdict"], r["band"], r["stage"]) == ("trial", "継続", "継続帯", "skill:cross-review")


@pytest.mark.parametrize("total,deny", [(300_000, False), (400_001, True)])
def test_trial_limit_from_env(tmp_path, state, ctx, total, deny):
    reason = denied(run(skill(transcript(tmp_path, total)), state, env_of(ctx, NDF_CONTEXT_POLICY="trial")))
    assert bool(reason) == deny
    if deny:
        assert "上限 400000（再起動の方針: 試行方式、理由: 運用上限）を超えた" in reason


def test_trial_small_window_denies_by_capacity(tmp_path, state, ctx):
    (ctx / "sessions").mkdir(parents=True)
    (ctx / "sessions" / "s1.window.json").write_text(json.dumps({"window": 300_000, "model": "m"}))
    reason = denied(run(skill(transcript(tmp_path, 250_001)), state, env_of(ctx, NDF_CONTEXT_POLICY="trial")))
    assert "容量（窓 300000 − 余白 50000 = 250000）" in reason
    assert rows(ctx)[-1]["reason"] == "容量"


def test_trial_without_window_records_missing(tmp_path, state, ctx):
    run(skill(transcript(tmp_path, 300_000)), state, env_of(ctx, NDF_CONTEXT_POLICY="trial"))
    r = rows(ctx)[-1]
    assert (r["verdict"], r["basis"], r["window"]) == ("継続", "見込みなし", None)
    assert r["missing"] == ["window", "estimate"]


def test_trial_cost_denial_keeps_denying_under_relay(tmp_path, state, ctx, relay_dir):  # noqa: F811  relay_dir は fixture
    repo = cd.decisions_repo(None)
    est = {
        "restore_context": 10_000,
        "restore_calls": 2,
        "handoff_calls": 1,
        "handoff_output": 100,
        "stage_calls": {"*": 50},
        "stage_expiries": {"*": 2},
        "samples": {"restore": 3, "handoff": 3, "stage:*": 3},
    }
    assert cd.write_cost_estimates(est, repo, {"NDF_CONTEXT_DIR": str(ctx)}) is True
    env = env_of(ctx, NDF_CONTEXT_POLICY="trial", NDF_RELAY_DIR=str(relay_dir))
    tp = transcript(tmp_path, 300_000)
    payload = agent(tp, cwd=str(tmp_path))  # git の外（_local）で見込みを読む
    first, second = denied(run(payload, state, env)), denied(run(payload, state, env))
    assert first and second and "理由: 費用" in second
    assert [r["relay"] for r in rows(ctx)] == [True, True]


def test_unreadable_context_passes_and_records(tmp_path, state, ctx):
    assert denied(run(skill(tmp_path / "missing.jsonl"), state, env_of(ctx))) is None
    r = rows(ctx)[-1]
    assert (r["verdict"], r["basis"], r["context"]) == ("継続", "値なし", None)


def test_guard_off_and_subagent_neither_judge_nor_record(tmp_path, state, ctx):
    tp = transcript(tmp_path, 500_000)
    assert denied(run(skill(tp), state, env_of(ctx, NDF_CONTEXT_GUARD="0", NDF_CONTEXT_POLICY="trial"))) is None
    assert denied(run(skill(tp, agent_id="a1"), state, env_of(ctx))) is None
    assert rows(ctx) == []


def test_one_row_per_launch_with_the_columns(tmp_path, state, ctx):
    run(agent(transcript(tmp_path, 1_000)), state, env_of(ctx))
    (r,) = rows(ctx)
    for k in ("model", "runtime", "policy", "decide_from", "limit", "margin", "stage", "context", "verdict", "work", "at"):
        assert k in r
    assert (r["work"], r["stage"], r["runtime"], r["source"]) == ("#829 #830", "agent:設計", "claude", "hook")


def test_unwritable_record_does_not_change_the_verdict(tmp_path, state):
    bad = tmp_path / "file"
    bad.write_text("")
    env = {"NDF_CONTEXT_DIR": str(bad / "x")}
    assert denied(run(skill(transcript(tmp_path, 200_001)), state, env))
    assert denied(run(skill(transcript(tmp_path, 100, name="t2.jsonl"), session="s2"), state, env)) is None


def test_broken_setting_is_told_once(tmp_path, state, ctx):
    root = git_repo(tmp_path, {"policy": "fast"})
    tp = transcript(tmp_path, 1_000)
    out = run(skill(tp, cwd=str(root)), state, env_of(ctx))
    assert "context.json" in json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]
    assert run(skill(tp, cwd=str(root)), state, env_of(ctx)).stdout.strip() == ""


# ---------------------------------------------------------------- 3 つの読み口（条件 2・8、I2）


def cli(*args, ctx: Path, stdin: str = "", cwd=None, **env):
    e = {k: v for k, v in os.environ.items() if not k.startswith("NDF_")} | {"NDF_CONTEXT_DIR": str(ctx)} | env
    return subprocess.run([sys.executable, str(CLI), *args], input=stdin, capture_output=True, text=True, env=e, cwd=cwd, timeout=30)


def statusline(ctx: Path, sid: str, total: int, window: int) -> str:
    payload = {"session_id": sid, "model": {"id": "m"}, "context_window": {"total_input_tokens": total, "context_window_size": window}}
    e = {"PATH": os.environ["PATH"], "HOME": str(ctx), "NDF_CONTEXT_DIR": str(ctx), "CLAUDE_CODE_USE_BEDROCK": "1"}
    return subprocess.run(["bash", str(STATUSLINE)], input=json.dumps(payload), capture_output=True, text=True, env=e).stdout


def test_three_readers_follow_the_same_setting(tmp_path, state, ctx):
    root = trial_repo(tmp_path, limit=300_000)
    tp = transcript(tmp_path, 1_000)
    run(skill(tp, cwd=str(root)), state, env_of(ctx))
    cli("publish", ctx=ctx, stdin=json.dumps({"session_id": "s2", "cwd": str(root)}))
    hook_limit = rows(ctx)[-1]["limit"]
    published = json.loads((ctx / "sessions" / "s2.applied.json").read_text())["limit"]
    limit_of, meta = cd.record_limits(["s1"], None, {"NDF_CONTEXT_DIR": str(ctx)})
    assert hook_limit == published == limit_of("s1") == 300_000
    assert "\033[0;31m" in statusline(ctx, "s2", 301_000, 1_000_000)
    assert "\033[0;33m" in statusline(ctx, "s2", 250_000, 1_000_000)
    (root / ".ndf" / "context.json").unlink()
    run(skill(transcript(tmp_path, 1_000, name="t3.jsonl"), session="s3", cwd=str(root)), state, env_of(ctx))
    cli("publish", ctx=ctx, stdin=json.dumps({"session_id": "s2", "cwd": str(root)}))
    assert rows(ctx)[-1]["limit"] == 200_000
    assert json.loads((ctx / "sessions" / "s2.applied.json").read_text())["policy"] == "current"
    assert cd.record_limits(["s3"], None, {"NDF_CONTEXT_DIR": str(ctx)})[0]("s3") == 200_000
    back = statusline(ctx, "s2", 250_000, 1_000_000)
    assert "\033[0;31m" not in back and "\033[0;33m" not in back


def test_statusline_writes_the_window(ctx):
    statusline(ctx, "abc", 1_000, 1_000_000)
    assert json.loads((ctx / "sessions" / "abc.window.json").read_text())["window"] == 1_000_000


def test_resolve_cli_shows_sources(tmp_path, ctx):
    out = json.loads(cli("resolve", "--root", str(trial_repo(tmp_path)), ctx=ctx, NDF_CONTEXT_LIMIT="350000").stdout)
    item = out["items"][0]
    assert (item["policy"], item["limit"], item["sources"]["limit"], item["sources"]["policy"]) == ("trial", 350_000, "env", "config")


# ---------------------------------------------------------------- 記録・集計（条件 12・13、I12〜I14）


def test_record_marks_and_restart(tmp_path, ctx):
    ok = cli("record", "--event", "引継ぎ開始", "--work", "#7", ctx=ctx, cwd=str(tmp_path))
    assert ok.returncode == 0
    assert cli("record", "--event", "途中", "--work", "#7", ctx=ctx, cwd=str(tmp_path)).returncode == 2
    assert cli("record", "--verdict", "再起動", "--reason", "気分", "--work", "#7", ctx=ctx, cwd=str(tmp_path)).returncode == 2
    assert cli("record", "--verdict", "再起動", "--reason", "独立性", "--work", "#7", ctx=ctx, cwd=str(tmp_path)).returncode == 0
    mark, restart = rows(ctx)
    assert (mark["event"], mark["verdict"], mark["stage"]) == ("引継ぎ開始", None, None)
    assert (restart["verdict"], restart["reason"]) == ("再起動", "独立性")


def test_record_row_columns_and_stdout(tmp_path, ctx):
    # CLI の記録の行の列（現状固定。#2422）。標準出力の行と記録の行は同じ
    out = cli(
        "record",
        "--verdict",
        "再起動",
        "--reason",
        "独立性",
        "--work",
        "#7",
        "--context",
        "1234",
        ctx=ctx,
        cwd=str(tmp_path),
        CLAUDE_CODE_SESSION_ID="s9",
    )
    (row,) = rows(ctx)
    assert json.loads(out.stdout)["items"] == [row]
    picked = {
        k: row[k] for k in ("runtime", "source", "session_id", "work", "context", "stage", "verdict", "reason", "band", "event", "policy")
    }
    assert picked == {
        "runtime": "claude",
        "source": "record",
        "session_id": "s9",
        "work": "#7",
        "context": 1234,
        "stage": "cut",
        "verdict": "再起動",
        "reason": "独立性",
        "band": None,
        "event": "判定",
        "policy": "current",
    }
    cli("record", "--event", "復元完了", "--work", "#7", "--runtime", "codex", ctx=ctx, cwd=str(tmp_path), CLAUDE_CODE_SESSION_ID="s9")
    mark = rows(ctx)[-1]
    assert (mark["runtime"], mark["session_id"], mark["event"], mark["stage"], mark["reason"]) == ("codex", None, "復元完了", None, None)


def call(ts: str, mid: str, out: int = 10, read: int = 1_000, model: str = "claude-opus-5-5") -> dict:
    usage = {"input_tokens": 100, "output_tokens": out, "cache_read_input_tokens": read, "cache_creation_input_tokens": 0}
    return {"type": "assistant", "timestamp": ts, "message": {"id": mid, "model": model, "usage": usage, "content": []}}


def write_transcript(path: Path, lines: list[dict]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n")
    return str(path)


def hook_row(sid: str, tp: str, at: str, **more) -> dict:
    return {"at": at, "session_id": sid, "transcript": tp, "work": "#7", "event": "判定", "policy": "current", "limit": 200_000, **more}


def mark(event: str, at: str, sid=None) -> dict:
    return {"at": at, "session_id": sid, "work": "#7", "event": event, "policy": "current", "verdict": None}


def load_cli():
    spec = importlib.util.spec_from_file_location("context_policy_cli", CLI)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_same_message_and_ledger_are_counted_once(tmp_path):
    m = load_cli()
    a = call("2026-11-01T00:00:00Z", "m1")
    tp = write_transcript(tmp_path / "s1.jsonl", [a, a, a, call("2026-11-01T00:01:00Z", "m2")])
    write_transcript(tmp_path / "s1" / "subagents" / "agent-x.jsonl", [call("2026-11-01T00:00:30Z", "m3")])
    tp_p = write_transcript(tmp_path / "p1.jsonl", [call("2026-11-01T00:00:40Z", "m4")])
    ledger = [{"session_id": "p1", "parent_session_id": "s1", "at": "2026-11-01T00:00:50Z", "usage": a["message"]["usage"], "seconds": 3}]
    recs = [hook_row("s1", tp, "2026-11-01T00:00:00Z"), hook_row("p1", tp_p, "2026-11-01T00:00:40Z")]
    out = m.report_work("#7", recs, ledger)
    calls = sum(p["calls"] for p in out["parts"].values())
    assert out["parts"]["conductor"]["calls"] == 2 and out["parts"]["ndf"]["calls"] == 2
    assert calls == 4  # m1・m2・サブエージェントの m3・帳簿の 1 行（p1 の会話の記録は読まない）


def test_subagent_in_the_ledger_is_not_read_from_its_transcript(tmp_path):
    # SubagentStop が帳簿へ書いたサブエージェント（session_id = agent_id）は、会話の記録からは足さない（PR 2414 の指摘）
    m = load_cli()
    a = call("2026-11-01T00:00:00Z", "m1")
    tp = write_transcript(tmp_path / "s1.jsonl", [a])
    write_transcript(tmp_path / "s1" / "subagents" / "agent-ag1.jsonl", [call("2026-11-01T00:00:30Z", "m3")])
    ledger = [
        {"session_id": "ag1", "parent_session_id": "s1", "agent_id": "ag1", "at": "2026-11-01T00:00:50Z", "usage": a["message"]["usage"]}
    ]
    out = m.report_work("#7", [hook_row("s1", tp, "2026-11-01T00:00:00Z")], ledger)
    assert out["parts"]["ndf"]["calls"] == 1


def test_same_message_takes_the_last_usage(tmp_path):
    # ストリーミングで後の行ほど output_tokens が増える。最初の usage で固定せず、最後の値を 1 回だけ数える
    tp = write_transcript(tmp_path / "s1.jsonl", [call("2026-11-01T00:00:00Z", "m1", out=1), call("2026-11-01T00:00:05Z", "m1", out=900)])
    calls, _, _ = cd.read_calls(tp)
    assert len(calls) == 1 and calls[0]["out"] == 900 and calls[0]["ts"] == cd.parse_ts("2026-11-01T00:00:00Z")


def test_work_report_counts_only_the_calls_while_the_work_is_current(tmp_path):
    # 同じセッションで #7 の後に #8 を扱うと、#7 は #8 の行より前の呼び出しだけ、#8 は後の呼び出しだけを数える
    m = load_cli()
    tp = write_transcript(tmp_path / "s1.jsonl", [call("2026-11-01T00:00:00Z", "m1"), call("2026-11-01T01:00:00Z", "m2")])
    recs = [hook_row("s1", tp, "2026-11-01T00:00:00Z"), {**hook_row("s1", tp, "2026-11-01T00:30:00Z"), "work": "#8"}]
    one, two = m.report_work("#7", recs), m.report_work("#8", recs)
    assert sum(p["calls"] for p in one["parts"].values()) == 1 and sum(p["calls"] for p in two["parts"].values()) == 1


def test_marks_split_restore_and_handoff(tmp_path):
    m = load_cli()
    first = {"type": "user", "message": {"content": "/goal /ndf:development-workflow .ndf/handoff/issue-7.md の続きから"}}
    lines = [first] + [call(f"2026-11-01T00:0{i}:00Z", f"m{i}") for i in range(6)]
    tp = write_transcript(tmp_path / "s1.jsonl", lines)

    def parts(restored_at):
        recs = [
            hook_row("s1", tp, "2026-11-01T00:00:00Z"),
            mark("復元完了", restored_at),
            mark("引継ぎ開始", "2026-11-01T00:03:30Z", "s1"),
            mark("引継ぎ開始", "2026-11-01T00:04:30Z", "s1"),
        ]
        return m.report_work("#7", recs)["parts"]

    early, late = parts("2026-11-01T00:01:00Z"), parts("2026-11-01T00:02:00Z")
    assert (early["restore"]["calls"], late["restore"]["calls"]) == (2, 3)
    assert early["handoff"]["calls"] == 1  # 最後の印（00:04:30）から
    assert early["conductor"]["calls"] + early["restore"]["calls"] + early["handoff"]["calls"] == 6


def test_missing_marks_are_unknown_and_counted(tmp_path):
    m = load_cli()
    first = {"type": "user", "message": {"content": "/goal .ndf/handoff/issue-7.md の続きから"}}
    tp = write_transcript(tmp_path / "s1.jsonl", [first, call("2026-11-01T00:00:00Z", "m1")])
    out = m.report_work("#7", [hook_row("s1", tp, "2026-11-01T00:00:00Z", verdict="再起動", reason="手動")])
    assert out["unknown_parts"] == {"restore": 1, "handoff": 1}
    assert out["parts"]["conductor"]["calls"] == 1
    assert out["quality"]["review_fixes"] == "不明" and out["quality"]["missed_requirements"] == "不明"
    assert out["auto_compacts"] == 0


def test_rows_without_policy_are_read_as_current(tmp_path, ctx):
    tp = write_transcript(tmp_path / "s1.jsonl", [call("2026-11-01T00:00:00Z", "m1")])
    old = {"at": "2026-11-01T00:00:00Z", "session_id": "old", "transcript": tp, "work": "#7", "limit": 999}
    new = {**old, "session_id": "new", "policy": "trial", "limit": 400_000}
    cd.append_decision(old, "_local", {"NDF_CONTEXT_DIR": str(ctx)})
    cd.append_decision(new, "_local", {"NDF_CONTEXT_DIR": str(ctx)})
    limit_of, meta = cd.record_limits(["old", "new", "none"], None, {"NDF_CONTEXT_DIR": str(ctx)})
    assert (limit_of("old"), limit_of("new"), limit_of("none")) == (200_000, 400_000, 200_000)
    assert meta["records_without_decisions"] == 1
    assert load_cli().report_work("#7", cd.read_decisions("_local", {"NDF_CONTEXT_DIR": str(ctx)}))["no_policy_rows"] == 1


def test_skill_stats_window_limit_overrides_records(ctx):
    limit_of, meta = cd.record_limits(["a"], 50_000, {"NDF_CONTEXT_DIR": str(ctx)})
    assert limit_of("a") == 50_000 and meta["window_limit_source"] == "cli"


def test_calibrate_needs_three_samples(tmp_path):
    recs = []
    for i in range(3):
        first = {"type": "user", "message": {"content": "/goal .ndf/handoff/issue-7.md"}}
        lines = [first] + [call(f"2026-11-0{i + 1}T00:0{j}:00Z", f"s{i}m{j}", out=50) for j in range(4)]
        tp = write_transcript(tmp_path / f"s{i}.jsonl", lines)
        recs += [
            hook_row(f"s{i}", tp, f"2026-11-0{i + 1}T00:00:00Z", stage="plan", verdict="継続"),
            hook_row(f"s{i}", tp, f"2026-11-0{i + 1}T00:02:00Z", stage="plan", verdict="継続"),
            mark("復元完了", f"2026-11-0{i + 1}T00:00:30Z", f"s{i}"),
            mark("引継ぎ開始", f"2026-11-0{i + 1}T00:02:30Z", f"s{i}"),
        ]
    body = cd.calibrate(recs)
    assert body["samples"]["restore"] == 3 and body["restore_calls"] == 1.0
    assert body["handoff_calls"] == 1.0 and body["handoff_output"] == 50.0
    assert cd.calibrate(recs[:8])["restore_context"] is None


def test_sprint_state_next_records_restart(tmp_path, ctx):
    sprint = tmp_path / "sprint.json"
    sprint.write_text(json.dumps({"name": "x", "issues": [9, 3], "goal_template": "/goal x"}))
    e = {k: v for k, v in os.environ.items() if not k.startswith("NDF_")} | {"NDF_CONTEXT_DIR": str(ctx)}
    p = subprocess.run(
        [sys.executable, str(SCRIPTS / "sprint-state.py"), "next", str(sprint)], capture_output=True, text=True, env=e, cwd=tmp_path
    )
    assert "ndf-next" in p.stdout
    (r,) = rows(ctx)
    assert (r["work"], r["verdict"], r["reason"], r["source"]) == ("#3 #9", "再起動", "カットポイント", "sprint-state")
    assert (ctx / "estimates" / "_local.cost-estimates.json").is_file()


# ---------------------------------------------------------------- calibrate の現状固定（I-001。リファクタリングの前に振る舞いを留める）


def _calibrated(recs, since=None) -> dict:
    body = cd.calibrate(recs, since=since)
    assert isinstance(body.pop("at"), str)
    return body


def _handoff_session(tmp_path, i: int, hours: list[int], first: str = "/goal .ndf/handoff/issue-7.md") -> str:
    lines = [{"type": "user", "message": {"content": first}}]
    lines += [call(f"2026-11-0{i + 1}T{h:02d}:00:00Z", f"s{i}m{h}", out=10 * (h + 1), read=1_000 * (h + 1)) for h in hours]
    return write_transcript(tmp_path / f"s{i}.jsonl", lines)


def test_calibrate_characterizes_empty_rows():
    assert _calibrated([]) == {
        "restore_context": None,
        "restore_calls": None,
        "handoff_calls": None,
        "handoff_output": None,
        "stage_calls": {},
        "stage_expiries": {},
        "samples": {"restore": 0, "handoff": 0},
    }


def test_calibrate_characterizes_stages_marks_and_expiries(tmp_path):
    recs = []
    for i in range(3):
        tp = _handoff_session(tmp_path, i, [0, 1, 3, 4, 5, 7])
        day = f"2026-11-0{i + 1}"
        recs += [
            hook_row(f"s{i}", tp, f"{day}T00:00:00Z", stage="plan", verdict="継続"),
            hook_row(f"s{i}", tp, f"{day}T04:30:00Z", stage="impl", verdict="継続"),
            hook_row(f"s{i}", tp, f"{day}T06:00:00Z", stage="review", verdict="継続"),
            hook_row(f"s{i}", tp, f"{day}T06:30:00Z", stage="test", verdict="継続"),  # 区間に呼び出しが無い
            hook_row(f"s{i}", tp, f"{day}T06:40:00Z", stage="cut", verdict="拒否"),  # 継続でないので区間の境にならない
            hook_row(f"s{i}", tp, f"{day}T06:50:00Z", stage="last", verdict="継続"),  # 次が無いので数えない
            mark("復元完了", f"{day}T03:30:00Z"),  # session_id の無い印は時間の範囲で割り当てる
            mark("引継ぎ開始", f"{day}T01:30:00Z", f"s{i}"),
            mark("引継ぎ開始", f"{day}T04:00:00Z", f"s{i}"),  # 最後の印から数える
        ]
    assert _calibrated(recs) == {
        "restore_context": 4100.0,
        "restore_calls": 3.0,
        "handoff_calls": 3.0,
        "handoff_output": 190.0,
        "stage_calls": {"plan": 4.0, "*": 2.5, "impl": 1.0},
        "stage_expiries": {"plan": 1.0, "*": 0.5, "impl": 0.0},
        "samples": {"restore": 3, "handoff": 3, "stage:plan": 3, "stage:*": 6, "stage:impl": 3},
    }


def test_calibrate_characterizes_since_and_unmatched_sessions(tmp_path):
    recs = []
    for i in range(3):
        tp = _handoff_session(tmp_path, i, [1, 2], first="普通の始まり" if i == 0 else "/goal .ndf/handoff/issue-7.md")
        day = f"2026-11-0{i + 1}"
        recs += [
            hook_row(f"s{i}", tp, f"{day}T00:00:00Z", stage="plan", verdict="継続", work=""),  # 作業は空でも数える
            hook_row(f"s{i}", tp, f"{day}T02:00:00Z", stage="plan", verdict="継続"),
            mark("復元完了", f"{day}T00:30:00Z", f"s{i}"),  # 呼び出しより前の印は数えない
            mark("復元完了", f"{day}T09:00:00Z"),  # どのセッションにも入らない印
        ]
    recs.append(hook_row("s9", None, "2026-11-01T00:00:00Z", stage="plan", verdict="継続"))  # 会話の記録が無い
    recs.append({"at": "2026-11-01T00:00:00Z", "event": "判定"})  # session_id の無い行
    assert _calibrated(recs) == {
        "restore_context": None,
        "restore_calls": None,
        "handoff_calls": None,
        "handoff_output": None,
        "stage_calls": {"plan": 1.0, "*": 1.0},
        "stage_expiries": {"plan": 0.0, "*": 0.0},
        "samples": {"restore": 0, "handoff": 0, "stage:plan": 3, "stage:*": 3},
    }
    since = cd.parse_ts("2026-11-02T00:00:00Z")
    assert _calibrated(recs, since=since)["samples"] == {"restore": 0, "handoff": 0, "stage:plan": 2, "stage:*": 2}


def _cached(ts: str, mid: str, **over) -> dict:
    """キャッシュの書き込みを 5 分と 1 時間に分けた usage を持つ呼び出し。"""
    c = call(ts, mid)
    c["message"]["usage"].update(
        {"cache_creation_input_tokens": 70, "cache_creation": {"ephemeral_5m_input_tokens": 30, "ephemeral_1h_input_tokens": 40}}, **over
    )
    return c


def test_transcript_and_ledger_build_the_same_call(tmp_path):
    # 会話の記録と使用量の帳簿は、同じ usage から同じキーと値の呼び出しを作る（現状固定。#2422）
    m = load_cli()
    a = _cached("2026-11-01T00:00:00Z", "m1")
    calls, compacts, by_next = cd.read_calls(write_transcript(tmp_path / "s1.jsonl", [a]))
    want = {
        "ts": cd.parse_ts("2026-11-01T00:00:00Z"),
        "model": "claude-opus-5-5",
        "inp": 100,
        "out": 10,
        "read": 1000,
        "w5": 30,
        "w1h": 40,
        "context": 1170,
    }
    assert (calls, compacts, by_next) == ([want], 0, False)
    row = {"at": "2026-11-01T00:00:00Z", "model": "claude-opus-5-5", "usage": a["message"]["usage"], "seconds": 4}
    assert m.ledger_calls(row) == want | {"seconds": 4}
    assert m.ledger_calls({**row, "model": "", "seconds": "x"}) == want | {"model": None, "seconds": None}


def test_transcript_counts_auto_compacts_and_the_handoff_start(tmp_path):
    lines = [
        {"type": "user", "isMeta": True, "message": {"content": "meta"}},
        {"type": "user", "message": {"content": [{"type": "text", "text": "cat .ndf/handoff/x.md"}]}},
        {"type": "system", "subtype": "compact_boundary", "compactMetadata": {"trigger": "auto"}},
        {"type": "system", "subtype": "compact_boundary", "compactMetadata": {"trigger": "manual"}},
        _cached("2026-11-01T00:00:00Z", "m1"),
    ]
    calls, compacts, by_next = cd.read_calls(write_transcript(tmp_path / "s1.jsonl", lines))
    assert (len(calls), compacts, by_next) == (1, 1, True)
    assert cd.read_calls(tmp_path / "missing.jsonl") == ([], None, False)


def test_work_report_pins_every_part(tmp_path):
    # 作業の報告の区分ごとの token・秒・費用・再起動・やり直しの数（現状固定。#2422）
    m = load_cli()
    first = {"type": "user", "message": {"content": "続き .ndf/handoff/a.md"}}
    tp = write_transcript(
        tmp_path / "s1.jsonl",
        [
            first,
            _cached("2026-11-01T00:00:00Z", "m1"),
            call("2026-11-01T00:10:00Z", "m2"),
            call("2026-11-01T00:20:00Z", "m3"),
            call("2026-11-01T01:30:00Z", "m4"),
        ],
    )
    write_transcript(
        tmp_path / "s1" / "subagents" / "agent-x.jsonl", [call("2026-11-01T00:11:00Z", "m5"), call("2026-11-01T00:12:00Z", "m6")]
    )
    ledger = [
        {
            "session_id": "ag9",
            "parent_session_id": "s1",
            "at": "2026-11-01T00:15:00Z",
            "usage": call("t", "m7")["message"]["usage"],
            "seconds": 12,
        }
    ]
    recs = [
        hook_row("s1", tp, "2026-11-01T00:00:00Z", verdict="継続", stage="skill:a"),
        mark("復元完了", "2026-11-01T00:05:00Z"),
        hook_row("s1", tp, "2026-11-01T00:06:00Z", verdict="継続", stage="skill:a"),
        mark("引継ぎ開始", "2026-11-01T01:00:00Z", sid="s1"),
        hook_row("s1", tp, "2026-11-01T01:40:00Z", verdict="再起動", reason="容量", stage="cut"),
        hook_row("s2", None, "2026-11-01T02:00:00Z", verdict="再起動"),
    ]
    out = m.report_work("#7", recs, ledger)
    parts = {b: (t["calls"], t["inp"], t["out"], t["read"], t["w5"], t["w1h"], t["seconds"], t["cost"]) for b, t in out["parts"].items()}
    assert parts == PINNED_PARTS
    rest = {k: v for k, v in out.items() if k != "parts"}
    assert rest == PINNED_REST


PINNED_PARTS = {
    "conductor": (2, 200, 20, 2000, 0, 0, 600, 400),
    "ndf": (3, 300, 30, 3000, 0, 0, 72, 650),
    "handoff": (1, 100, 10, 1000, 0, 0, 0, 200),
    "restore": (1, 100, 10, 1000, 30, 40, 0, 318),
}
PINNED_REST = {
    "work": "#7",
    "sessions": 2,
    "unknown_parts": {"restore": 0, "handoff": 1},
    "restarts": {"容量": 1, "不明": 1},
    "restart_count": 2,
    "auto_compacts": "不明",
    "quality": {"rework": 1, "review_fixes": "不明", "missed_requirements": "不明"},
    "no_policy_rows": 0,
}
