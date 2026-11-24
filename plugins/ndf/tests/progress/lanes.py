"""ステートマシンの `next` を同じプロセスで進めるテストが共有する手（pytest は集めない）。

`turn` は `next` を打って起動した作業を同じプロセスで流し（`run.work`）、`answer` は承認ゲートに回答する。
作業（プラン）は偽物で、ステートマシンの DB への書き込みだけが本物である。
"""

from __future__ import annotations

from pathlib import Path

from ndf.contracts import result
from ndf.progress import gate, invoke, run, store


def values(cwd: Path, branch: str) -> dict:
    """偽の作業が出す文脈の値の組（工程の定義の outputs のキーの値）。"""
    return {
        "worktree": str(cwd),
        "branch": branch,
        "design_pr": 7,
        "pr": 8,
        "delivery": {"target": "t", "kind": "auto", "trigger": "cd", "channel": "production", "versioned": True},
        "product_version": "11.0.0",
        # すべての単位の PR を集めた時点のコミット（工程「実装」の出力。#3879）
        "gathered": "0" * 40,
        # 承認ゲートの直前の MVV 判定（進め方 normal は省かない）
        "mvv_gate": {"gate": "design", "skip": False, "verdict": None, "why": ["進め方 normal は承認ゲートを MVV 判定で省かない"]},
    }


def ok_result(r: invoke.StageRun, values: dict) -> invoke.StageResult:
    """定義した出力を `values` から持つ ok の結果。"""
    outputs = {k: values[k] for k in r.outputs}
    return invoke.finished(r, result.result("plan", "ok", f"{r.step} を終えた", outputs=outputs or None))


def answer(st: store.Store, tid: str, stage: str, value: str, reason: str, notes: Path | None = None) -> dict:
    """待っている手へ回答する。承認ゲート（`ndf.listen`）は質問の id を添えた `ndf answer`、ほかの待ちは `ndf progress answer`。"""
    v = run.read(st, tid)
    m = run.decide(v)
    if m.action != run.Action.GATE or m.step is None or not invoke.is_listen(m.step):
        return run.answer(st, tid, stage, value, reason, notes)
    q = gate.question(v, m)
    key = f"test-{q.id}-{value}"
    return gate.answer(st, tid, gate.Answer(q.id, value, reason, "human:test", key, notes))


def turn(st: store.Store, tid: str, cwd: Path, env: run.Env) -> dict:
    """`next` を打ち、作業を起動したらその作業を同じプロセスで流す。"""
    out = run.advance(st, tid, cwd, env)
    if out["status"] == "ok" and "next" in out and out["next"]["argv"][2] == "wait":
        out = run.work(st, tid, cwd, env)
    return out


class _NoRecords:
    def read(self, number: int) -> str:
        return ""

    def write(self, number: int, body: str) -> None:
        return None
