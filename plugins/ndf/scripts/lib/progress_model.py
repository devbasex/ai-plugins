"""進行表のモデル（#3092 の決定 1・13・16・17・19・20）。純粋な処理で、DB も DBOS も import しない。

進行表はスプリント・レーン・計画の 3 階層で、状態の正本は追記だけの進行の事象（`Event`）である。行の今の状態は
行の鍵ごとに最新の `state` を持つ事象から畳み込む（`fold`）。事象の種類と書き手の役の組（`WRITERS`）と、
状態の遷移（`check_event`）・レーンをまたぐ決まり（`check_lane_change`）の判定はここだけが持ち、追記の関数
（`progress_db.append_events`）が書く直前に呼ぶ。
"""

from __future__ import annotations

import graphlib
import hashlib
from dataclasses import dataclass, field, replace
from typing import Any, Optional

LEVELS = ("plan", "lane", "sprint")
ACTORS = ("workflow", "conductor", "organizer", "durable", "adopt")

# 行の状態（スプリント）と、その表示の語
PENDING, RUNNING, FINISHED, FAILED, DISABLED, CANCELLED = "pending", "running", "finished", "failed", "disabled", "cancelled"
COMPLETED = "completed"  # 進行表全体・レーンの行・計画の完了
TERMINAL = (FINISHED, DISABLED, CANCELLED)
LABELS = {PENDING: "未着手", RUNNING: "実行中", FINISHED: "終了", FAILED: "失敗", DISABLED: "無効", CANCELLED: "中止", COMPLETED: "完了"}
TABLE_KEY = ""  # 進行表全体の事象の行の鍵

_S, _L, _P = ("sprint",), ("lane",), ("plan",)
_ALL = LEVELS
# I6 の正本（設計の runtime 文書の「事象の種類と書き手の表」）。（種類 → (階層, 書ける役)）
_WRITER_ROWS: list[tuple[tuple, tuple, tuple]] = [
    (("created",), _ALL, ("conductor", "organizer", "adopt")),
    (("execution", "started", "finished", "failed", "skipped", "completed"), _S, ("workflow",)),
    (("disabled", "enabled", "retry", "manual_finished", "scope_changed", "mode_raised", "carried", "cancelled"), _S, ("conductor",)),
    (("answered",), _S, ("organizer", "conductor")),
    (("row_added", "row_moved", "row_cancelled"), _L, ("conductor", "workflow", "organizer")),
    (("row_started",), _L, ("conductor", "adopt")),
    (("row_ended",), _L, ("workflow", "conductor")),
    (("lane_added",), _P, ("organizer", "adopt")),
    (("lane_changed", "request_closed", "plan_cancelled"), _P, ("organizer",)),
    (("request_opened",), _P, ("organizer", "workflow", "adopt")),
    (("plan_started",), _P, ("organizer", "adopt")),
    (("plan_completed",), _P, ("durable",)),
    (("adopted",), _ALL, ("adopt",)),
]
WRITERS: dict[tuple[str, str], frozenset] = {
    (lv, kind): frozenset(actors) for kinds, levels, actors in _WRITER_ROWS for kind in kinds for lv in levels
}
REASON_REQUIRED = frozenset(
    ("disabled", "enabled", "retry", "manual_finished", "scope_changed", "mode_raised", "cancelled", "request_closed")
)
# 種類 → 事象の後の行の状態（None は状態を変えない。payload で決まるものは別に扱う）
STATE_OF = {
    "created": PENDING, "started": RUNNING, "finished": FINISHED, "failed": FAILED, "skipped": DISABLED,
    "disabled": DISABLED, "enabled": PENDING, "retry": PENDING, "manual_finished": FINISHED, "cancelled": CANCELLED,
    "completed": COMPLETED, "row_added": PENDING, "row_started": RUNNING, "row_cancelled": CANCELLED,
    "plan_started": RUNNING, "plan_completed": COMPLETED, "plan_cancelled": CANCELLED,
}
# スプリントの行の遷移（状態の図）。（種類 → 許す前の状態。None は行がまだ無い）
_FROM = {
    "created": (None,), "carried": (None,), "started": (PENDING,), "finished": (RUNNING,), "failed": (PENDING, RUNNING),
    "skipped": (PENDING,), "disabled": (PENDING,), "enabled": (DISABLED,), "retry": (FAILED,),
    "manual_finished": (PENDING, FAILED), "adopted": (None, PENDING), "cancelled": (PENDING, RUNNING, FAILED),
    "answered": (RUNNING, FAILED, PENDING),
    "row_added": (None,), "row_started": (PENDING,), "row_ended": (RUNNING,), "row_cancelled": (PENDING,), "row_moved": (PENDING,),
}


