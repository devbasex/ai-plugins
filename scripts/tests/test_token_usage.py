"""`scripts/token-usage.py` を小さな合成の記録で確かめる（#893）。

記録の形は 2026-09-23 に実物（Claude Code 2.1.280 / codex 0.156.0 / kiro-cli 2.23.0）で
確かめた項目だけを使う。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import types
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
    return {
        "type": "assistant",
        "timestamp": _ts(minute),
        "version": "2.1.200",
        "cwd": "/work/x",
        "message": {"id": mid, "model": model, "usage": usage, "content": content or []},
    }


def _bash(tid: str, command: str) -> dict:
    return {"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": command}}


def _result(minute: int, tid: str, text: str) -> dict:
    return {
        "type": "user",
        "timestamp": _ts(minute),
        "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": text}]},
    }


U = {
    "input_tokens": 10,
    "cache_read_input_tokens": 1000,
    "output_tokens": 100,
    "cache_creation_input_tokens": 200,
    "cache_creation": {"ephemeral_1h_input_tokens": 200, "ephemeral_5m_input_tokens": 0},
}
# 換算: 10*1 + 1000*0.1 + 200*2 + 100*5 = 1010
U_COST = 1010


def _build_claude(tmp: Path, version: str) -> Path:
    claude = tmp / "claude"
    proj = claude / "-work-x"
    _jsonl(
        proj / f"{SID}.jsonl",
        [
            {
                "type": "user",
                "isMeta": True,
                "timestamp": _ts(0),
                "cwd": "/work/x",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": f"Base directory for this skill: /h/.claude/plugins/cache/ai-plugins/ndf/{version}/skills/development-workflow",
                        }
                    ]
                },
            },
            _assistant(1, "m1", U, [_bash("t1", 'bash progress-record.sh 1 "実装" --mode light')]),
            _assistant(1, "m1", U, [_bash("t1", 'bash progress-record.sh 1 "実装" --mode light')]),  # 同じ応答の重複行
            _result(2, "t1", "ok"),
            _assistant(3, "m2", U, [_bash("t2", "gh pr create --draft --base develop")]),
            _result(4, "t2", "https://github.com/acme/secret-repo/pull/7\n"),
            _assistant(5, "m3", U, [_bash("t3", f"cd {WT} && gh pr view 7 --json url")]),
            _result(6, "t3", "https://github.com/acme/secret-repo/pull/7"),  # gh pr create 以外の URL は数えない
            _assistant(180, "m4", U),  # 174 分の空き（打ち切りの対象）
        ],
    )
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
    # 席が Agent で起動したサブエージェントの消費も席に入る
    seat_sub = dict(_assistant(31, "c2", U, model="claude-opus-5-5"), cwd=WT)
    _jsonl(claude / "-tmp-ndf-worktrees-acme--secret-repo-pr7" / "seat" / "subagents" / "agent-b1.jsonl", [seat_sub])
    return claude


def _build_codex(tmp: Path) -> Path:
    codex = tmp / "codex"
    _jsonl(
        codex / "2026/09/10/rollout-a.jsonl",
        [
            {"timestamp": _ts(30), "type": "session_meta", "payload": {"cwd": WT}},
            {"timestamp": _ts(30), "type": "turn_context", "payload": {"model": "gpt-x"}},
            {
                "timestamp": _ts(33),
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {"total_token_usage": {"input_tokens": 5000, "cached_input_tokens": 4000, "output_tokens": 50}},
                },
            },
        ],
    )
    # 寄せ先の会話が無い席
    _jsonl(
        codex / "2026/09/10/rollout-b.jsonl",
        [
            {"timestamp": _ts(30), "type": "session_meta", "payload": {"cwd": "/tmp/ndf-worktrees/acme--other/rf9/work"}},
            {
                "timestamp": _ts(31),
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {"total_token_usage": {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1}},
                },
            },
        ],
    )
    return codex


def _build_kiro(tmp: Path) -> Path:
    kiro = tmp / "kiro"
    kiro.mkdir()
    (kiro / "k.json").write_text(
        json.dumps(
            {
                "cwd": WT,
                "created_at": "2026-09-10T00:31:00.000000000Z",
                "session_state": {
                    "conversation_metadata": {
                        "user_turn_metadatas": [
                            {
                                "model": "auto",
                                "input_token_count": 0,
                                "turn_duration": {"secs": 120, "nanos": 0},
                                "metering_usage": [{"value": 0.5}, {"value": 0.25}],
                            }
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return kiro


def build(tmp: Path, version: str = "10.16.0") -> dict:
    claude = _build_claude(tmp, version)
    codex = _build_codex(tmp)
    kiro = _build_kiro(tmp)
    return {"claude": claude, "codex": codex, "kiro": kiro}


def run(roots: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--claude-root",
            str(roots["claude"]),
            "--codex-root",
            str(roots["codex"]),
            "--kiro-root",
            str(roots["kiro"]),
            *args,
        ],
        capture_output=True,
        text=True,
    )


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
    # 親の 0→6 の 6 分、子の 10→12→14→20 の行と親の 6 分との間の 14 分、20→180 の空きを 30 分で打ち切った分
    assert row["minutes"] == 6 + 14 + 30


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
    # 席とそのサブエージェントの 2 呼び出し
    assert row["claude_seat_cost"] == 2 * 960
    ext = {x["runtime"]: x for x in r["external"]}
    # P は席の最初の呼び出しの文脈だけ（サブエージェントの最初の文脈は足さない）
    assert ext["claude"]["p"] == 1210
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
    return {
        "input_tokens": inp,
        "cache_read_input_tokens": read,
        "output_tokens": out,
        "cache_creation_input_tokens": w5 + w1h,
        "cache_creation": {"ephemeral_5m_input_tokens": w5, "ephemeral_1h_input_tokens": w1h},
    }


def build_calls(tmp: Path) -> dict:
    """呼び出しの並びを決めた会話 1 件。supervisor が待ちの後に全体を書き直す。"""
    roots = build(tmp)
    sub = roots["claude"] / "-work-x" / SID / "subagents"
    _jsonl(
        sub / "agent-a3.jsonl",
        [
            _assistant(40, "v1", _u(0, 30_000, w5=12_000)),  # 最初の呼び出し: P = 42k
            _assistant(41, "v2", _u(0, 42_000, w5=1_000)),  # 当たり
            _assistant(52, "v3", _u(0, 0, w5=43_000)),  # 11 分空いて全体を書き直す
            _assistant(53, "v4", _u(0, 43_000, w5=30_000)),  # 書き込みが 5 割未満
            _assistant(54, "v5", _u(0, 5_000, w5=15_000)),  # 5 割超だが 20k 以下
            _assistant(55, "v6", _u(0, 0, w5=80_000), model="claude-fable-5-1"),  # 1 分で書き直す
        ],
    )
    (sub / "agent-a3.meta.json").write_text(json.dumps({"description": "検査: #1", "spawnDepth": 1}), encoding="utf-8")
    return roots


def test_cache_read_rate_follows_each_call_model(tmp_path):
    roots = build(tmp_path)
    sub = roots["claude"] / "-work-x" / SID / "subagents"
    _jsonl(
        sub / "agent-a3.jsonl",
        [
            _assistant(40, "r1", _u(0, 1000), model="claude-opus-5-5"),
            _assistant(41, "r2", _u(0, 1000), model="claude-fable-5-1"),
            _assistant(42, "r3", _u(0, 1000), model="claude-sonnet-5"),
        ],
    )
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
        return {
            "timestamp": _ts(minute),
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {"input_tokens": total_in, "cached_input_tokens": 0, "output_tokens": 1},
                    "last_token_usage": {"input_tokens": inp, "cached_input_tokens": cached, "output_tokens": 1},
                },
            },
        }

    _jsonl(
        roots["codex"] / "2026/09/10/rollout-a.jsonl",
        [
            {"timestamp": _ts(30), "type": "session_meta", "payload": {"cwd": WT}},
            tc(31, 30_000, 30_000, 0),  # 最初: P = 30k
            tc(31, 30_000, 30_000, 0),  # 同じ累計の再掲は数えない
            tc(32, 70_000, 40_000, 35_000),  # 当たり
            tc(40, 120_000, 50_000, 10_000),  # 8 分空いて 40k を書き直す
        ],
    )
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
    # 期間は半開区間で、--until ちょうどの行も読まない（#3412）
    rows = {(x["layer"], x["role"]): x for x in run_json(roots, "--until", _ts(53))["per_role"]}
    r = rows[("supervisor", "検査")]
    assert (r["k"], r["rewrites"]) == (3, 1)  # 53 分以降の 3 呼び出しを読まない
    assert run_json(roots, "--until", _ts(53))["meta"]["until"] == _ts(53)
    for bad in ("yesterday", "2026-09-10T00:52:00"):  # 時間帯の無い値は機械で打ち切りが変わるため拒む
        p = run(roots, "--until", bad)
        assert p.returncode == 2 and "--until" in p.stderr, bad


def test_until_applies_to_codex_lines_and_kiro_creation(tmp_path):
    roots = build(tmp_path)

    def tc(minute, total_in, inp):
        return {
            "timestamp": _ts(minute),
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {"input_tokens": total_in, "cached_input_tokens": 0, "output_tokens": 1},
                    "last_token_usage": {"input_tokens": inp, "cached_input_tokens": 0, "output_tokens": 1},
                },
            },
        }

    _jsonl(
        roots["codex"] / "2026/09/10/rollout-a.jsonl",
        [
            {"timestamp": _ts(30), "type": "session_meta", "payload": {"cwd": WT}},
            tc(31, 1000, 1000),
            tc(33, 3000, 2000),
            tc(40, 6000, 3000),
        ],
    )
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
    return {
        "type": "tool_use",
        "id": tid,
        "name": "Agent",
        "input": {"subagent_type": subagent_type, "description": description, "prompt": "…"},
    }


def build_waits(tmp: Path) -> dict:
    """同じ description（設計: #1）の supervisor 2 本。片方は meta の agentType に定義の名前を持ち、
    もう片方は general-purpose で、親の記録の Agent 呼び出しの subagent_type から引く。"""
    roots = build(tmp)
    proj = roots["claude"] / "-work-x"
    with open(proj / f"{SID}.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(_assistant(39, "m9", U, [_agent("toolu_w", "ndf:supervisor-waits", "設計: #1")])) + "\n")
    sub = proj / SID / "subagents"
    # 5 分の定義: 4 分の間隔で 30k、6 分の間隔で 50k を書き直し、6 分の間隔で 80k を読み込む。時刻の無い書き直しも 1 回
    _jsonl(
        sub / "agent-b1.jsonl",
        [
            _assistant(40, "b1", _u(0, 0, w5=40_000)),
            _assistant(44, "b2", _u(0, 10_000, w5=30_000)),  # 4 分: 書き直し 30k（5 分以内）
            _assistant(50, "b3", _u(0, 5_000, w5=50_000)),  # 6 分: 書き直し 50k（5 分超）
            _assistant(56, "b4", _u(0, 80_000, w5=1_000)),  # 6 分: 読み込み 80k（書き直しでない）
            {"type": "assistant", "message": {"id": "b5", "model": "claude-opus-5", "usage": _u(0, 0, w5=90_000)}},
        ],
    )
    (sub / "agent-b1.meta.json").write_text(
        json.dumps({"description": "設計: #1", "spawnDepth": 1, "agentType": "ndf:supervisor"}), encoding="utf-8"
    )
    # 1 時間の定義: 10 分空いても読み込みで済む
    _jsonl(
        sub / "agent-b2.jsonl",
        [
            _assistant(60, "c1", _u(0, 0, w1h=40_000)),
            _assistant(70, "c2", _u(0, 40_000, w1h=500)),
        ],
    )
    (sub / "agent-b2.meta.json").write_text(
        json.dumps({"description": "設計: #1", "spawnDepth": 1, "agentType": "general-purpose", "toolUseId": "toolu_w"}), encoding="utf-8"
    )
    return roots


def test_per_role_splits_by_agent_type(tmp_path):
    rows = run_json(build_waits(tmp_path))["per_role"]
    got = {x["agent_type"]: x for x in rows if (x["layer"], x["role"]) == ("supervisor", "設計")}
    assert set(got) == {"ndf:supervisor", "ndf:supervisor-waits"}
    assert got["ndf:supervisor-waits"]["w1h"] > 0 and got["ndf:supervisor-waits"]["w5"] == 0
    # 定義の名前が取れない起動は -
    assert {x["agent_type"] for x in rows if x["role"] == "実装"} == {"-"}


def test_agent_type_outside_ndf_and_builtins_is_grouped(tmp_path):
    """利用者や他社の定義の名前は記録へ残さず「その他」にまとめる。組み込みの定義はそのまま載せる。"""
    roots = build_waits(tmp_path)
    proj = roots["claude"] / "-work-x"
    with open(proj / f"{SID}.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(_assistant(39, "m8", U, [_agent("toolu_p", "acme-plugin:deployer", "設計: #1")])) + "\n")
        fh.write(json.dumps(_assistant(39, "m7", U, [_agent("toolu_e", "Explore", "設計: #1")])) + "\n")
    sub = proj / SID / "subagents"
    for name, tid in (("agent-p1", "toolu_p"), ("agent-e1", "toolu_e")):
        _jsonl(sub / f"{name}.jsonl", [_assistant(41, f"{name}-1", _u(0, 1_000, w5=1_000))])
        (sub / f"{name}.meta.json").write_text(
            json.dumps({"description": "設計: #1", "spawnDepth": 1, "agentType": "x", "toolUseId": tid}), encoding="utf-8"
        )
    rows = run_json(roots)["per_role"]
    got = {x["agent_type"] for x in rows if x["role"] == "設計"}
    assert got == {"ndf:supervisor", "ndf:supervisor-waits", "Explore", "その他"}
    assert "acme" not in json.dumps(rows)


def test_until_does_not_count_subagents_started_later(tmp_path):
    """打ち切りより後の行しか持たないサブエージェントは起動数へ入れない（同じ打ち切りで作り直しても変わらない）。"""
    roots = build_waits(tmp_path)
    before = {x["agent_type"]: x["count"] for x in run_json(roots, "--until", _ts(55))["per_role"] if x["role"] == "設計"}
    assert before == {"ndf:supervisor": 1}  # 60 分に始まる 1 時間の定義は数えない


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
        return {
            "timestamp": _ts(minute),
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {"input_tokens": total_in, "cached_input_tokens": 0, "output_tokens": 1},
                    "last_token_usage": {"input_tokens": inp, "cached_input_tokens": cached, "output_tokens": 1},
                },
            },
        }

    _jsonl(
        roots["codex"] / "2026/09/10/rollout-a.jsonl",
        [
            {"timestamp": _ts(30), "type": "session_meta", "payload": {"cwd": WT}},
            tc(31, 30_000, 30_000, 0),
            tc(40, 70_000, 40_000, 5_000),  # 9 分空いて 35k を書き直す
            tc(50, 120_000, 50_000, 45_000),  # 10 分空いて 45k を読み込む
        ],
    )
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


# ---------- 期間の始まりと PR の一意化（#3412） ----------

SID2 = "99999999-2222-3333-4444-555555555555"


def _add_session(roots: dict, sid: str, version: str, extra: list[dict] | None = None) -> None:
    """build の主の会話を版を変えて写す（サブエージェントは写さない。conductor の 4 応答だけ）。"""
    proj = roots["claude"] / "-work-x"
    text = (proj / f"{SID}.jsonl").read_text(encoding="utf-8").replace("/ndf/10.16.0/", f"/ndf/{version}/")
    text += "".join(json.dumps(r) + "\n" for r in extra or [])
    (proj / f"{sid}.jsonl").write_text(text, encoding="utf-8")


def _empty_ledger(tmp: Path) -> str:
    d = tmp / "ledger"
    d.mkdir(exist_ok=True)
    return str(d)


def test_same_pr_in_two_sessions_counts_once_with_both_costs(tmp_path):
    roots = build(tmp_path)
    _add_session(roots, SID2, "10.17.0")
    r = run_json(roots, "--by", "version", "--repo", "acme/secret-repo", "--usage-root", _empty_ledger(tmp_path))
    meta = r["meta"]
    # 版をまたいでも同じ PR は 1 本。費用は両方の会話の 3 層の和（8 応答 + 4 応答）、所要も両方の和
    assert meta["unique_prs"] == 1
    assert meta["pr_cost_total"] == 12 * U_COST
    assert meta["pr_minutes_total"] == 50 + 36  # 子を持つ会話は子の活動を含む 50 分、写した会話は親だけの 36 分
    assert "reason" not in meta
    rows = {x["version"]: x for x in r["per_pr"]}
    assert (rows["10.16.0"]["unique_prs"], rows["10.17.0"]["unique_prs"]) == (1, 1)
    assert sum(x["pr_cost_total"] for x in r["per_pr"]) == meta["pr_cost_total"]
    assert rows["10.16.0"]["prs"] == 1  # 今のキーは変えない


def test_a_session_with_two_prs_adds_its_cost_once(tmp_path):
    roots = build(tmp_path)
    _add_session(
        roots,
        SID2,
        "10.16.0",
        [
            _assistant(7, "m7", U, [_bash("t7", "gh pr create --draft")]),
            _result(8, "t7", "https://github.com/acme/secret-repo/pull/8\n"),
            _assistant(9, "m8", U, [_bash("t8", "gh pr create --draft")]),
            _result(10, "t8", "https://github.com/acme/other/pull/9\n"),  # 対象の外
        ],
    )
    meta = run_json(roots, "--by", "version", "--repo", "acme/secret-repo", "--usage-root", _empty_ledger(tmp_path))["meta"]
    assert meta["unique_prs"] == 2  # pull/7 と pull/8。acme/other は数えない
    assert meta["pr_cost_total"] == 8 * U_COST + 6 * U_COST


def test_no_pr_of_the_target_repo_gives_zero_and_reason(tmp_path):
    r = run_json(build(tmp_path), "--by", "version", "--repo", "devbasex/ai-plugins", "--usage-root", _empty_ledger(tmp_path))
    assert r["meta"]["unique_prs"] == 0 and r["meta"]["reason"]
    [row] = r["per_pr"]
    assert row["unique_prs"] == 0 and row["reason"]
    assert row["prs"] == 1  # 対象を問わない今の数え方は残る


def test_release_axis_needs_a_repository_directory(tmp_path):
    p = run(build(tmp_path), "--by", "release", "--repo", "acme/secret-repo")
    assert p.returncode == 2 and "--repo" in p.stderr


def _boundary_roots(tmp: Path) -> dict:
    """実データの形（UTC・ミリ秒・Z）の時刻を持つ会話 1 件。期間の端の前後に応答を置く。"""
    claude = tmp / "claude"

    def at(ts: str, mid: str) -> dict:
        return dict(_assistant(0, mid, U), timestamp=ts)

    _jsonl(
        claude / "-work-x" / f"{SID}.jsonl",
        [
            {
                "type": "user",
                "isMeta": True,
                "timestamp": "2026-09-10T01:00:00.000Z",
                "message": {"content": [{"type": "text", "text": "Base directory for this skill: /h/ai-plugins/ndf/10.16.0/skills/x"}]},
            },
            at("2026-09-10T00:59:59.999Z", "a0"),  # 始まりの前: 数えない
            at("2026-09-10T01:00:00.000Z", "a1"),  # 始まりちょうど: 数える
            at("2026-09-10T23:59:59.999Z", "a2"),  # 終わりの 1 ミリ秒前: 数える
            at("2026-09-11T00:00:00.000Z", "a3"),  # 終わりちょうど: 数えない
        ],
    )
    (tmp / "codex").mkdir()
    (tmp / "kiro").mkdir()
    return {"claude": claude, "codex": tmp / "codex", "kiro": tmp / "kiro"}


@pytest.mark.parametrize(
    "since,until",
    [
        ("2026-09-10T01:00:00Z", "2026-09-11T00:00:00Z"),
        ("2026-09-10T10:00:00+09:00", "2026-09-11T09:00:00+09:00"),  # 時差の違う値も時刻として比べる
    ],
)
def test_since_and_until_make_a_half_open_period(tmp_path, since, until):
    roots = _boundary_roots(tmp_path)
    r = run_json(roots, "--since", since, "--until", until, "--usage-root", _empty_ledger(tmp_path))
    [row] = r["per_role"]
    assert row["count"] == 1 and row["k"] == 2  # a1 と a2 だけ
    assert r["meta"]["since"] == since


def _module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("token_usage", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["token_usage"] = mod  # dataclass が自分のモジュールを sys.modules から引くため
    spec.loader.exec_module(mod)
    return mod


def test_since_applies_to_kiro_creation(tmp_path):
    mod = _module()
    kiro = _build_kiro(tmp_path)  # 31 分に作られた記録
    assert len(mod.read_kiro(kiro, since=mod.parse_ts(_ts(31)))) == 1  # 始まりちょうどは数える
    assert mod.read_kiro(kiro, since=mod.parse_ts(_ts(32))) == []
    assert mod.read_kiro(kiro, until=mod.parse_ts(_ts(31))) == []  # 終わりちょうどは数えない


def test_since_without_offset_is_rejected(tmp_path):
    for bad in ("2026-09-10T00:52:00", "yesterday"):
        p = run(build(tmp_path / bad.replace(":", "")), "--since", bad)
        assert p.returncode == 2 and "--since" in p.stderr, bad


def _add_session_without_pr(roots: dict, sid: str) -> None:
    """PR を作らない会話（計画が起動して会話の記録が残った claude -p など）。"""
    _add_session(roots, sid, "10.16.0")
    f = roots["claude"] / "-work-x" / f"{sid}.jsonl"
    f.write_text(f.read_text(encoding="utf-8").replace("gh pr create --draft --base develop", "echo"), encoding="utf-8")


def test_sessions_out_lists_only_sessions_in_pr_cost_total(tmp_path):
    """--sessions-out は U1 の分子に費用を入れた会話だけ。PR を持たない会話は載せず、plan-cost に足させる。"""
    roots = build(tmp_path)
    _add_session(roots, SID2, "10.17.0")
    _add_session(roots, "00000000-0000-0000-0000-000000000000", "10.9.1")  # --min-version で落ちる
    _add_session_without_pr(roots, "77777777-0000-0000-0000-000000000000")
    out = tmp_path / "sessions.txt"
    p = run(roots, "--min-version", "10.14.0", "--repo", "acme/secret-repo", "--no-ledger", "--sessions-out", str(out))
    assert p.returncode == 0, p.stderr
    assert out.read_text(encoding="utf-8") == f"{SID}\n{SID2}\n"
    assert SID not in p.stdout  # 表には会話の ID を載せない
    assert out.stat().st_mode & 0o777 == 0o600  # 会話の ID を他の利用者に読ませない


def test_sessions_out_refuses_an_existing_file_and_needs_repo(tmp_path):
    roots = build(tmp_path)
    out = tmp_path / "sessions.txt"
    out.write_text("keep\n", encoding="utf-8")
    p = run(roots, "--repo", "acme/secret-repo", "--no-ledger", "--sessions-out", str(out))
    assert p.returncode == 2 and "--sessions-out" in p.stderr
    assert out.read_text(encoding="utf-8") == "keep\n"
    p = run(roots, "--no-ledger", "--sessions-out", str(tmp_path / "new.txt"))
    assert p.returncode == 2 and "--repo" in p.stderr and not (tmp_path / "new.txt").exists()


def test_skill_loaded_before_since_keeps_the_session(tmp_path):
    """期間の前に Skill を読み込み、期間の内に続けた会話は、版を引き継いで期間の内の使用量だけを数える。"""
    r = run_json(build(tmp_path), "--since", _ts(3), "--usage-root", _empty_ledger(tmp_path))
    assert r["meta"]["sessions"] == 1
    [row] = r["per_pr"]
    assert row["version"] == "10.16.0"
    assert row["conductor_cost"] == 3 * U_COST  # m1（1 分）は期間の前。m2（3 分）・m3（5 分）・m4（180 分）
    # 期間の前の Bash の呼び出しで決めたモードも引き継ぐ
    assert row["mode"] == "light"


def test_pr_created_before_since_counts_by_result_time(tmp_path):
    """gh pr create の呼び出しが期間の前でも、PR の URL を返した結果が期間の内なら数える。"""
    roots = build(tmp_path)
    args = ("--by", "version", "--repo", "acme/secret-repo", "--usage-root", _empty_ledger(tmp_path))
    assert run_json(roots, "--since", _ts(4), *args)["meta"]["unique_prs"] == 1  # 呼び出し 3 分・結果 4 分
    assert run_json(roots, "--since", _ts(5), *args)["meta"]["unique_prs"] == 0  # 結果も期間の前


def _token(minute: int, total: int, last: int | None) -> dict:
    info = {"total_token_usage": {"input_tokens": total, "cached_input_tokens": 0, "output_tokens": 1}}
    if last is not None:
        info["last_token_usage"] = {"input_tokens": last, "cached_input_tokens": 0, "output_tokens": 1}
    return {"timestamp": _ts(minute), "type": "event_msg", "payload": {"type": "token_count", "info": info}}


def test_codex_seat_across_since_counts_only_the_increase(tmp_path):
    """期間をまたぐ Codex の席は、先頭の session_meta で席を識別し、期間の前の直近の累計からの増分だけを数える。"""
    mod = _module()
    codex = tmp_path / "codex"
    _jsonl(
        codex / "2026/09/10/rollout-a.jsonl",
        [
            {"timestamp": _ts(30), "type": "session_meta", "payload": {"cwd": WT}},
            {"timestamp": _ts(30), "type": "turn_context", "payload": {"model": "gpt-x"}},
            _token(31, 1000, 1000),  # 期間の前
            _token(33, 1500, 500),
            _token(34, 1500, 500),  # 同じ累計の再掲
            _token(35, 1800, 300),
        ],
    )
    [seat] = mod.read_codex(codex, 30, since=mod.parse_ts(_ts(32)))
    assert (seat.model, seat.tokens["input"], seat.tokens["out"]) == ("gpt-x", 800, 0)
    assert seat.usage.calls == 2
    assert mod.read_codex(codex, 30, since=mod.parse_ts(_ts(36))) == []  # 期間の内に累計が動かない席は数えない


def test_default_axes_do_not_resolve_the_repository(tmp_path, monkeypatch, capsys):
    """--repo を渡さない既定の集計では、origin を読む git を起動しない。"""
    mod = _module()
    roots = build(tmp_path)

    def boom(*_a, **_k):
        raise AssertionError("repo_key を呼んだ")

    monkeypatch.setattr(mod.usage_ledger, "repo_key", boom)
    argv = ["--claude-root", str(roots["claude"]), "--codex-root", str(roots["codex"]), "--kiro-root", str(roots["kiro"])]
    assert mod.main([*argv, "--format", "json", "--usage-root", _empty_ledger(tmp_path)]) == 0
    assert "unique_prs" not in json.loads(capsys.readouterr().out)["meta"]


def test_md_with_repo_shows_the_target_repository_prs(tmp_path):
    """--repo を渡した Markdown は、対象リポジトリの一意な PR・費用・所要を出し、0 本なら理由を出す（cross-review r1-1）。"""
    roots = build(tmp_path)
    _add_session(roots, SID2, "10.17.0")
    p = run(roots, "--by", "version", "--repo", "acme/secret-repo", "--usage-root", _empty_ledger(tmp_path))
    assert p.returncode == 0, p.stderr
    # 期間全体は 1 本で、費用は両方の会話の 3 層の和（12 応答）
    assert "PR（期間全体・一意）: 1 本" in p.stdout and f"{12 * U_COST / 1e6:.2f}M" in p.stdout
    p = run(roots, "--by", "version", "--repo", "devbasex/ai-plugins", "--usage-root", _empty_ledger(tmp_path))
    assert p.returncode == 0, p.stderr
    assert "0 本（計測不能" in p.stdout
    # 従来の「PR 1 本あたり」の表（層別の換算・外部 CLI の列）は対象リポジトリの節とは別に残す（cross-review r2-5）
    head, target = p.stdout.split("## 対象リポジトリの PR 1 本あたり")
    assert "conductor 換算" in head and "claude 席 換算" in head and "conductor 換算" not in target.split("## フェーズごと")[0]


def test_md_release_axis_alone_keeps_the_released_prs_column(tmp_path, monkeypatch, capsys):
    """--repo を省いた --by release の Markdown でも「載った PR」の列を出す（cross-review r2-3）。"""
    mod = _module()
    roots = build(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    monkeypatch.setattr(mod.usage_ledger, "repo_key", lambda _p: "acme__secret-repo")

    class FakeMap:
        def prs_of(self, v):
            return [7, 8] if v == "10.16.0" else []

        def place(self, prs, end):
            return types.SimpleNamespace(version="10.16.0", by="changelog")

    monkeypatch.setattr(mod.release_map, "load_prs", lambda *_a: [])
    monkeypatch.setattr(mod.release_map.ReleaseMap, "from_repo", classmethod(lambda cls, *_a: FakeMap()))
    argv = ["--claude-root", str(roots["claude"]), "--codex-root", str(roots["codex"]), "--kiro-root", str(roots["kiro"])]
    assert mod.main([*argv, "--by", "release", "--no-ledger"]) == 0
    out = capsys.readouterr().out
    header = next(line for line in out.splitlines() if "載った PR" in line and "|" in line)
    row = next(line for line in out.splitlines() if line.startswith("| 10.16.0"))
    assert header.split("|").index(" 載った PR ") == row.split("|").index(" 2 ")
    assert "## 対象リポジトリの PR 1 本あたり" in out  # 一意な PR の節も出す


def test_md_release_axis_shows_release_without_session_prs(tmp_path, monkeypatch, capsys):
    """会話が PR を作らずに寄った版も、載った PR を出し、換算の欄は - にする（cross-review r3-4）。"""
    mod = _module()
    roots = build(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    monkeypatch.setattr(mod.usage_ledger, "repo_key", lambda _p: "acme__secret-repo")

    class FakeMap:
        def prs_of(self, v):
            return [7, 8, 9] if v == "10.16.0" else []

        def place(self, prs, end):
            return types.SimpleNamespace(version="10.16.0", by="time")

    real_scan = mod.scan_file

    def no_prs(*a, **k):
        s = real_scan(*a, **k)
        s.prs = set()
        return s

    monkeypatch.setattr(mod, "scan_file", no_prs)
    monkeypatch.setattr(mod.release_map, "load_prs", lambda *_a: [])
    monkeypatch.setattr(mod.release_map.ReleaseMap, "from_repo", classmethod(lambda cls, *_a: FakeMap()))
    argv = ["--claude-root", str(roots["claude"]), "--codex-root", str(roots["codex"]), "--kiro-root", str(roots["kiro"])]
    assert mod.main([*argv, "--by", "release", "--no-ledger"]) == 0
    out = capsys.readouterr().out.split("## 対象リポジトリの PR 1 本あたり")[0]
    header = next(line for line in out.splitlines() if "載った PR" in line and "|" in line).split("|")
    row = next(line for line in out.splitlines() if line.startswith("| 10.16.0")).split("|")
    assert row[header.index(" 載った PR ")] == " 3 " and row[header.index(" PR ")] == " 0 "
    assert row[header.index(" conductor 換算 ")] == " - "


def test_session_active_only_in_subagents_keeps_its_pr(tmp_path):
    """親の行が期間の外でも、サブエージェントが期間の内に PR を作った会話を数える（cross-review r1-3）。"""
    claude = tmp_path / "claude"
    proj = claude / "-work-x"
    _jsonl(
        proj / f"{SID}.jsonl",
        [
            {
                "type": "user",
                "isMeta": True,
                "timestamp": _ts(0),
                "message": {"content": [{"type": "text", "text": "Base directory for this skill: /h/ai-plugins/ndf/10.16.0/skills/x"}]},
            },
            _assistant(1, "m1", U, [_agent("a1", "ndf:worker", "実装: #1")]),  # 期間の前に Agent を起動
            _result(90, "a1", "done"),  # 結果は期間の後
        ],
    )
    sub = proj / SID / "subagents"
    _jsonl(
        sub / "agent-a1.jsonl",
        [_assistant(20, "s1", U, [_bash("t1", "gh pr create")]), _result(21, "t1", "https://github.com/acme/secret-repo/pull/5\n")],
    )
    (sub / "agent-a1.meta.json").write_text(json.dumps({"description": "実装: #1", "spawnDepth": 1}), encoding="utf-8")
    (tmp_path / "codex").mkdir()
    (tmp_path / "kiro").mkdir()
    roots = {"claude": claude, "codex": tmp_path / "codex", "kiro": tmp_path / "kiro"}
    r = run_json(roots, "--since", _ts(10), "--until", _ts(60), "--repo", "acme/secret-repo", "--no-ledger")
    assert r["meta"]["unique_prs"] == 1 and r["meta"]["pr_cost_total"] == U_COST
    assert r["meta"]["pr_minutes_total"] == 1  # 子が動いた 20→21 の 1 分を会話の所要に入れる（cross-review r2-1）


def test_overlapping_parent_and_subagent_activity_is_counted_once(tmp_path):
    """親と子の行が同じ区間に重なっても、会話の所要はその区間を一度だけ数える（cross-review r2-1）。"""
    mod = _module()
    proj = tmp_path / "-work-x"
    _jsonl(
        proj / f"{SID}.jsonl",
        [
            {
                "type": "user",
                "isMeta": True,
                "timestamp": _ts(0),
                "message": {"content": [{"type": "text", "text": "Base directory for this skill: /h/ai-plugins/ndf/10.16.0/skills/x"}]},
            },
            _assistant(4, "m1", U),
            _assistant(8, "m2", U),
        ],
    )
    _jsonl(proj / SID / "subagents" / "agent-a1.jsonl", [_assistant(2, "s1", U), _assistant(6, "s2", U)])
    [s], _, _ = mod.read_claude(tmp_path, 1800)
    assert s.active == 8 * 60  # 0→8 の 8 分。親の 8 分と子の 4 分を足した 12 分にしない


def test_usage_repeated_across_since_is_counted_in_one_period(tmp_path):
    """同じ message.id の usage が期間の境の前後に再掲されたら、最初の出現の期間だけに数える（cross-review r1-4）。"""
    mod = _module()
    f = tmp_path / "a.jsonl"
    _jsonl(f, [_assistant(1, "m1", U), _assistant(3, "m1", U), _assistant(4, "m2", U)])
    before = mod.scan_file(f, until=mod.parse_ts(_ts(2))).usage.calls
    after = mod.scan_file(f, since=mod.parse_ts(_ts(2))).usage.calls
    assert (before, after) == (1, 1)  # m1 は前の期間だけ、m2 は後の期間だけ


def test_usage_settled_after_until_is_counted_once_with_the_final_value(tmp_path):
    """境の前に途中値、境の後に確定値が出た応答は、隣り合う 2 期間の和が通算の確定値と一致する（cross-review r2-2）。"""
    mod = _module()
    f = tmp_path / "a.jsonl"
    partial, final = dict(U, output_tokens=10), dict(U, output_tokens=100)
    _jsonl(f, [_assistant(1, "m1", partial), _assistant(3, "m1", final)])
    edge = mod.parse_ts(_ts(2))
    before, after, whole = (mod.scan_file(f, until=edge).usage, mod.scan_file(f, since=edge).usage, mod.scan_file(f).usage)
    assert (before.calls, after.calls) == (1, 0)  # 帰属は最初に現れた行の時刻
    assert before.out + after.out == whole.out == 100


def test_target_prs_are_matched_once_per_session(tmp_path, monkeypatch):
    """対象 PR の照合は会話ごとに一度だけ。版ごと・期間全体・ID の出力で使い回す（cross-review r1-5）。"""
    mod = _module()
    roots = build(tmp_path)
    _add_session(roots, SID2, "10.17.0")
    sessions, _, _ = mod.collect(roots["claude"], roots["codex"], roots["kiro"])
    calls = []
    real = mod.pr_numbers
    monkeypatch.setattr(mod, "pr_numbers", lambda urls, slug: calls.append(slug) or real(urls, slug))
    mod.aggregate(sessions, ["version"], repo="acme/secret-repo")
    mod.write_sessions(tmp_path / "ids.txt", sessions, "acme/secret-repo")
    assert len(calls) == len(sessions) == 2


def test_unresolvable_repo_stops_before_scanning(tmp_path, monkeypatch):
    """明示した --repo を解決できなければ、ログを読む前に終了コード 2（cross-review r1-6）。"""
    mod = _module()
    roots = build(tmp_path)
    monkeypatch.setattr(mod, "collect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("走査した")))
    argv = ["--claude-root", str(roots["claude"]), "--codex-root", str(roots["codex"]), "--kiro-root", str(roots["kiro"])]
    for bad in (str(tmp_path / "no-such-dir"), str(tmp_path)):  # 誤記のパス・origin の無いディレクトリ
        with pytest.raises(SystemExit) as e:
            mod.main([*argv, "--repo", bad, "--no-ledger"])
        assert e.value.code == 2


def test_unresolvable_repo_does_not_echo_credentials(tmp_path):
    """認証情報入りの clone URL を --repo に渡しても、トークンを標準エラーへ出さない（cross-review r2-4）。"""
    for by in ("version", "release"):  # 軸 release の既定（カレント）へ落ちる経路でも同じ
        url = "https://user:ghp_SECRET123@github.com/acme/secret-repo.git?access_token=QUERY_SECRET"
        p = run(build(tmp_path / by), "--by", by, "--repo", url)
        assert p.returncode == 2 and "--repo" in p.stderr
        # 入力の値は表示しない（クエリの access_token も残さない。cross-review r3-1）
        assert "ghp_SECRET123" not in p.stderr and "user:" not in p.stderr and "QUERY_SECRET" not in p.stderr


def test_repo_slug_ignores_case(tmp_path):
    """GitHub の所有者・リポジトリの大文字小文字は区別しない（cross-review r1-6）。"""
    r = run_json(build(tmp_path), "--by", "version", "--repo", "Acme/Secret-Repo", "--no-ledger")
    assert r["meta"]["unique_prs"] == 1
