"""並列の計画の副命令と耐久ワークフローが、計画の進行表とレーンの進行表を読み書きする口（#3092 の受け入れ条件 13・14・16・17）。

計画の状態の正本は計画の進行表（オーガナイザーが書く）とレーンの進行表（レーンの側が書く）で、`plan.json` は計画を
立てたときの入力、計画の台帳はメッセージの記録にだけ使う。規則の判定は `progress_lanes`、書き込みは `progress_db`
が持ち、ここは計画の名前から進行表の ID を引いて呼ぶだけにする。
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Iterator, Optional

import progress_db as pdb
import progress_lanes as plr
import progress_model as pm

ORGANIZER, DURABLE = "organizer", "durable"


def plan_table_id(main: Path, name: str) -> str:
    return pm.table_id("plan", pdb.progress_repo_key(main), name)


def lane_table_name(plan: str, lane: str) -> str:
    return f"{plan}/{lane}"


def _store(main: Path, create: bool) -> Optional[pdb.Store]:
    """進行表の DB。`create` が偽で SQLite のファイルがまだ無ければ None（読むだけの副命令で DB を作らない）。"""
    try:
        url = pdb.default_url(main)
    except pdb.Unreadable:
        return None
    if not create and url.startswith("sqlite:///") and not Path(url[len("sqlite:///") :]).exists():
        return None
    return pdb.connect(url)


@contextlib.contextmanager
def plan_tx(main: Path, create: bool = False) -> Iterator[Optional[object]]:
    """計画の進行表の DB の 1 つのトランザクション。DB が無ければ None を渡す。"""
    store = _store(main, create)
    if store is None:
        yield None
        return
    with pdb.transaction(store) as conn:
        yield conn


def read_plan(main: Path, name: str) -> Optional[tuple[dict, dict, dict]]:
    """（計画の畳み込み, レーン名 → 順番表, レーン名 → レーンの進行表の ID）。進行表が無ければ None。"""
    with plan_tx(main) as conn:
        if conn is None:
            return None
        tid = plan_table_id(main, name)
        if pdb.progress_table(conn, tid) is None:
            return None
        return pdb.plan_and_lanes(conn, tid)


def _first_row(ln) -> dict:
    inp = ln.input
    return {
        "row": 1,
        "issues": list(getattr(inp, "issues", []) or []),
        "loop": getattr(inp, "loop", None),
        "touches": list(ln.touches),
        "after": list(ln.after),
        "args": list(inp.args),
    }


def create_plan_tables(main: Path, plan, max_lanes: Optional[int] = None) -> str:
    """計画の進行表と、レーンごとの進行表と最初の順番表を 1 つのトランザクションで作る（start の CRUD の C）。

    同じ計画の進行表が既にあれば作らずに今の ID を返す（作り直さない。I1）。"""
    with plan_tx(main, create=True) as conn:
        repo, tid = pdb.progress_repo_key(main), plan_table_id(main, plan.name)
        if pdb.progress_table(conn, tid) is not None:
            return tid
        head = [pm.EventDraft("created", ORGANIZER, None, payload={"max_lanes": max_lanes or len(plan.lanes), "rules": list(plan.rules)})]
        head += [
            pm.EventDraft("lane_added", ORGANIZER, None, payload={"lane": ln.name, "touches": list(ln.touches), "after": list(ln.after)})
            for ln in plan.lanes
        ]
        head.append(pm.EventDraft("plan_started", ORGANIZER, None))
        pdb.create_table(conn, "plan", repo, plan.name, None, None, head)
        for ln in plan.lanes:
            rows = [pm.EventDraft("created", ORGANIZER, None), pm.EventDraft("created", ORGANIZER, "1", payload=_first_row(ln))]
            pdb.create_table(conn, "lane", repo, lane_table_name(plan.name, ln.name), tid, None, rows)
        return tid


def record_lane(main: Path, name: str, lane, action: str, reason: str) -> list[int]:
    """`lane add`・`set` を計画の進行表の `lane_added`・`lane_changed` と、足したレーンの進行表にする。

    計画の決まり（I13・I19〜I21）で断られたら `progress_model.Refused` を上げ、何も書かない。進行表が無い計画では何もしない。"""
    with plan_tx(main) as conn:
        if conn is None:
            return []
        tid = plan_table_id(main, name)
        if pdb.progress_table(conn, tid) is None:
            return []
        plan, lanes, ids = pdb.plan_and_lanes(conn, tid)
        kind = "lane_added" if lane.name not in plan["lanes"] else "lane_changed"
        if action == "add" and kind == "lane_changed":
            raise pm.Refused(f"計画 {name} には既にレーン {lane.name} がある")
        draft = pm.EventDraft(
            kind, ORGANIZER, None, reason=reason, payload={"lane": lane.name, "touches": list(lane.touches), "after": list(lane.after)}
        )
        errs = plr.check_lane_change(plan, lanes, draft)
        if errs:
            raise pm.Refused("; ".join(errs))
        seqs = pdb.append_events(conn, tid, [draft])
        if kind == "lane_added":
            rows = [pm.EventDraft("created", ORGANIZER, None)]
            pdb.create_table(conn, "lane", pdb.progress_repo_key(main), lane_table_name(name, lane.name), tid, None, rows)
            lane_id = pm.table_id("lane", pdb.progress_repo_key(main), lane_table_name(name, lane.name))
            # 最初の行も計画の決まり（触る場所の重なり・after の輪）で判定してから書く
            pdb.lane_write(conn, lane_id, pm.EventDraft("row_added", ORGANIZER, "1", reason=reason, payload=_first_row(lane)))
        return seqs


def lane_view(main: Path, name: str) -> dict[str, dict]:
    """レーン名 → レーンの進行表から作った表示（`table`: 順番表の状態・`sprint`: 実行中のスプリントの進行表・`rows`・`asking`）。"""
    got = read_plan(main, name)
    if got is None:
        return {}
    plan, lanes, _ = got
    asking = {str((q["body"] or {}).get("lane")) for q in plan["requests"].values() if q["open"] and (q["body"] or {}).get("lane")}
    out = {}
    for ln, rows in lanes.items():
        running = next((r for r in rows if r["state"] == pm.RUNNING), None)
        if running is not None:
            state = pm.RUNNING
        elif rows and all(r["state"] in (pm.COMPLETED, pm.CANCELLED) for r in rows):
            state = pm.COMPLETED if any(r["state"] == pm.COMPLETED for r in rows) else pm.CANCELLED
        else:
            state = pm.PENDING
        out[ln] = {
            "table": state,
            "sprint": running["sprint"] if running else None,
            "rows": [{"row": r["row"], "issues": r["issues"], "state": r["state"]} for r in rows],
            "asking": ln in asking,
        }
    return out


def table_plan_state(main: Path, name: str, status: Optional[str], running: tuple) -> Optional[str]:
    """計画の進行表から計画の状態の語を返す。進行表が無ければ None（呼ぶ側は取り込み前の計画として扱う）。"""
    got = read_plan(main, name)
    if got is None:
        return None
    state = got[0]["state"]
    if state in (pm.COMPLETED, pm.CANCELLED):
        return "closed"
    if state == pm.RUNNING:
        return "running" if status in running else "lost"
    return "fresh"


def finish_plan(main: Path, name: str) -> bool:
    """全レーンの行が完了か中止で、答えていない依頼が無いときだけ計画の進行表を完了にする（I16）。書いたら真。"""
    with plan_tx(main) as conn:
        if conn is None:
            return False
        tid = plan_table_id(main, name)
        if pdb.progress_table(conn, tid) is None:
            return False
        plan, lanes, _ = pdb.plan_and_lanes(conn, tid)
        if plan["state"] != pm.RUNNING or not plr.plan_complete(lanes, list(plan["requests"].values())):
            return False
        pdb.append_events(conn, tid, [pm.EventDraft("plan_completed", DURABLE, None)])
        return True


def cancel_plan(main: Path, name: str, reason: str) -> list[str]:
    """計画の進行表を中止にし、各レーンの未着手の行を中止にする。実行中のスプリントの進行表の ID を返す（呼ぶ側が中止を送る）。"""
    with plan_tx(main) as conn:
        if conn is None:
            return []
        tid = plan_table_id(main, name)
        if pdb.progress_table(conn, tid) is None:
            return []
        plan, lanes, ids = pdb.plan_and_lanes(conn, tid)
        running = []
        for ln, rows in lanes.items():
            for r in rows:
                if r["state"] == pm.PENDING:
                    d = pm.EventDraft("row_cancelled", ORGANIZER, str(r["row"]), reason=reason, payload={"row": r["row"]})
                    pdb.append_events(conn, ids[ln], [pm.EventDraft(**{**d.__dict__, "payload": {**d.payload, "lane": ln}})])
                elif r["state"] == pm.RUNNING and r.get("sprint"):
                    running.append(r["sprint"])
        if plan["state"] == pm.RUNNING:
            pdb.append_events(conn, tid, [pm.EventDraft("plan_cancelled", ORGANIZER, None, reason=reason)])
        return running


def wake_list(main: Path, name: str) -> list[tuple[str, int]]:
    """起こせるレーンと行（`after` を満たす未着手の行があり、実行中の行が無いレーン）。進行表が無ければ空。"""
    got = read_plan(main, name)
    return plr.wakeable_lanes(got[0], got[1]) if got else []