class Refused(Exception):
    """不変条件・書き手・状態で断った操作。`current` は I1 で返す今の進行表の ID。"""

    def __init__(self, reason: str, current: Optional[str] = None):
        super().__init__(reason)
        self.reason = reason
        self.current = current


@dataclass(frozen=True)
class EventDraft:
    kind: str
    actor: str
    row_key: Optional[str] = None
    rev: Optional[int] = None
    state: Optional[str] = None  # 省けば STATE_OF から決める
    reason: Optional[str] = None
    payload: Optional[dict] = None
    dedupe_key: Optional[str] = None

    def resolved_state(self) -> Optional[str]:
        if self.state is not None:
            return self.state
        if self.kind in ("carried", "adopted", "row_ended"):
            return (self.payload or {}).get("state")
        return STATE_OF.get(self.kind)


@dataclass(frozen=True)
class Event(EventDraft):
    table_id: str = ""
    seq: int = 0
    at: str = ""


@dataclass
class RowState:
    state: str
    seq: int = 0
    at: str = ""
    reason: Optional[str] = None
    payload: dict = field(default_factory=dict)
    kind: str = ""


Rows = dict  # 行の鍵 → RowState


@dataclass(frozen=True)
class Action:
    kind: str  # run / run_children / skip / resume / complete / stop_failed / stop_plan_cancelled
    row: Optional[str] = None
    reason: Optional[str] = None
    children: tuple = ()


def table_id(level: str, repo: str, name: str) -> str:
    """主キー `<階層>:<リポジトリの短い鍵>:<名前>`（決定 15）。"""
    if level not in LEVELS:
        raise ValueError(f"知らない階層: {level}")
    return f"{level}:{hashlib.sha256(repo.encode()).hexdigest()[:12]}:{name}"


def level_of(tid: str) -> str:
    return tid.split(":", 1)[0]


def fold(events: list, rev: Optional[int] = None) -> Rows:
    """事象から行の今の状態を畳み込む（I23）。スプリントでは `rev` に一致する事象だけを使う。"""
    rows: Rows = {}
    for e in events:
        if rev is not None and e.rev is not None and e.rev != rev:
            continue
        key = e.row_key if e.row_key is not None else TABLE_KEY
        state = e.resolved_state()
        if e.kind == "cancelled" and key == TABLE_KEY:
            for k, r in rows.items():
                if k != TABLE_KEY and r.state not in TERMINAL + (COMPLETED,):
                    rows[k] = RowState(CANCELLED, e.seq, e.at, e.reason, {}, e.kind)
        if state is None:
            if key in rows and e.payload:
                rows[key].payload = {**rows[key].payload, **e.payload}
            continue
        rows[key] = RowState(state, e.seq, e.at, e.reason, dict(e.payload or {}), e.kind)
    return rows


def table_state(rows: Rows) -> Optional[str]:
    r = rows.get(TABLE_KEY)
    return r.state if r else None


def latest_rev(events: list) -> int:
    return max((e.rev for e in events if e.rev is not None), default=1)


# --- 書き手と遷移の判定 -------------------------------------------------------


def check_writer(level: str, kind: str, actor: str) -> None:
    """事象の種類と書き手の役の組が書き手の表にあるかを確かめる（I6）。"""
    if actor not in WRITERS.get((level, kind), frozenset()):
        raise Refused(f"{actor} は {level} の進行表へ {kind} を書けない（I6）")


