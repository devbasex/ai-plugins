"""並列の進行の観測（#2436）: 一覧の状態を耐久ワークフローの値へ写す。問いの判定は `parallel_watch` にある（#2649）。

耐久ワークフローの中で呼ぶため、耐久ステップもイベントも呼ばず、渡された値だけで決める（`parallel_run.py` の決まり）。
"""

from __future__ import annotations

import parallel_plan as pp


def apply_observed_states(run, rows: dict) -> list[str]:
    """一覧の状態を各レーンへ写し、承認待ちへ移ったレーンを返す。一覧に無い起動済みのレーンは止まったと読む。"""
    states = {n: r.get("state") for n, r in rows.items()}
    noticed = []
    for n in run.order:
        st = states.get(n)
        if st is None and run.lanes[n].get("started"):
            st = "stopped"
        if st and st != run.lanes[n].get("state"):
            run.lanes[n]["state"] = st
            if st != "stopped" and n in run.revive:  # 起こしたレーンが動き出したら待ちを外す
                run.revive = [x for x in run.revive if x != n]
                run.revive_at.pop(n, None)
            if st == "awaiting_approval":
                noticed.append(n)
    return noticed


def _asking_lanes(run) -> set[str]:
    """答えていない問い（まとめた問いの `merged` も含む）を送ったレーンの名前。"""
    return {x for q in run.questions for x in (q.get("lane"), *(m.get("lane") for m in q.get("merged") or [])) if x}


def _plan_view(run) -> pp.ParallelPlan:
    """起動の判定に使う計画の形（耐久ワークフローの入力から作る。ファイルを読まない）。"""
    lanes = [
        {"name": n, "input": {"kind": "loop", "loop": n, "args": ["--milestones", n]}, "plans": 1, "touches": [], "after": run.after[n]}
        for n in run.order
    ]
    return pp.ParallelPlan.model_construct(version=1, name=run.name, rules=[], lanes=[pp.Lane.model_construct(**ln) for ln in lanes])
