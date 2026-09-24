"""`scripts/token-usage.py` を小さな合成の記録で確かめる（#893）。

記録の形は 2026-09-23 に実物（Claude Code 2.1.280 / codex 0.156.0 / kiro-cli 2.23.0）で
確かめた項目だけを使う。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "token-usage.py"
WT = "/tmp/ndf-worktrees/acme--secret-repo/pr7"
SID = "11111111-2222-3333-4444-555555555555"


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _ts(minute: int) -> str:
    return f"2026-09-10T{minute // 60:02d}:{minute % 60:02d}:00.000Z"


def _assistant(minute: int, mid: str, usage: dict, content: list | None = None, model: str = "claude-opus-5") -> dict:
    return {"type": "assistant", "timestamp": _ts(minute), "version": "2.1.200", "cwd": "/work/x",
            "message": {"id": mid, "model": model, "usage": usage, "content": content or []}}


def _bash(tid: str, command: str) -> dict:
    return {"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": command}}


def _result(minute: int, tid: str, text: str) -> dict:
    return {"type": "user", "timestamp": _ts(minute),
            "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": text}]}}


U = {"input_tokens": 10, "cache_read_input_tokens": 1000, "output_tokens": 100,
     "cache_creation_input_tokens": 200, "cache_creation": {"ephemeral_1h_input_tokens": 200, "ephemeral_5m_input_tokens": 0}}
# 換算: 10*1 + 1000*0.1 + 200*2 + 100*5 = 1010
U_COST = 1010


def build(tmp: Path, version: str = "10.16.0") -> dict:
    claude = tmp / "claude"
    proj = claude / "-work-x"
    _jsonl(proj / f"{SID}.jsonl", [
        {"type": "user", "isMeta": True, "timestamp": _ts(0), "cwd": "/work/x",
         "message": {"content": [{"type": "text", "text":
                                  f"Base directory for this skill: /h/.claude/plugins/cache/ai-plugins/ndf/{version}/skills/development-workflow"}]}},
        _assistant(1, "m1", U, [_bash("t1", 'bash progress-record.sh 1 "実装" --mode light')]),
        _assistant(1, "m1", U, [_bash("t1", 'bash progress-record.sh 1 "実装" --mode light')]),  # 同じ応答の重複行
        _result(2, "t1", "ok"),
        _assistant(3, "m2", U, [_bash("t2", "gh pr create --draft --base develop")]),
        _result(4, "t2", "https://github.com/acme/secret-repo/pull/7\n"),
        _assistant(5, "m3", U, [_bash("t3", f"cd {WT} && gh pr view 7 --json url")]),
        _result(6, "t3", "https://github.com/acme/secret-repo/pull/7"),  # gh pr create 以外の URL は数えない
        _assistant(180, "m4", U),  # 174 分の空き（打ち切りの対象）
    ])
    sub = proj / SID / "subagents"
    _jsonl(sub / "agent-a1.jsonl", [_assistant(10, "s1", U), _assistant(20, "s2", U)])
    (sub / "agent-a1.meta.json").write_text(json.dumps({"description": "実装: #1", "spawnDepth": 1}), encoding="utf-8")
    _jsonl(sub / "agent-a2.jsonl", [_assistant(12, "w1", U), _assistant(14, "w2", U)])
    (sub / "agent-a2.meta.json").write_text(json.dumps({"description": "調査: 何か", "spawnDepth": 2}), encoding="utf-8")
    # ndf の Skill を起動しない会話は数えない
    _jsonl(claude / "-work-y" / "other.jsonl", [_assistant(1, "o1", U)])
    # cross-review の claude の席
    seat = dict(_assistant(30, "c1", U, model="claude-opus-5-5"), cwd=WT)
    _jsonl(claude / "-tmp-ndf-worktrees-acme--secret-repo-pr7" / "seat.jsonl", [seat])

    codex = tmp / "codex"
    _jsonl(codex / "2026/09/10/rollout-a.jsonl", [
        {"timestamp": _ts(30), "type": "session_meta", "payload": {"cwd": WT}},
        {"timestamp": _ts(30), "type": "turn_context", "payload": {"model": "gpt-x"}},
        {"timestamp": _ts(33), "type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {
            "input_tokens": 5000, "cached_input_tokens": 4000, "output_tokens": 50}}}},
    ])
    # 寄せ先の会話が無い席
    _jsonl(codex / "2026/09/10/rollout-b.jsonl", [
        {"timestamp": _ts(30), "type": "session_meta", "payload": {"cwd": "/tmp/ndf-worktrees/acme--other/rf9/work"}},
        {"timestamp": _ts(31), "type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {
            "input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1}}}},
    ])
    kiro = tmp / "kiro"
    kiro.mkdir()
    (kiro / "k.json").write_text(json.dumps({"cwd": WT, "created_at": "2026-09-10T00:31:00.000000000Z", "session_state": {
        "conversation_metadata": {"user_turn_metadatas": [{"model": "auto", "input_token_count": 0,
                                                           "turn_duration": {"secs": 120, "nanos": 0},
                                                           "metering_usage": [{"value": 0.5}, {"value": 0.25}]}]}}}),
        encoding="utf-8")
    return {"claude": claude, "codex": codex, "kiro": kiro}


def run(roots: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), "--claude-root", str(roots["claude"]),
                           "--codex-root", str(roots["codex"]), "--kiro-root", str(roots["kiro"]), *args],
                          capture_output=True, text=True)


def run_json(roots: dict, *args: str) -> dict:
    p = run(roots, "--format", "json", *args)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_per_pr_counts_one_session_by_version_mode_model(tmp_path):
    r = run_json(build(tmp_path))
    assert r["meta"]["sessions"] == 1
    assert r["meta"]["skipped"] == {"ndf の Skill が無い": 1}
    [row] = r["per_pr"]
    assert (row["version"], row["mode"], row["model"], row["prs"]) == ("10.16.0", "light", "claude-opus-5", 1)
    # 重複行を 1 回に数え、conductor は 4 応答
    assert row["conductor_cost"] == 4 * U_COST
    assert row["supervisor_cost"] == 2 * U_COST
    assert row["worker_cost"] == 2 * U_COST
    # 0→1→2→3→4→5→6 の 6 分と、174 分の空きを 30 分で打ち切った分
    assert row["minutes"] == 6 + 30


def test_roles_come_from_description_head_and_depth(tmp_path):
    rows = run_json(build(tmp_path))["per_role"]
    got = {(x["layer"], x["role"]): (x["count"], x["minutes"]) for x in rows}
    assert got == {("conductor", "-"): (1, 36), ("supervisor", "実装"): (1, 10), ("worker", "調査"): (1, 2)}


def test_external_cli_is_linked_by_worktree_and_time(tmp_path):
    r = run_json(build(tmp_path), "--by", "version,reviewers")
    [row] = r["per_pr"]
    assert row["reviewers"] == "claude+codex+kiro"
    assert (row["codex_input"], row["codex_out"], row["kiro_credit"]) == (5000, 50, 0.75)
    # 席は claude-opus-5-5 のため cache read は 0.05 倍: 10 + 1000*0.05 + 200*2 + 100*5
    assert row["claude_seat_cost"] == 960
    ext = {x["runtime"]: x for x in r["external"]}
    assert ext["kiro"]["minutes"] == 2
    assert ext["codex"]["skill"] == "cross-review"
    assert r["meta"]["unlinked_external"] == 1


def test_min_version_drops_older_sessions(tmp_path):
    r = run_json(build(tmp_path, version="10.9.1"), "--min-version", "10.14.0")
    assert r["meta"]["sessions"] == 0
    assert r["meta"]["unlinked_external"] == 1  # 版で絞る前に寄せるため、古い版の会話の席は未対応に数えない


def test_output_carries_no_repository_path_or_session_id(tmp_path):
    roots = build(tmp_path)
    for fmt in ("json", "md"):
        out = run(roots, "--format", fmt, "--by", "version,mode,model,cc,reviewers").stdout
        for secret in ("secret-repo", "acme", SID, "/tmp/", "/work/"):
            assert secret not in out, (fmt, secret)


def test_md_has_a_row_per_version(tmp_path):
    out = run(build(tmp_path), "--by", "version").stdout
    assert "| 10.16.0 | 1 | 1 |" in out


def test_unknown_axis_is_rejected(tmp_path):
    p = run(build(tmp_path), "--by", "version,repo")
    assert p.returncode == 2
    assert "repo" in p.stderr


def _u(inp: int, read: int, w5: int = 0, w1h: int = 0, out: int = 0) -> dict:
    return {"input_tokens": inp, "cache_read_input_tokens": read, "output_tokens": out,
            "cache_creation_input_tokens": w5 + w1h,
            "cache_creation": {"ephemeral_5m_input_tokens": w5, "ephemeral_1h_input_tokens": w1h}}


def build_calls(tmp: Path) -> dict:
    """呼び出しの並びを決めた会話 1 件。supervisor が待ちの後に全体を書き直す。"""
    roots = build(tmp)
    sub = roots["claude"] / "-work-x" / SID / "subagents"
    _jsonl(sub / "agent-a3.jsonl", [
        _assistant(40, "v1", _u(0, 30_000, w5=12_000)),                       # 最初の呼び出し: P = 42k
        _assistant(41, "v2", _u(0, 42_000, w5=1_000)),                        # 当たり
        _assistant(52, "v3", _u(0, 0, w5=43_000)),                            # 11 分空いて全体を書き直す
        _assistant(53, "v4", _u(0, 43_000, w5=30_000)),                       # 書き込みが 5 割未満
        _assistant(54, "v5", _u(0, 5_000, w5=15_000)),                        # 5 割超だが 20k 以下
        _assistant(55, "v6", _u(0, 0, w5=80_000), model="claude-fable-5-1"),  # 1 分で書き直す
    ])
    (sub / "agent-a3.meta.json").write_text(json.dumps({"description": "検査: #1", "spawnDepth": 1}), encoding="utf-8")
    return roots


def test_cache_read_rate_follows_each_call_model(tmp_path):
    roots = build(tmp_path)
    sub = roots["claude"] / "-work-x" / SID / "subagents"
    _jsonl(sub / "agent-a3.jsonl", [_assistant(40, "r1", _u(0, 1000), model="claude-opus-5-5"),
                                    _assistant(41, "r2", _u(0, 1000), model="claude-fable-5-1"),
                                    _assistant(42, "r3", _u(0, 1000), model="claude-sonnet-5")])
    (sub / "agent-a3.meta.json").write_text(json.dumps({"description": "検査: #1", "spawnDepth": 1}), encoding="utf-8")
    rows = {(x["layer"], x["role"]): x for x in run_json(roots)["per_role"]}
    assert rows[("supervisor", "検査")]["cost"] == 1000 * 0.05 + 1000 * 0.025 + 1000 * 0.1


def test_p_k_and_rewrites_per_role(tmp_path):
    rows = {(x["layer"], x["role"]): x for x in run_json(build_calls(tmp_path))["per_role"]}
    r = rows[("supervisor", "検査")]
    assert (r["count"], r["p"], r["k"]) == (1, 42_000, 6)
    assert (r["rewrites"], r["rewrites_after_5m"]) == (2, 1)
    assert r["rewrite_tokens"] == 43_000 + 80_000
    assert r["rewrite_gap_median"] == (11 + 1) / 2  # 分
    assert (r["w5"], r["w1h"]) == (12_000 + 1_000 + 43_000 + 30_000 + 15_000 + 80_000, 0)
    # conductor の書き込みは 1 時間（U）。最初の呼び出しは書き直しに数えない
    c = rows[("conductor", "-")]
    assert (c["p"], c["k"], c["rewrites"], c["w1h"], c["w5"]) == (1210, 4, 0, 800, 0)
    assert c["rewrite_gap_median"] is None


def test_codex_seat_calls_from_last_token_usage(tmp_path):
    roots = build(tmp_path)

    def tc(minute, total_in, inp, cached):
        return {"timestamp": _ts(minute), "type": "event_msg", "payload": {"type": "token_count", "info": {
            "total_token_usage": {"input_tokens": total_in, "cached_input_tokens": 0, "output_tokens": 1},
            "last_token_usage": {"input_tokens": inp, "cached_input_tokens": cached, "output_tokens": 1}}}}
    _jsonl(roots["codex"] / "2026/09/10/rollout-a.jsonl", [
        {"timestamp": _ts(30), "type": "session_meta", "payload": {"cwd": WT}},
        tc(31, 30_000, 30_000, 0),        # 最初: P = 30k
        tc(31, 30_000, 30_000, 0),        # 同じ累計の再掲は数えない
        tc(32, 70_000, 40_000, 35_000),   # 当たり
        tc(40, 120_000, 50_000, 10_000),  # 8 分空いて 40k を書き直す
    ])
    ext = {x["runtime"]: x for x in run_json(roots)["external"]}
    c = ext["codex"]
    assert (c["p"], c["k"], c["rewrites"], c["rewrites_after_5m"], c["rewrite_gap_median"]) == (30_000, 3, 1, 1, 8)
    assert ext["kiro"]["k"] == 1  # kiro はターン数だけ
    assert "p" not in ext["kiro"]


def test_md_has_call_tables(tmp_path):
    out = run(build_calls(tmp_path), "--by", "version").stdout
    assert "| 10.16.0 | supervisor | 検査 | 1 | 42.0k | 6.0 | 0.18M | 0.00M | 2 | 1 | 6.0 |" in out