def check_event(level: str, rows: Rows, draft: EventDraft, heights: Optional[dict] = None) -> None:
    """1 件の事象が今の行の状態から許される遷移かを確かめる（I4・I5・I12b・状態の図）。"""
    if draft.kind in REASON_REQUIRED and not (draft.reason or "").strip():
        raise Refused(f"{draft.kind} には理由が要る")
    if level == "sprint" and draft.rev is None:
        raise Refused("スプリントの事象に改訂（rev）が無い")
    tstate = table_state(rows)
    if tstate in (COMPLETED, CANCELLED) and draft.kind != "adopted":
        raise Refused(f"進行表は{LABELS[tstate]}している")
    if draft.row_key is None:
        _check_table_event(level, tstate, draft, heights)
        return
    if draft.kind not in _FROM:
        return
    cur = rows.get(draft.row_key)
    # アクティビティの行（`<工程>/<ID>`）と課題ごとの子の行（`<工程>#<番号>`）は、最初の事象の前を未着手とする
    before = cur.state if cur else (PENDING if ("/" in draft.row_key or "#" in draft.row_key) else None)
    if before not in _FROM[draft.kind]:
        if draft.kind == "disabled" and before == RUNNING:
            raise Refused(f"実行中の行 {draft.row_key} は無効にできない（I4）")
        label = LABELS.get(before, "無い")
        raise Refused(f"{draft.row_key} は{label}で、{draft.kind} を書けない")


def _check_table_event(level: str, tstate: Optional[str], draft: EventDraft, heights: Optional[dict]) -> None:
    if draft.kind in ("created", "plan_started") and tstate not in (None, PENDING):
        raise Refused(f"進行表はもう{LABELS.get(tstate, tstate)}である")
    if draft.kind in ("completed", "plan_completed", "plan_cancelled") and tstate != RUNNING:
        raise Refused(f"実行中でない進行表へ {draft.kind} を書けない")
    if draft.kind == "mode_raised":
        p = draft.payload or {}
        if heights is None or heights.get(p.get("to"), 0) <= heights.get(p.get("from"), 0):
            raise Refused(f"モードは上げる方向だけ変えられる（{p.get('from')} → {p.get('to')}。I5）")


# --- 次の手 -----------------------------------------------------------------


def next_action(snap, rows: Rows, enabled: dict, plan_cancelled: bool, pace: str = "normal", issues: tuple = ()) -> Action:
    """次に流す工程か、飛ばす工程か、終わりかを 1 つ返す（詳細設計の「処理の手順」）。"""
    if plan_cancelled:
        return Action("stop_plan_cancelled", reason="計画が中止された")
    for stage in snap.stages:
        r = rows.get(stage.name)
        state = r.state if r else PENDING
        if state in TERMINAL:
            continue
        if state == FAILED:
            return Action("stop_failed", stage.name, r.reason if r else None)
        if state == RUNNING:
            return Action("resume", stage.name)
        en = enabled.get(stage.name)
        if en is not None and not en.enabled:
            return Action("skip", stage.name, en.reason or "無効")
        if stage.per_issue and pace == "fast" and issues:
            return Action("run_children", stage.name, children=tuple(f"{stage.name}#{n}" for n in issues))
        return Action("run", stage.name)
    return Action("complete")


def carry_rows(old: Rows, new_snap, first_affected: str, rev: int, actor: str = "conductor") -> list[EventDraft]:
    """範囲とモードの変更で、新しい改訂の全行の最初の事象を作る（決定 16）。"""
    names = new_snap.stage_names
    if first_affected not in names:
        raise Refused(f"影響する工程 {first_affected} が新しい定義に無い")
    cut = names.index(first_affected)
    out = []
    for i, name in enumerate(names):
        r = old.get(name)
        if i < cut and r and r.state in (FINISHED, DISABLED):
            out.append(EventDraft("carried", actor, name, rev, payload={"state": r.state, "from_reason": r.reason}))
        else:
            out.append(EventDraft("created", actor, name, rev))
    return out


