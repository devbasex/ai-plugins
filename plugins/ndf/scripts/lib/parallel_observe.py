"""並列の進行の観測（#2436）: 一覧の状態を耐久ワークフローの値へ写す。

耐久ワークフローの中で呼ぶため、耐久ステップもイベントも呼ばず、渡された値だけで決める（`parallel_run.py` の決まり）。
"""

from __future__ import annotations


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
