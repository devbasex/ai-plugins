"""`parallel.py request-ask` の送り分け（#3603 の「送り分けの順序」）: 送り手の種類・問いの対象・同じ問いの答えの引き継ぎ。

送り手を確かめた後の問いを、次の順で最初に当たった分岐へ送る。読む処理（スプリント・`gh pr view`・`git rev-parse`・台帳）を済ませてから、
最後に台帳へ高々 1 行だけ書く。書く直前に hook の上限の秒（`deadline_at`）が尽きていれば書かない（I10）。

| 順 | 分岐 | 入る条件 |
| --- | --- | --- |
| 1 | 設計承認の依頼（`gate1`） | 送り手が 1 スプリントのレーンで、区分が `ndf-gate:1` で、承認の行が設計 PR を 1 本だけ指す（`parallel_design_gate.design_gate`。I1） |
| 2 | 同じ問い（`reused`） | 送り手が一時レーンで、問いの対象を求められ、同じ名前・区分・対象の前の依頼がある（I5〜I8） |
| 3 | `ask` の依頼 | 上の 2 つに当たらない問いのすべて |

開発ループのレーンの設計承認の依頼は、この順序を通らずに今どおり `sprint_loop_gate.lane_gate1` が送る（I3）。
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import parallel_external as pe
from gate1_ids import gate1_id
import parallel_lane as pl
import parallel_ledger as plg
from parallel_question import AskTarget, Prior, prior_of, target_line


def sender_kind(main: Path, name: str, lane: str) -> str:
    """送り手の種類: 計画 `name` の宣言の 1 スプリントのレーン（`sprint`）・開発ループのレーン（`loop`）・一時レーン（`helper`）。
    宣言に `lane` が無い・読めなければ `helper`（`sender_of` が一時レーンと認めたときだけ同じ問いの判定に入る。I5）。"""
    import parallel_hold as ph

    got = ph.lane_input(main, name, lane)
    if got is None:
        return "helper"
    return "loop" if got.get("kind") == "loop" else "sprint"


def ask_target(tool_input: dict, cwd: str) -> AskTarget:
    """対象の行の値から問いの対象を作る（#3603 の決定 7）。ブランチ名は `git check-ref-format --branch` を通り、`cwd` で
    `refs/heads/<名>` の先端の 40 桁を読めたときだけ `<名>@<40 桁>`、`#<番号>` はそのまま。ほかは空（前の依頼と比べない。I6）。"""
    raw = target_line(tool_input)
    if re.fullmatch(r"#\d+", raw):
        return AskTarget(raw, raw)
    if not raw or raw.startswith("-"):
        return AskTarget(raw, "")
    try:
        ok = subprocess.run(["git", "check-ref-format", "--branch", raw], capture_output=True, text=True, cwd=cwd or None)
        got = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--verify", "--quiet", f"refs/heads/{raw}^{{commit}}"], capture_output=True, text=True
        ) if ok.returncode == 0 else None  # fmt: skip
    except OSError:
        return AskTarget(raw, "")
    sha = got.stdout.strip() if got is not None and got.returncode == 0 else ""
    return AskTarget(raw, f"{raw}@{sha}" if re.fullmatch(r"[0-9a-f]{40}", sha) else "")


def record_reused(main: Path, name: str, lane: str, prior: Prior, source: str, target: str, ledger_id: str) -> bool:
    """答えの引き継ぎの `reused` の行を 1 行書く（耐久ワークフローへは知らせない。I8）。鍵は `reused:<判定の記録の id>` で、
    同じ鍵の行が既にあれば書かずに真。書けなければ偽（呼び出し元は答えを使わずに新しい依頼を送る）。"""
    msg = {
        "kind": "reused", "id": prior.id, "lane": lane, "source": source, "target": target, "ledger_id": ledger_id,
        "answered": prior.answer is not None, "at": pe.utc_stamp(),
    }  # fmt: skip
    try:
        plg.append_ledger_row(main, name, "reused", f"reused:{ledger_id}", msg, by=lane)
    except OSError:
        return False
    return True


def expired(deadline_at, now=None) -> bool:
    """台帳へ書く直前に、hook の上限の秒が尽きたか（`deadline_at` は UNIX 時刻の秒。無いか数でなければ偽。I10）。"""
    if isinstance(deadline_at, bool) or not isinstance(deadline_at, (int, float)):
        return False
    return (now or time.time)() >= deadline_at


@dataclass
class Routed:
    """送り分けの結果。`kind` は gate1 / reused / ask / late（上限の秒が尽きて書かなかった）。`sent` は ask を送った結果。"""

    kind: str
    id: str = ""
    metrics: dict = field(default_factory=dict)
    sent: Optional[plg.Sent] = None


def _design_gate_or_none(main: Path, name: str, lane: str, tool_input: dict, rows: list[dict]):
    """設計承認の依頼の中身。組めなければ None（今どおり ask で送る。決定 3）。"""
    import parallel_design_gate as pdg

    try:
        return pdg.design_gate(main, name, lane, tool_input, rows)
    except Exception:  # noqa: BLE001 — 組めなければ今どおり ask で送る（決定 3）
        return None


def _route_gate1(main: Path, name: str, lane: str, gate, rows: list[dict], ledger_id: str, deadline_at) -> Routed:
    """分岐 1: 設計承認の依頼を送る。同じ先頭の打ち直しは送らずに already を返す（I2）。"""
    import parallel_design_gate as pdg

    rid = gate1_id(lane, gate.pr, gate.stamp, gate.suffix)
    state = {"sprint_state": gate.state}
    if pdg.gate1_row(rows, rid) is not None:  # 同じ先頭の打ち直し（I2）
        return Routed("gate1", rid, {**state, "already": True})
    if expired(deadline_at):
        return Routed("late")
    extra = {"returned": True} if gate.returned else {}
    pl.request_gate1(
        main, name, lane, gate.issue, gate.pr, gate.report, gate.state, gate.stamp, suffix=gate.suffix,
        verdict=gate.verdict, reasons=gate.reasons, boundary=gate.boundary, ledger_id=ledger_id, **extra,
    )  # fmt: skip
    return Routed("gate1", rid, state)


def _route_reused(main: Path, name: str, lane: str, prior: Prior, source: str, target: str, ledger_id: str, deadline_at) -> Routed | None:
    """分岐 2: 同じ問いの前の答えを引き継ぐ。記録に失敗したら None（呼び出し元は分岐 3 へ落ちる）。"""
    if expired(deadline_at):
        return Routed("late")
    if record_reused(main, name, lane, prior, source, target, ledger_id):
        answer = {"answer": prior.answer} if prior.answer is not None else {}
        return Routed("reused", prior.id, {"target": target, **answer})
    return None


def route_ask(main: Path, name: str, lane: str, got: dict, rows: list[dict]) -> Routed:
    """問いを 3 つの分岐の最初に当たったものへ送る。台帳へ書けなければ `OSError`（呼び出し元が 2 で終える）。"""
    import parallel_design_gate as pdg

    tool_input, cwd = got.get("tool_input") or {}, got.get("cwd") or ""
    ledger_id, deadline_at = str(got.get("ledger_id") or ""), got.get("deadline_at")
    source = str((tool_input.get("metadata") or {}).get("source") or "")
    kind = sender_kind(main, name, lane)
    gate = _design_gate_or_none(main, name, lane, tool_input, rows) if kind == "sprint" and source == pdg.GATE1_SOURCE else None
    if gate is not None:  # 分岐 1
        return _route_gate1(main, name, lane, gate, rows, ledger_id, deadline_at)
    target = ask_target(tool_input, cwd).target
    prior = prior_of(rows, lane, source, target) if kind == "helper" and target else None  # 同じ問いは一時レーンだけ（I5）
    if prior is not None and (reused := _route_reused(main, name, lane, prior, source, target, ledger_id, deadline_at)):  # 分岐 2
        return reused
    if expired(deadline_at):  # 分岐 3
        return Routed("late")
    sent = pl.request_ask(main, name, lane, tool_input, cwd, ledger_id, target=target or None)
    return Routed("ask", str(sent), {"target": target or None}, sent)