def adopt_rows(snap, done: set, stage_map: dict, rev: int = 1) -> list[EventDraft]:
    """取り込みの元の終わったステージから、工程の行の事象を作る（決定 19）。"""
    unknown = sorted(set(done) - set(stage_map))
    if unknown:
        raise ValueError(f"対応の無いステージ: {', '.join(unknown)}")
    out = []
    for name in snap.stage_names:
        hits = [st for st, procs in stage_map.items() if name in procs]
        out.append(EventDraft("created", "adopt", name, rev))
        if not hits:
            continue
        ended = [st for st in hits if st in done]
        if len(ended) == len(hits):
            out.append(EventDraft("adopted", "adopt", name, rev, payload={"state": FINISHED, "stages": hits}))
        elif ended:
            rest = [st for st in hits if st not in done]
            why = f"終わったステージ: {'・'.join(ended)} / 終わっていないステージ: {'・'.join(rest)}"
            out.append(EventDraft("adopted", "adopt", name, rev, reason=why, payload={"state": FAILED, "stages": hits}))
    return out


# --- 計画とレーン -------------------------------------------------------------


def lane_rows(events: list) -> list[dict]:
    """レーンの進行表の事象から、順番表の行を位置の順に作る。"""
    order: list[int] = []
    data: dict[int, dict] = {}
    for e in events:
        p = e.payload or {}
        if e.kind == "row_added":
            n = int(p["row"])
            data[n] = {"row": n, "issues": list(p.get("issues") or []), "touches": list(p.get("touches") or []),
                       "after": list(p.get("after") or []), "args": list(p.get("args") or []), "state": PENDING, "sprint": None}
            pos = p.get("position")
            order.insert(len(order) if pos is None else int(pos), n)
        elif e.kind == "row_moved":
            n = int(p["row"])
            order.remove(n)
            order.insert(int(p["to"]), n)
        elif e.row_key is not None and e.row_key.isdigit() and int(e.row_key) in data:
            row = data[int(e.row_key)]
            st = e.resolved_state()
            if st:
                row["state"] = st
            if e.kind == "row_started":
                row["sprint"] = p.get("sprint")
            if e.kind == "adopted" and p.get("sprint"):
                row["sprint"] = p.get("sprint")
    return [data[n] for n in order]


def plan_view(events: list) -> dict:
    """計画の進行表の事象から、レーン・上限・依頼・状態を作る。"""
    lanes: dict[str, dict] = {}
    requests: dict[str, dict] = {}
    plan: dict[str, Any] = {"lanes": lanes, "requests": requests, "max_lanes": None, "state": PENDING}
    for e in events:
        p = e.payload or {}
        if e.kind == "created" and e.row_key is None:
            plan["max_lanes"] = p.get("max_lanes")
            plan["rules"] = p.get("rules")
        elif e.kind in ("lane_added", "lane_changed"):
            lanes[p["lane"]] = {**lanes.get(p["lane"], {}), **{k: v for k, v in p.items() if k != "lane"}}
        elif e.kind == "request_opened":
            requests[str(e.seq)] = {"id": str(e.seq), "body": p, "reason": e.reason, "open": True}
        elif e.kind == "request_closed" and p.get("request") in requests:
            requests[p["request"]]["open"] = False
        st = e.resolved_state()
        if e.row_key is None and st:
            plan["state"] = st
    return plan


def _rows_index(lanes: dict) -> dict[str, dict]:
    return {f"{ln}#{r['row']}": {**r, "lane": ln} for ln, rows in lanes.items() for r in rows}


def _after_target(ref: str, lanes: dict) -> Optional[str]:
    """`after` の 1 つ（`<レーン>` か `<レーン>#<行>`）が指す行の鍵。レーンだけなら最初の行。"""
    if "#" in ref:
        return ref
    rows = lanes.get(ref) or []
    return f"{ref}#{rows[0]['row']}" if rows else None


