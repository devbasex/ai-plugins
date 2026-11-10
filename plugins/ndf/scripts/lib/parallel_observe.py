"""並列の進行の観測（#2436）: 一覧の状態を耐久ワークフローの値へ写し、一時レーンの止まりを問う。

耐久ワークフローの中で呼ぶため、耐久ステップもイベントも呼ばず、渡された値だけで決める（`parallel_run.py` の決まり）。
"""

from __future__ import annotations

import clock
import parallel_plan as pp
import parallel_release as pr


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


def _ask_helper(run, rows: dict) -> None:
    """リリースの途中で一時レーンが止まり、settle もリリース承認ゲートの依頼も、その一時レーンが送った答えていない問い（ask も含む）も
    無ければ問う（受け入れ条件 6g）。一時レーンは問いを保留すると自分のラッパーを止めるため、問いを送って止まったものは問わない。

    耐久ワークフローが起動を打つ記録（#2936 の I4）では、今の起動の立ち上がりを確かめた後に、今の起動より後に止まった行を
    2 回続けて観たときだけ問う。前の起動の止まった行・時刻の無い行は数えない（#2629）。"""
    rel = run.current
    if rel is None or not run.launch or run.helper_asked == run.launch["seq"]:
        return
    name = pr.helper_name(run.name, rel["n"])
    row = rows.get(name) or {}
    if row.get("state") != "stopped" or any(q.get("kind") == "gate2" and q.get("n") == rel["n"] for q in run.questions):
        run.helper_seen.pop(name, None)
        return
    if name in _asking_lanes(run):
        return
    if run.launching:
        rec = run.launches.get(run.launch.get("purpose") or "") or {}
        since = clock.parse(rec.get("at"), naive="utc") if rec.get("result") == "up" and rec.get("at") else None
        at = clock.parse(row.get("at"), naive="utc") if row.get("at") else None
        if since is None or at is None or at.timestamp() < since.timestamp() - 0.5:
            return  # 立ち上がりを確かめる前か、前の起動の行
        run.helper_seen[name] = run.helper_seen.get(name, 0) + 1
        if run.helper_seen[name] < 2:
            return
    run.helper_asked = run.launch["seq"]
    spec = {"argv": run.launch.get("argv"), "cwd": run.launch.get("cwd")}
    run.ask(
        "helper", f"{name}-h{run.launch['seq']}", name, f"一時レーン {name} がリリース {rel['n']} の途中で止まった（settle もリリース承認ゲートの依頼も無い）",
        ["続きから起こし直す", "止める"], n=rel["n"], purpose=run.launch.get("purpose"), launch=spec,
    )  # fmt: skip


def _plan_view(run) -> pp.ParallelPlan:
    """起動の判定に使う計画の形（耐久ワークフローの入力から作る。ファイルを読まない）。"""
    lanes = [
        {"name": n, "input": {"kind": "loop", "loop": n, "args": ["--milestones", n]}, "plans": 1, "touches": [], "after": run.after[n]}
        for n in run.order
    ]
    return pp.ParallelPlan.model_construct(version=1, name=run.name, rules=[], lanes=[pp.Lane.model_construct(**ln) for ln in lanes])
