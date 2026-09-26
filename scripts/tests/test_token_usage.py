"""`scripts/token-usage.py` を小さな合成の記録で確かめる（#893）。

記録の形は 2026-09-23 に実物（Claude Code 2.1.280 / codex 0.156.0 / kiro-cli 2.23.0）で
確かめた項目だけを使う。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

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
    assert r["rewrite_tokens"] == 43_000 + 80_000  # 回数と同じく合計
    assert r["rewrite_gap_median"] == (11 + 1) / 2  # 分
    assert (r["w5"], r["w1h"]) == (12_000 + 1_000 + 43_000 + 30_000 + 15_000 + 80_000, 0)
    # conductor の書き込みは 1 時間（U）。最初の呼び出しは書き直しに数えない
    c = rows[("conductor", "-")]
    assert (c["p"], c["k"], c["rewrites"], c["w1h"], c["w5"]) == (1210, 4, 0, 800, 0)
    assert c["rewrite_gap_median"] is None
    assert r["rewrites_untimed"] == 0


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
    assert "w5" not in c and "w1h" not in c  # codex は書き込みを記録しない
    assert ext["kiro"]["turns"] == 1  # kiro はターン数だけで、呼び出し回数 k は出さない
    assert "p" not in ext["kiro"] and "k" not in ext["kiro"]


def test_md_has_call_tables(tmp_path):
    out = run(build_calls(tmp_path), "--by", "version").stdout
    assert "| 10.16.0 | supervisor | 検査 | - | 1 | 42.0k | 6.0 | 0.18M | 0.00M | 2 | 1 | 0.04M | 0.00M | 6.0 |" in out


def test_codex_seat_without_per_call_usage_has_no_p_or_k(tmp_path):
    # build の codex の席は累計だけで last_token_usage を持たない
    ext = {x["runtime"]: x for x in run_json(build(tmp_path))["external"]}
    assert "p" not in ext["codex"] and "k" not in ext["codex"]
    out = run(build(tmp_path / "md"), "--by", "version").stdout
    assert "| 10.16.0 | codex | cross-review | gpt-x | 1 | - | - | - | - | - |" in out


def test_until_drops_later_lines(tmp_path):
    roots = build_calls(tmp_path)
    rows = {(x["layer"], x["role"]): x for x in run_json(roots, "--until", _ts(52))["per_role"]}
    r = rows[("supervisor", "検査")]
    assert (r["k"], r["rewrites"]) == (3, 1)  # 53 分以降の 3 呼び出しを読まない
    assert run_json(roots, "--until", _ts(52))["meta"]["until"] == _ts(52)
    for bad in ("yesterday", "2026-09-10T00:52:00"):  # 時間帯の無い値は機械で打ち切りが変わるため拒む
        p = run(roots, "--until", bad)
        assert p.returncode == 2 and "--until" in p.stderr, bad


def test_until_applies_to_codex_lines_and_kiro_creation(tmp_path):
    roots = build(tmp_path)

    def tc(minute, total_in, inp):
        return {"timestamp": _ts(minute), "type": "event_msg", "payload": {"type": "token_count", "info": {
            "total_token_usage": {"input_tokens": total_in, "cached_input_tokens": 0, "output_tokens": 1},
            "last_token_usage": {"input_tokens": inp, "cached_input_tokens": 0, "output_tokens": 1}}}}
    _jsonl(roots["codex"] / "2026/09/10/rollout-a.jsonl", [
        {"timestamp": _ts(30), "type": "session_meta", "payload": {"cwd": WT}},
        tc(31, 1000, 1000), tc(33, 3000, 2000), tc(40, 6000, 3000)])
    # 打ち切りの後も席を会話へ寄せられるよう、会話の行を打ち切りの直前に 1 つ足す
    with open(roots["claude"] / "-work-x" / f"{SID}.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(_assistant(34, "m5", U)) + "\n")
    r = run_json(roots, "--until", _ts(35))
    ext = {x["runtime"]: x for x in r["external"]}
    assert (ext["codex"]["k"], ext["codex"]["p"]) == (2, 1000)  # 40 分の呼び出しを読まない
    [row] = r["per_pr"]
    assert row["codex_input"] == 3000  # 累計も打ち切りの前の値
    # kiro の記録は 31 分に作られたため、30 分で打ち切ると除かれる
    r = run_json(roots, "--until", _ts(30))
    assert "kiro" not in {x["runtime"] for x in r["external"]}
    assert r["per_pr"][0]["kiro_credit"] == 0


# ---------- 待ちの後の書き直しと読み込みの量・定義の名前（#954 の AC6） ----------

def _agent(tid: str, subagent_type: str, description: str) -> dict:
    return {"type": "tool_use", "id": tid, "name": "Agent",
            "input": {"subagent_type": subagent_type, "description": description, "prompt": "…"}}


def build_waits(tmp: Path) -> dict:
    """同じ description（設計: #1）の supervisor 2 本。片方は meta の agentType に定義の名前を持ち、
    もう片方は general-purpose で、親の記録の Agent 呼び出しの subagent_type から引く。"""
    roots = build(tmp)
    proj = roots["claude"] / "-work-x"
    with open(proj / f"{SID}.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(_assistant(39, "m9", U, [_agent("toolu_w", "ndf:supervisor-waits", "設計: #1")])) + "\n")
    sub = proj / SID / "subagents"
    # 5 分の定義: 4 分の間隔で 30k、6 分の間隔で 50k を書き直し、6 分の間隔で 80k を読み込む。時刻の無い書き直しも 1 回
    _jsonl(sub / "agent-b1.jsonl", [
        _assistant(40, "b1", _u(0, 0, w5=40_000)),
        _assistant(44, "b2", _u(0, 10_000, w5=30_000)),   # 4 分: 書き直し 30k（5 分以内）
        _assistant(50, "b3", _u(0, 5_000, w5=50_000)),    # 6 分: 書き直し 50k（5 分超）
        _assistant(56, "b4", _u(0, 80_000, w5=1_000)),    # 6 分: 読み込み 80k（書き直しでない）
        {"type": "assistant", "message": {"id": "b5", "model": "claude-opus-5", "usage": _u(0, 0, w5=90_000)}},
    ])
    (sub / "agent-b1.meta.json").write_text(json.dumps(
        {"description": "設計: #1", "spawnDepth": 1, "agentType": "ndf:supervisor"}), encoding="utf-8")
    # 1 時間の定義: 10 分空いても読み込みで済む
    _jsonl(sub / "agent-b2.jsonl", [
        _assistant(60, "c1", _u(0, 0, w1h=40_000)),
        _assistant(70, "c2", _u(0, 40_000, w1h=500)),
    ])
    (sub / "agent-b2.meta.json").write_text(json.dumps(
        {"description": "設計: #1", "spawnDepth": 1, "agentType": "general-purpose", "toolUseId": "toolu_w"}),
        encoding="utf-8")
    return roots


def test_per_role_splits_by_agent_type(tmp_path):
    rows = run_json(build_waits(tmp_path))["per_role"]
    got = {x["agent_type"]: x for x in rows if (x["layer"], x["role"]) == ("supervisor", "設計")}
    assert set(got) == {"ndf:supervisor", "ndf:supervisor-waits"}
    assert got["ndf:supervisor-waits"]["w1h"] > 0 and got["ndf:supervisor-waits"]["w5"] == 0
    # 定義の名前が取れない起動は -
    assert {x["agent_type"] for x in rows if x["role"] == "実装"} == {"-"}


def test_after_5m_amounts_split_by_gap(tmp_path):
    rows = run_json(build_waits(tmp_path))["per_role"]
    got = {x["agent_type"]: x for x in rows if x["role"] == "設計"}
    short, long = got["ndf:supervisor"], got["ndf:supervisor-waits"]
    assert short["rewrites"] == 3 and short["rewrites_untimed"] == 1
    assert short["rewrite_tokens_after_5m"] == 50_000  # 4 分の 30k と時刻の無い 90k は足さない
    assert short["read_tokens_after_5m"] == 80_000
    assert (long["rewrite_tokens_after_5m"], long["read_tokens_after_5m"]) == (0, 40_000)


def test_after_5m_amounts_add_up_across_sessions(tmp_path):
    roots = build_waits(tmp_path)
    proj = roots["claude"] / "-work-x"
    sid2 = "99999999-2222-3333-4444-555555555555"
    shutil.copy(proj / f"{SID}.jsonl", proj / f"{sid2}.jsonl")
    shutil.copytree(proj / SID, proj / sid2)
    rows = run_json(roots)["per_role"]
    got = {x["agent_type"]: x for x in rows if x["role"] == "設計"}
    assert got["ndf:supervisor"]["count"] == 2
    assert got["ndf:supervisor"]["rewrite_tokens_after_5m"] == 2 * 50_000
    assert got["ndf:supervisor"]["read_tokens_after_5m"] == 2 * 80_000


def test_external_has_after_5m_columns(tmp_path):
    roots = build_waits(tmp_path)

    def tc(minute, total_in, inp, cached):
        return {"timestamp": _ts(minute), "type": "event_msg", "payload": {"type": "token_count", "info": {
            "total_token_usage": {"input_tokens": total_in, "cached_input_tokens": 0, "output_tokens": 1},
            "last_token_usage": {"input_tokens": inp, "cached_input_tokens": cached, "output_tokens": 1}}}}
    _jsonl(roots["codex"] / "2026/09/10/rollout-a.jsonl", [
        {"timestamp": _ts(30), "type": "session_meta", "payload": {"cwd": WT}},
        tc(31, 30_000, 30_000, 0),
        tc(40, 70_000, 40_000, 5_000),    # 9 分空いて 35k を書き直す
        tc(50, 120_000, 50_000, 45_000),  # 10 分空いて 45k を読み込む
    ])
    c = {x["runtime"]: x for x in run_json(roots)["external"]}["codex"]
    assert (c["rewrite_tokens_after_5m"], c["read_tokens_after_5m"]) == (35_000, 45_000)
    out = run(roots, "--by", "version").stdout
    assert "5 分超の書き直し | 5 分超の読み込み" in out
    assert "| 10.16.0 | supervisor | 設計 | ndf:supervisor | 1 |" in out


def test_version_key_orders_dev_numbers_numerically():
    import importlib.util
    spec = importlib.util.spec_from_file_location("token_usage", Path(__file__).parents[1] / "token-usage.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["token_usage"] = mod  # dataclass が自分のモジュールを sys.modules から引くため
    spec.loader.exec_module(mod)
    got = sorted(["10.17.30", "10.17.30-dev.10", "10.17.29", "10.17.30-dev.9", "10.17.30-dev.2"], key=mod.version_key)
    assert got == ["10.17.29", "10.17.30-dev.2", "10.17.30-dev.9", "10.17.30-dev.10", "10.17.30"]


def test_a_version_outside_the_release_forms_is_not_a_version(tmp_path):
    """#1142 の D8: 版の比較を lib/versions.py（SemVer 2.0 と -dev.N / -rc.N）へ移した。ほかの接尾辞の版は
    版を判定できない会話に数え、version_key は ValueError を出す（移す前は字面で並べていた）。"""
    r = run_json(build(tmp_path, version="10.16.0-beta"))
    assert r["meta"]["sessions"] == 0
    assert r["meta"]["skipped"].get("版を判定できない") == 1
    import importlib.util
    spec = importlib.util.spec_from_file_location("token_usage", Path(__file__).parents[1] / "token-usage.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["token_usage"] = mod
    spec.loader.exec_module(mod)
    with pytest.raises(ValueError):
        mod.version_key("10.16.0-beta")
