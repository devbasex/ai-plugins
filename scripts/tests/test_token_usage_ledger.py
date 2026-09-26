"""`scripts/token-usage.py` が使用量の帳簿（plugins/ndf/scripts/lib/usage_ledger.py）の行を版と層へ加える（#1142 の不足 f）。

会話の記録の作り方は test_token_usage.py の合成の記録を使う。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_token_usage import U_COST, _jsonl, _ts, build  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[1] / "token-usage.py"

LEDGER_USAGE = {"input_tokens": 10, "output_tokens": 100, "cache_read_input_tokens": 1000,
                "cache_creation_input_tokens": 200,
                "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 200}}


def row(minute: int, kind: str, version: str = "10.16.0") -> dict:
    return {"at": _ts(minute), "ndf_version": version, "source": "supervise", "plan": "/tmp/p.json", "step": "s",
            "kind": kind, "model": "claude-opus-5", "usage": LEDGER_USAGE, "model_usage": None,
            "cost_usd": 0.1, "turns": 3, "seconds": 60.0, "session_id": None}


def run_json(roots: dict, usage: Path, *args: str) -> dict:
    p = subprocess.run([sys.executable, str(SCRIPT), "--claude-root", str(roots["claude"]),
                        "--codex-root", str(roots["codex"]), "--kiro-root", str(roots["kiro"]),
                        "--usage-root", str(usage), "--format", "json", *args], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_ledger_rows_join_the_session_layers(tmp_path):
    roots = build(tmp_path)
    usage = tmp_path / "usage"
    # work は worker、judge は supervisor。full は会話が残り別に数えられるため読まない。範囲の外は寄せない
    _jsonl(usage / "acme__secret-repo.jsonl", [row(20, "work"), row(21, "judge"), row(22, "full"),
                                              row(900, "work")])
    r = run_json(roots, usage)
    [pr] = r["per_pr"]
    assert pr["supervisor_cost"] == 2 * U_COST + U_COST
    assert pr["worker_cost"] == 2 * U_COST + U_COST
    ledger_roles = {(x["layer"], x["role"]) for x in r["per_role"] if x["agent_type"] == "claude -p"}
    assert ledger_roles == {("supervisor", "judge"), ("worker", "work")}
    w = next(x for x in r["per_role"] if x["agent_type"] == "claude -p" and x["role"] == "work")
    assert w["w1h"] == 200 and w["w5"] == 0
    assert r["meta"]["unlinked_ledger"] == 1


def test_without_ledger_values_stay_as_before(tmp_path):
    roots = build(tmp_path)
    r = run_json(roots, tmp_path / "none")
    [pr] = r["per_pr"]
    assert pr["supervisor_cost"] == 2 * U_COST and pr["worker_cost"] == 2 * U_COST
    assert r["meta"]["unlinked_ledger"] == 0


def test_until_drops_later_ledger_rows(tmp_path):
    roots = build(tmp_path)
    usage = tmp_path / "usage"
    _jsonl(usage / "x.jsonl", [row(20, "work"), row(25, "judge")])
    r = run_json(roots, usage, "--until", _ts(22))
    roles = {x["role"] for x in r["per_role"] if x["agent_type"] == "claude -p"}
    assert roles == {"work"}
