"""耐久ワークフロー `parallel-<計画名>` のレーンの起動を、計画の進行表とレーンの進行表から決める（#3092 の受け入れ条件 13・14）。

版の印 `parallel-progress-tables` の後の記録で、計画の進行表のある計画にだけ使う。`after` の判定はレーンの停止ではなく
指す行の完了で決まり、前のスプリントを終えて止まったレーンは同じレーンの次の行で起こし直す。
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import durable
import parallel_plan as pp
import parallel_tables as ptab


@durable.step(name="parallel.wakeable")
def step_wakeable(main: str, name: str) -> dict:
    """計画とレーンの進行表から、起こせるレーンと行（`after` を満たす未着手の行があり、実行中の行が無いレーン）を読む
    （#3092 の受け入れ条件 13・14）。`tables` は計画の進行表があるか（無ければ取り込み前の計画として今までの道で起動する）。"""

    got = ptab.read_plan(Path(main), name)
    if got is None:
        return {"tables": False, "wake": [], "ended": []}
    states = {ln: {r["state"] for r in rows} for ln, rows in got[1].items()}
    ended = [ln for ln, st in states.items() if "running" not in st and st - {"pending"}]  # 実行中の行が無く、終えた行がある
    return {"tables": True, "wake": [[ln, row] for ln, row in ptab.wake_list(Path(main), name)], "ended": sorted(ended)}


@durable.step(name="parallel.row-input")
def step_row_input(main: str, name: str, lane: str, row: int) -> dict:
    """レーン `lane` の順番表の行 `row` を始めさせる起動の入力（`relay.py` の引数の列と打つ場所）。"""
    plan = pp.load_valid_plan(Path(main), name)
    first = ptab.row_input(Path(main), name, lane, row)
    return {"argv": ["spawn", "--name", lane, "--json", "--", first], "cwd": str(plan.lane(lane).repo_dir(Path(main)))}


def spawn_rows(run, main: str, view: pp.ParallelPlan, started: set, wake: list, ended: list, admit: Callable) -> None:
    """計画とレーンの進行表から起動を決める（#3092 の受け入れ条件 13・14）。`after` の満たされた未着手の行を持つレーンのうち、
    まだ起動していないレーンは計画の最初の入力で起こし、前のスプリントを終えて止まったレーンは次の行の入力で起こし直す。
    `after` の判定はレーンの停止ではなく、指す行の完了で決まる。レーンの終わりはレーンの進行表の行の終わり（決定 17）で数え、
    `parallel.py end` の記録を待たない。"""
    for n, st in run.lanes.items():
        if n in ended:
            st["ended"] = st.get("ended") or {"at": None, "by": "progress", "request": None}
    left = {n for n, st in run.lanes.items() if st.get("given_up")} | set(run.left_stopped)  # 人が止めたレーンは起こさない
    rows = {ln: int(row) for ln, row in wake if ln not in left}
    run.wakeable = sorted(rows)
    ready = [ln for ln in view.lanes if ln.name in rows and ln.name not in started]
    for n in sorted(set(rows) & run.stopped()):
        spec = step_row_input(main, run.name, n, rows[n])
        rec = run.launch_once(f"row-{n}-{rows[n]}", n, spec["argv"], spec["cwd"], fresh=True)
        if rec.get("result") == "up":  # 次の行で動き出したレーンは、終わりの記録を消して止まったと数えない
            run.lanes[n].pop("ended", None)
            run.lanes[n]["state"] = "running"
    admit(run, main, ready)