def _graph(lanes: dict) -> dict[str, set]:
    """行の前後の有向グラフ（レーンの中の並びと `after`）。辺は前 → 後ろ。"""
    preds: dict[str, set] = {}
    for ln, rows in lanes.items():
        live = [r for r in rows if r["state"] != CANCELLED]
        for i, r in enumerate(live):
            k = f"{ln}#{r['row']}"
            preds.setdefault(k, set())
            if i:
                preds[k].add(f"{ln}#{live[i - 1]['row']}")
            for ref in r.get("after") or []:
                t = _after_target(ref, lanes)
                if t:
                    preds[k].add(t)
    return preds


def _reach(preds: dict[str, set]) -> dict[str, set]:
    """行ごとに、前にある行の集合（到達の集合）。"""
    memo: dict[str, set] = {}

    def up(k: str, seen: frozenset) -> set:
        if k in memo:
            return memo[k]
        out: set = set()
        for p in preds.get(k, ()):
            if p in seen:
                continue
            out.add(p)
            out |= up(p, seen | {p})
        memo[k] = out
        return out

    return {k: up(k, frozenset({k})) for k in preds}


def _violations(plan: dict, lanes: dict) -> set[tuple]:
    from parallel_plan import _touch_hit  # 触る場所の重なりの判定は計画の検査と同じ関数を使う

    out: set[tuple] = set()
    idx = _rows_index(lanes)
    live = {k: r for k, r in idx.items() if r["state"] not in (CANCELLED, COMPLETED)}
    preds = _graph(lanes)
    try:
        graphlib.TopologicalSorter(preds).prepare()
    except graphlib.CycleError as e:
        out.add(("I21", f"after が輪になる（{' → '.join(map(str, e.args[1]))}）"))
        return out
    reach = _reach(preds)
    keys = sorted(live)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            ra, rb = live[a], live[b]
            if ra["lane"] == rb["lane"]:
                continue
            if set(ra["issues"]) & set(rb["issues"]):
                out.add(("I19", f"{a} と {b} で課題が重なる（{sorted(set(ra['issues']) & set(rb['issues']))}）"))
            if a in reach.get(b, set()) or b in reach.get(a, set()):
                continue
            ta = ra["touches"] or (plan["lanes"].get(ra["lane"]) or {}).get("touches") or []
            tb = rb["touches"] or (plan["lanes"].get(rb["lane"]) or {}).get("touches") or []
            hits = sorted({f"{x}・{y}" for x in ta for y in tb if _touch_hit(x, y)})
            if hits:
                out.add(("I13", f"同時に動き得る {a} と {b} で触る場所が重なる（{', '.join(hits)}）"))
    cap = plan.get("max_lanes")
    if cap is not None and len(plan["lanes"]) > cap:
        out.add(("I20", f"レーンの数 {len(plan['lanes'])} が上限 {cap} を超える"))
    return out


def apply_lane_change(plan: dict, lanes: dict, change: EventDraft) -> tuple[dict, dict]:
    """変更を写した計画とレーンの写しを返す（判定のため。書き込みはしない）。"""
    p = change.payload or {}
    plan2 = {**plan, "lanes": {k: dict(v) for k, v in plan["lanes"].items()}}
    lanes2 = {k: [dict(r) for r in v] for k, v in lanes.items()}
    lane = p.get("lane")
    if change.kind in ("lane_added", "lane_changed"):
        plan2["lanes"][lane] = {**plan2["lanes"].get(lane, {}), **{k: v for k, v in p.items() if k != "lane"}}
        lanes2.setdefault(lane, [])
    elif change.kind == "row_added":
        rows = lanes2.setdefault(lane, [])
        new = {"row": int(p["row"]), "issues": list(p.get("issues") or []), "touches": list(p.get("touches") or []),
               "after": list(p.get("after") or []), "state": PENDING, "sprint": None}
        rows.insert(len(rows) if p.get("position") is None else int(p["position"]), new)
    elif change.kind == "row_moved":
        rows = lanes2[lane]
        r = next(x for x in rows if x["row"] == int(p["row"]))
        rows.remove(r)
        rows.insert(int(p["to"]), r)
    elif change.kind == "row_cancelled":
        for r in lanes2[lane]:
            if r["row"] == int(p["row"]):
                r["state"] = CANCELLED
    elif change.kind == "row_ended":
        for r in lanes2[lane]:
            if r["row"] == int(p["row"]):
                r["state"] = p.get("state", COMPLETED)
    return plan2, lanes2


