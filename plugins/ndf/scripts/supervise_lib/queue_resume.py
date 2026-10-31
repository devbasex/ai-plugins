"""キューの続き（`supervise.py queue --resume <done>`。#1426 の決定 2）。

前の起動が done の隣に残した一覧（`<done>.plans.json` の `stages`・`max`）からステージの並びを読み、done の `items` の
完了していないプラン（結果が 止まった・報告なし・関門）を、それぞれが終えたステップから続ける。後続のステージは今の
queue と同じ条件（前のステージがすべて完了）で流れる。完了したプランは耐久の記録から結果を返し、流し直さない（I4）。
一覧が無い・`stages` が無いときは何も流さない（I6）。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import jsonio
from step_result import result
from supervise_lib.paths import queue_plans_path, state_dir_of
from supervise_lib.queue import named

TOOL = "supervise-queue"
RESUMABLE = ("止まった", "報告なし", "関門")  # 続ける対象の結果
KEPT = "前の起動の開始"  # 一覧に残した前の開始のステップ（続ける対象ではない）


@dataclass(frozen=True)
class ResumeTarget:
    """続けるプラン 1 本と開始のステップ（None は頭から）。"""

    plan: str
    step: str | None
    reason: str


class ResumeError(Exception):
    """続きを組めない（一覧が無い・`--from` のプランが無い）。"""


def _json_of(path: Path) -> dict | None:
    data = jsonio.read(path, missing=None, broken=None)
    return data if isinstance(data, dict) else None


def last_step(plan: str) -> str | None:
    """プランの `state.json` の `log` の最後のステップ（judge は落ちたステップの後の判断なので飛ばす）。無ければ None。"""
    data = _json_of(state_dir_of(plan) / "state.json") or {}
    for row in reversed(data.get("log") or []):
        if isinstance(row, dict) and row.get("id") and row.get("type") != "judge":
            return str(row["id"])
    return None


def parse_from(values: list[str]) -> dict[str, str]:
    """`--from <プラン名>=<ステップ>` の並び。形が違えば ValueError。"""
    out = {}
    for v in values or []:
        name, sep, step = v.partition("=")
        if not sep or not name.strip() or not step.strip():
            raise ValueError(f"--from は <プラン名>=<ステップ> で渡す: {v}")
        out[name.strip()] = step.strip()
    return out


def resume_targets(done: Path, overrides: dict[str, str] | None = None) -> tuple[list[list[str]], int, list[ResumeTarget]]:
    """前の起動の (ステージの並び, --max, 続けるプランと開始のステップ)。組めなければ ResumeError。

    done が無い（前の起動が落ちて書けなかった）ときは、一覧に残した前の開始のステップをそのまま使う
    （同じ耐久の記録から続く）。`overrides` は `--from` の上書きで、名前の照合は `{queue_pr:<名>}` と同じ。"""
    listing = _json_of(queue_plans_path(done))
    if listing is None:
        raise ResumeError(f"前の起動の一覧が無い（{queue_plans_path(done)}）")
    stages = listing.get("stages")
    if not isinstance(stages, list) or not stages or not all(isinstance(s, list) for s in stages):
        raise ResumeError("前の起動の一覧にステージの並びが無い（この版より前の queue が書いたもの）")
    plans = [p for s in stages for p in s]
    prev = _json_of(done)
    kept = [ResumeTarget(p, s, KEPT) for p, s in (listing.get("starts") or {}).items() if p in plans and s]
    if prev is None:
        found = kept
    else:
        # 続けないプラン（完了など）も前の開始のステップを保つ。落とすと別の実行の回になり、元の関門の結果を拾う
        items = [i for i in prev.get("items") or [] if isinstance(i, dict) and i.get("result") in RESUMABLE]
        found = kept + [ResumeTarget(i["plan"], last_step(i["plan"]), i["result"]) for i in items if i.get("plan") in plans]
    by_plan = {t.plan: t for t in found}
    for name, step in (overrides or {}).items():
        hits = [p for p in plans if named(p, name)]
        if not hits:
            raise ResumeError(f"--from のプラン {name} が前の起動の一覧に無い")
        for p in hits:
            by_plan[p] = ResumeTarget(p, step, "--from")
    return stages, int(listing.get("max") or 3), [by_plan[p] for p in plans if p in by_plan]


def cmd_queue_resume(done: str, from_: list[str], max_: int | None, poll: float, run_queue) -> dict:
    """前の起動の続きを流す。`run_queue` は `queue.cmd_queue`（テストで差し替える）。"""
    done_path = Path(done)
    try:
        stages, prev_max, found = resume_targets(done_path, parse_from(from_))
    except (ResumeError, ValueError) as e:
        metrics = {"plans": 0, "stopped": 0, "gate": 0, "not_run": 0, "done": str(done_path)}
        return result(TOOL, "stopped", f"続きを組めない: {e}。何も流さない", [], metrics, next="元の queue のコマンドを打ち直す")
    starts = {t.plan: t.step for t in found if t.step}
    # 続ける対象（前の結果か --from）は、関門の記録があっても新しい実行の回で流す。同じ開始のステップでも、
    # 承認を記録した後に過去の関門の結果を返し続けないため
    rerun = [t.plan for t in found if t.reason != KEPT]
    for t in found:
        print(f"supervise-queue: {t.plan} を {t.step or '頭'} から続ける（{t.reason}）", file=sys.stderr, flush=True)
    return run_queue(stages[0], max_ or prev_max, poll, then=stages[1:], done=str(done_path), starts=starts, rerun=rerun)