def check_lane_change(plan: dict, lanes: dict, change: EventDraft) -> list[str]:
    """レーンの順番表の変更を計画の決まりで判定する（I12a・I13・I18〜I21）。断る理由の一覧を返す。"""
    p = change.payload or {}
    lane = p.get("lane")
    errs: list[str] = []
    rows = lanes.get(lane) or []
    if change.kind in ("row_moved", "row_cancelled", "row_added") and lane not in plan["lanes"]:
        return [f"計画に無いレーン {lane}"]
    if change.kind in ("row_moved", "row_cancelled"):
        target = next((r for r in rows if r["row"] == int(p["row"])), None)
        if target is None:
            return [f"{lane} に行 {p['row']} が無い"]
        if target["state"] in (RUNNING, COMPLETED):
            errs.append(f"{lane}#{target['row']} は{LABELS[target['state']]}で、位置と課題を変えられない（I12a）")
        key = f"{lane}#{target['row']}"
        refd = [f"{ln}#{r['row']}" for ln, rs in lanes.items() if ln != lane for r in rs
                if key in {_after_target(ref, lanes) for ref in r.get("after") or []}]
        moving_back = change.kind == "row_moved" and int(p["to"]) > rows.index(target)
        if refd and (change.kind == "row_cancelled" or moving_back):
            errs.append(f"{key} は {', '.join(refd)} の after が指すため、消せず後ろへも回せない（I18）")
    if change.kind == "row_added" and p.get("position") is not None:
        fixed = [i for i, r in enumerate(rows) if r["state"] in (RUNNING, COMPLETED)]
        if fixed and int(p["position"]) <= max(fixed):
            errs.append(f"{lane} の実行中か完了の行より前へは足せない（I12a）")
    if errs:
        return errs
    before = _violations(plan, lanes)
    plan2, lanes2 = apply_lane_change(plan, lanes, change)
    return [f"{why}（{rule}）" for rule, why in sorted(_violations(plan2, lanes2) - before)]


def _after_met(row: dict, lanes: dict) -> bool:
    idx = _rows_index(lanes)
    for ref in row.get("after") or []:
        t = _after_target(ref, lanes)
        if t is None or idx.get(t, {}).get("state") != COMPLETED:
            return False
    return True


def next_row(lane: str, plan: dict, lanes: dict) -> Optional[Any]:
    """終えた行の次の行と、その `after` が満たされているかを返す（決定 7）。

    次の未着手の行が無ければ None、`after` が満たされなければ `"waiting_after"`、満たされれば
    `progress.py new` の引数（`{"lane": ..., "row": ..., "issues": ..., "args": ...}`）を返す。"""
    nxt = next((r for r in lanes.get(lane) or [] if r["state"] == PENDING), None)
    if nxt is None:
        return None
    if not _after_met(nxt, lanes):
        return "waiting_after"
    return {"lane": lane, "row": nxt["row"], "issues": nxt["issues"], "args": nxt.get("args") or []}


def wakeable_lanes(plan: dict, lanes: dict) -> list[tuple[str, int]]:
    """実行中の行が無く、`after` を満たす未着手の行があるレーンを、計画の並びの順で返す。"""
    out = []
    for ln in plan["lanes"]:
        rows = lanes.get(ln) or []
        if any(r["state"] == RUNNING for r in rows):
            continue
        nxt = next((r for r in rows if r["state"] == PENDING), None)
        if nxt is not None and _after_met(nxt, lanes):
            out.append((ln, nxt["row"]))
    return out


def plan_complete(lanes: dict, requests: list) -> bool:
    """全レーンの行が完了か中止で、答えていない依頼が無いときだけ真（I16）。"""
    done = all(r["state"] in (COMPLETED, CANCELLED) for rows in lanes.values() for r in rows)
    return done and not any(q.get("open") for q in requests)


def with_rev(drafts: list, rev: int) -> list:
    return [replace(d, rev=rev) if d.rev is None else d for d in drafts]
