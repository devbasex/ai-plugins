"""レーンの進行表（#3097）: レーンの中のスプリントの順番表と、書き込みが通る計画の決まり（レーンをまたぐ制約）。

進行表は 3 段ある。計画の進行表（オーガナイザーが持つ。`ndf.progress.plan`）→ レーンの進行表（レーンの conductor が
持つ。このモジュール）→ スプリントの進行表（`ndf progress`）。

- レーンの進行表の行はスプリント 1 つで、状態は 未着手・実行中（スプリントの進行表の ID を持つ）・完了・中止
- レーンの進行表へ事象を書くのはこのモジュールの関数だけで、書く前に計画の決まり（触る場所の重なり I13・
  別のレーンの `after` が指す行 I18・レーンの間の課題の重なり I19・上限 I20・`after` の輪 I21・行の位置 I12a）で確かめる
- 計画の決まりは計画の進行表の事象を畳み込んで読む（`fold_plan`）。畳み込みと判定は純粋な関数で、DB を読まない
- 答えの事象（`row_answered`）の後に conductor が `ndf progress next` を打った印（`row_resumed`）が無い行は、
  「答えの後の next 待ち」として読める（`awaiting_next`）
"""

from __future__ import annotations

import fnmatch
import graphlib
import itertools
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Optional

import sqlalchemy as sa

from ndf import identity
from ndf.core import durable
from ndf.progress import model, store

# 計画の進行表の中の、計画そのものの行の鍵（規則・上限・形の番号を持つ）。レーン名の形に当たらない語にする
PLAN_ROW = "@plan"
# 行の鍵の接頭辞（r1・r2…）と、依頼の行の鍵の接頭辞
ROW_PREFIX = "r"
REQUEST_PREFIX = "request-"
# 行の状態の語（契約 progress.json の row_states.lane）
PENDING, RUNNING = "pending", "running"
DONE_STATES = ("completed", "cancelled")
# 計画が記録する形の番号の鍵。違えばレーンを起動しない（#3344 の決定 86）
FORMAT_KEYS = ("store_format", "format", "table_version")


class FormatMismatch(Exception):
    """計画が記録した形の番号と自分のものが違う。"""

    kind = "format_mismatch"


# --- 計画とレーンの型 ----------------------------------------------------------------------------


@dataclass(frozen=True)
class LaneSpec:
    """計画の進行表のレーンの行。`after` の要素は `<レーン>`（レーンの全行）か `<レーン>/<行の鍵>`。"""

    name: str
    table_id: str
    kind: str
    plans: int
    touches: tuple[str, ...]
    after: tuple[str, ...] = ()
    repo: Optional[str] = None


@dataclass(frozen=True)
class Request:
    """レーンの側からオーガナイザーへの依頼（`request_opened`）。閉じたら `closed` に理由を持つ。"""

    key: str
    lane: Optional[str]
    body: dict[str, Any]
    reason: Optional[str]
    closed: Optional[str] = None


@dataclass(frozen=True)
class PlanView:
    """計画の進行表の畳み込み。"""

    table_id: str
    name: str
    state: str
    rules: tuple[str, ...]
    max_lanes: int
    max_plans: int
    formats: dict[str, Any]
    ndf_version: Optional[str]
    lanes: dict[str, LaneSpec] = field(default_factory=dict)
    requests: dict[str, Request] = field(default_factory=dict)

    def open_requests(self) -> list[Request]:
        return [r for r in self.requests.values() if r.closed is None]


@dataclass(frozen=True)
class LaneRow:
    """レーンの進行表の行（スプリント 1 つ）。`answered_seq`・`resumed_seq` は答えと next の印の事象の番号。"""

    key: str
    input: dict[str, Any]
    state: str
    sprint_id: Optional[str] = None
    reason: Optional[str] = None
    answered_seq: int = 0
    answered_at: Optional[datetime] = None
    resumed_seq: int = 0

    @property
    def live(self) -> bool:
        return self.state in (PENDING, RUNNING)

    @property
    def awaiting_next(self) -> bool:
        """答えが載った後に conductor がまだ `ndf progress next` を打っていない実行中の行か。"""
        return self.state == RUNNING and self.answered_seq > self.resumed_seq

    def stalled(self, now: datetime, grace_seconds: float) -> bool:
        """答えの後の next 待ちのまま、答えが載ってから猶予の秒以上たったか（載った時刻が無ければ 0 秒とみなす）。"""
        if not self.awaiting_next:
            return False
        waited = (now - self.answered_at).total_seconds() if self.answered_at else 0.0
        return waited >= grace_seconds

    def issues(self) -> set[int]:
        return set(self.input["issues"]) if self.input.get("kind") == "sprint" else set()


Lanes = Mapping[str, list[LaneRow]]


# --- 畳み込み（純粋） -----------------------------------------------------------------------------


def _spec(name: str, body: Mapping[str, Any]) -> LaneSpec:
    return LaneSpec(
        name=name,
        table_id=body["table_id"],
        kind=body["kind"],
        plans=int(body["plans"]),
        touches=tuple(body["touches"]),
        after=tuple(body["after"]),
        repo=body.get("repo"),
    )


def _changed_spec(spec: LaneSpec, body: Mapping[str, Any]) -> LaneSpec:
    changes = {k: (tuple(v) if isinstance(v, list) else v) for k, v in body.items() if k in ("plans", "touches", "after")}
    return replace(spec, **changes)


def _plan_base(table_id: str, body: Mapping[str, Any], state: str) -> PlanView:
    return PlanView(
        table_id=table_id,
        name=body["name"],
        state=state,
        rules=tuple(body["rules"]),
        max_lanes=int(body["max_lanes"]),
        max_plans=int(body["max_plans"]),
        formats=dict(body["formats"]),
        ndf_version=body.get("ndf_version"),
    )


def _plan_limits(view: PlanView, body: Mapping[str, Any]) -> PlanView:
    changes = {k: int(v) for k, v in body.items() if k in ("max_lanes", "max_plans")}
    return replace(view, **changes)


def _fold_plan_event(view: PlanView, d: model.EventDraft) -> PlanView:
    body, key = d.payload, d.row_key
    if d.kind == "lane_added":
        return replace(view, lanes={**view.lanes, key: _spec(key, body)})
    if d.kind == "lane_changed" and key == PLAN_ROW:
        return _plan_limits(view, body)
    if d.kind == "lane_changed":
        return replace(view, lanes={**view.lanes, key: _changed_spec(view.lanes[key], body)})
    if d.kind == "request_opened":
        req = Request(key, body.get("lane"), dict(body["body"]), d.reason)
        return replace(view, requests={**view.requests, key: req})
    if d.kind == "request_closed":
        return replace(view, requests={**view.requests, key: replace(view.requests[key], closed=d.reason)})
    return view


def fold_plan(table_id: str, events: list[model.Event]) -> PlanView:
    """計画の進行表の事象から、レーンの配列・規則・上限・依頼・計画の状態を求める。"""
    created = [e for e in events if e.draft.kind == "created" and e.draft.row_key == PLAN_ROW]
    if not created:
        raise model.Refused(f"計画の進行表 {table_id} に計画の行が無い")
    state = model.table_state("plan", events)
    view = _plan_base(table_id, created[0].draft.payload, state)
    for ev in events:
        view = _fold_plan_event(view, ev.draft)
    return view


def _row_from(rows: dict[str, LaneRow], ev: model.Event) -> Optional[LaneRow]:
    """1 件の事象を当てた後の行。行の位置を変えない事象だけを扱う（位置は `fold_lane` が持つ）。"""
    d = ev.draft
    row = rows.get(d.row_key)
    if d.kind == "row_added":
        return LaneRow(d.row_key, dict(d.payload["input"]), PENDING, reason=d.reason)
    if row is None:
        return None
    if d.kind == "row_answered":
        return replace(row, answered_seq=ev.seq, answered_at=ev.at)
    if d.kind == "row_resumed":
        return replace(row, resumed_seq=ev.seq)
    sprint = d.payload.get("sprint_id") if d.payload else None
    if d.state is None:
        return row
    return replace(row, state=d.state, sprint_id=sprint if sprint is not None else row.sprint_id, reason=d.reason)


def _placed(order: list[str], key: str, index: Optional[int]) -> list[str]:
    rest = [k for k in order if k != key]
    at = len(rest) if index is None else max(0, min(index, len(rest)))
    return [*rest[:at], key, *rest[at:]]


def fold_lane(events: Iterable[model.Event]) -> list[LaneRow]:
    """レーンの進行表の事象から、順番表の行を並びの順に求める。"""
    rows: dict[str, LaneRow] = {}
    order: list[str] = []
    for ev in events:
        d = ev.draft
        if d.kind in ("row_added", "row_moved"):
            order = _placed(order, d.row_key, d.payload.get("index"))
        got = _row_from(rows, ev)
        if got is not None:
            rows[d.row_key] = got
    return [rows[k] for k in order]


# --- 計画の決まり（純粋） ---------------------------------------------------------------------------


def _rows_of(lanes: Lanes, name: str) -> list[LaneRow]:
    """レーンの行（計画に無いレーンなら空）。"""
    return list(lanes[name]) if name in lanes else []


def _next_of(edges: Mapping[tuple[str, str], set[tuple[str, str]]], node: tuple[str, str]) -> set[tuple[str, str]]:
    return edges[node] if node in edges else set()


def _target(ref: str) -> tuple[str, Optional[str]]:
    lane, _, row = ref.partition("/")
    return lane, (row if row else None)


def after_met(ref: str, lanes: Lanes) -> bool:
    """`after` の 1 つが満たされたか。行なら完了か中止、レーンなら全行が完了か中止。"""
    lane, row = _target(ref)
    rows = [r for r in _rows_of(lanes, lane) if row is None or r.key == row]
    return bool(rows) and all(r.state in DONE_STATES for r in rows)


def _after_errors(view: PlanView, lanes: Lanes) -> list[str]:
    errs = []
    for spec in view.lanes.values():
        for lane, row in map(_target, spec.after):
            if lane not in view.lanes or lane == spec.name:
                errs.append(f"レーン {spec.name} の after の先 {lane} が計画に無い")
            elif row is not None and row not in {r.key for r in _rows_of(lanes, lane)}:
                errs.append(f"レーン {spec.name} の after の先 {lane}/{row} がレーンの順番表に無い")
    graph = {s.name: {_target(a)[0] for a in s.after} for s in view.lanes.values()}
    try:
        graphlib.TopologicalSorter(graph).prepare()
    except graphlib.CycleError as exc:
        errs.append(f"after が輪になる（I21）: {' → '.join(exc.args[1])}")
    return errs


def _live_lanes(view: PlanView, lanes: Lanes) -> list[str]:
    """終わっていないレーン（未着手か実行中の行を持つ）。終わったレーンは上限にも重なりにも数えない（#3021）。"""
    return [name for name in view.lanes if any(r.live for r in _rows_of(lanes, name))]


def _limit_errors(view: PlanView, lanes: Lanes) -> list[str]:
    live = _live_lanes(view, lanes)
    errs = [f"終わっていないレーンの数 {len(live)} が上限 {view.max_lanes} を超える（I20）"] if len(live) > view.max_lanes else []
    # プランの本数はレーンごとに見る。after で直列のレーンを足し合わせない（#3145）
    return errs + [
        f"レーン {n} のプランの本数 {view.lanes[n].plans} が上限 {view.max_plans} を超える（I2）"
        for n in live
        if view.lanes[n].plans > view.max_plans
    ]


def _same_repo(a: LaneSpec, b: LaneSpec) -> bool:
    return a.repo == b.repo


def _issue_errors(view: PlanView, lanes: Lanes) -> list[str]:
    errs = []
    for a, b in itertools.combinations(_live_lanes(view, lanes), 2):
        if not _same_repo(view.lanes[a], view.lanes[b]):
            continue
        hit = set().union(*(r.issues() for r in lanes[a] if r.live)) & set().union(*(r.issues() for r in lanes[b] if r.live))
        if hit:
            errs.append(f"レーン {a} と {b} の間で課題が重なる（I19）: {', '.join(f'#{n}' for n in sorted(hit))}")
    return errs


def touch_hit(x: str, y: str) -> bool:
    """触る場所の 2 つが重なるか。接頭辞か glob が当たれば重なる。"""
    px, py = x.rstrip("/"), y.rstrip("/")
    if px == py or px.startswith(py + "/") or py.startswith(px + "/"):
        return True
    return fnmatch.fnmatch(px, py) or fnmatch.fnmatch(py, px)


def _order_graph(view: PlanView, lanes: Lanes) -> dict[tuple[str, str], set[tuple[str, str]]]:
    """行の前後の鎖（レーンの中の並びと after）。辺は前の行 → 後の行。"""
    edges: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for name, rows in lanes.items():
        nodes = [(name, r.key) for r in rows]
        for a, b in itertools.pairwise(nodes):
            edges.setdefault(a, set()).add(b)
        spec = view.lanes.get(name)
        if not nodes or spec is None:
            continue
        for lane, row in map(_target, spec.after):
            keys = [r.key for r in _rows_of(lanes, lane)]
            src = row if row is not None else (keys[-1] if keys else None)
            if src is not None:
                edges.setdefault((lane, src), set()).add(nodes[0])
    return edges


def _reach(edges: Mapping[tuple[str, str], set[tuple[str, str]]], start: tuple[str, str]) -> set[tuple[str, str]]:
    seen, todo = set(), [start]
    while todo:
        for nxt in _next_of(edges, todo.pop()) - seen:
            seen.add(nxt)
            todo.append(nxt)
    return seen


def _concurrent(edges: Mapping[tuple[str, str], set[tuple[str, str]]], x: tuple[str, str], y: tuple[str, str]) -> bool:
    """2 つの行の間に前後の鎖が無いか（同時に動き得るか）。"""
    return y not in _reach(edges, x) and x not in _reach(edges, y)


def _concurrent_pairs(edges: Mapping[tuple[str, str], set[tuple[str, str]]], lanes: Lanes, a: str, b: str) -> list[str]:
    """レーン a と b の終わっていない行のうち、同時に動き得る組。"""
    return [
        f"{a}/{x.key}・{b}/{y.key}"
        for x, y in itertools.product([r for r in lanes[a] if r.live], [r for r in lanes[b] if r.live])
        if _concurrent(edges, (a, x.key), (b, y.key))
    ]


def _touch_hits(la: LaneSpec, lb: LaneSpec) -> list[str]:
    """2 つのレーンの触る場所の重なる組。"""
    return sorted({f"{x}・{y}" for x, y in itertools.product(la.touches, lb.touches) if touch_hit(x, y)})


def _touch_errors(view: PlanView, lanes: Lanes) -> list[str]:
    """同時に動き得る行の組（前後の鎖が無い別のレーンの行どうし）の間で、触る場所が重なる組（I13）。"""
    edges, errs = _order_graph(view, lanes), []
    for a, b in itertools.combinations(_live_lanes(view, lanes), 2):
        la, lb = view.lanes[a], view.lanes[b]
        hits = _touch_hits(la, lb)
        if not hits or not _same_repo(la, lb):
            continue
        pairs = _concurrent_pairs(edges, lanes, a, b)
        if pairs:
            errs.append(f"同時に動き得る行 {', '.join(pairs)} の触る場所が重なる（I13）: {', '.join(hits)}")
    return errs


def plan_errors(view: PlanView, lanes: Lanes) -> list[str]:
    """計画の決まりに当たる箇所の一覧（I2・I13・I19・I20・I21 と after の先）。空なら通る。"""
    return _after_errors(view, lanes) + _limit_errors(view, lanes) + _issue_errors(view, lanes) + _touch_errors(view, lanes)


def _referenced(view: PlanView, lane: str) -> set[str]:
    """別のレーンの after が指す、このレーンの行の鍵。"""
    refs = (_target(a) for s in view.lanes.values() if s.name != lane for a in s.after)
    return {row for name, row in refs if name == lane and row is not None}


def _first_open(rows: list[LaneRow]) -> int:
    """書き換えてよい位置の先頭（実行中と完了と中止の行の後ろ）。"""
    done = [i for i, r in enumerate(rows) if r.state != PENDING]
    return done[-1] + 1 if done else 0


def change_errors(view: PlanView, rows: list[LaneRow], lane: str, draft: model.EventDraft) -> list[str]:
    """レーンの中の組み換え 1 件が、行の位置（I12a）と別のレーンの after が指す行（I18）を守るか。"""
    index = draft.payload.get("index") if draft.payload else None
    keys = [r.key for r in rows]
    errs = []
    if draft.kind in ("row_added", "row_moved") and index is not None and index < _first_open(rows):
        errs.append(f"レーン {lane} の位置 {index} は実行中か終わった行より前で、行の位置を書き換える（I12a）")
    if draft.row_key in _referenced(view, lane):
        moved_back = draft.kind == "row_moved" and (index is None or index > keys.index(draft.row_key))
        if draft.kind == "row_cancelled" or moved_back:
            errs.append(f"行 {lane}/{draft.row_key} は別のレーンの after が指していて、消せず後ろへも回せない（I18）")
    return errs


def next_row(view: PlanView, lanes: Lanes, lane: str) -> Optional[dict[str, Any]]:
    """レーンの次に始める行（最初の未着手の行）と、満たされていない after。無ければ None。"""
    rows = _rows_of(lanes, lane)
    if any(r.state == RUNNING for r in rows):
        return None
    pending = [r for r in rows if r.state == PENDING]
    if not pending:
        return None
    row = pending[0]
    waiting = [a for a in view.lanes[lane].after if not after_met(a, lanes)]
    return {"lane": lane, "row": row.key, "input": row.input, "waiting_after": waiting}


def wakeable_lanes(view: PlanView, lanes: Lanes) -> list[tuple[str, str]]:
    """実行中の行が無く、after を満たす未着手の行があるレーンを（レーン, 行の鍵）で、計画の並びの順に返す。"""
    if view.state != RUNNING:
        return []
    found = (next_row(view, lanes, name) for name in view.lanes)
    return [(n["lane"], n["row"]) for n in found if n is not None and not n["waiting_after"]]


def plan_complete(view: PlanView, lanes: Lanes) -> bool:
    """計画を完了にしてよいか（I16）: 全レーンの行が完了か中止で、答えていない依頼が無い。"""
    rows = [r for name in view.lanes for r in _rows_of(lanes, name)]
    return all(r.state in DONE_STATES for r in rows) and not view.open_requests()


def lane_state(view: PlanView, lanes: Lanes, lane: str) -> str:
    """レーンの状態（持たずに行から出す）: 実行中・未着手・after 待ち・完了・中止。"""
    rows = _rows_of(lanes, lane)
    if any(r.state == RUNNING for r in rows):
        return RUNNING
    nxt = next_row(view, lanes, lane)
    if nxt is not None:
        return "waiting_after" if nxt["waiting_after"] else PENDING
    return "completed" if any(r.state == "completed" for r in rows) else "cancelled"


def current_formats() -> dict[str, int]:
    """自分の形の番号（チェックアウトの置き場・耐久の記録の呼び出しの順・進行表の表の版）。"""
    return dict(zip(FORMAT_KEYS, (durable.STORE_FORMAT, durable.FORMAT, store.SCHEMA_VERSION), strict=True))


def ndf_version() -> Optional[str]:
    """自分の NDF の版（plugin.json の version）。読めなければ None。"""
    manifest = identity.ROOT / ".claude-plugin" / "plugin.json"
    try:
        return str(json.loads(manifest.read_text(encoding="utf-8"))["version"])
    except (OSError, ValueError, KeyError):  # noqa: NDF401 ファイル: 版が読めなければ版の比べを飛ばす
        return None


def format_check(view: PlanView, now: Mapping[str, Any], version: Optional[str]) -> list[str]:
    """計画が記録した形の番号と自分のものを比べる。形が違えば `FormatMismatch`、NDF の版だけが違えば警告の文を返す。"""
    diff = [f"{k}: 計画 {view.formats.get(k)}・自分 {now[k]}" for k in FORMAT_KEYS if view.formats.get(k) != now[k]]
    if diff:
        raise FormatMismatch(f"計画 {view.name} の形の番号が自分と違う: {'; '.join(diff)}")
    if view.ndf_version is not None and version is not None and view.ndf_version != version:
        return [f"計画 {view.name} を作った NDF の版 {view.ndf_version} と自分の版 {version} が違う（形は同じなので続ける）"]
    return []


# --- DB の読み書き ---------------------------------------------------------------------------------


def lane_table_name(plan: str, lane: str) -> str:
    return f"{plan}/{lane}"


def read_plan(conn: sa.Connection, plan_id: str) -> tuple[PlanView, list[model.Event]]:
    meta = store.read_table(conn, plan_id)
    if meta is None or meta["level"] != "plan":
        raise model.Refused(f"計画の進行表 {plan_id} は無い")
    events = store.read_events(conn, plan_id)
    return fold_plan(plan_id, events), events


def read_lanes(conn: sa.Connection, view: PlanView) -> dict[str, list[model.Event]]:
    """計画のレーンごとの、レーンの進行表の事象。"""
    return {name: store.read_events(conn, spec.table_id) for name, spec in view.lanes.items()}


def folded(events: Mapping[str, list[model.Event]]) -> dict[str, list[LaneRow]]:
    return {name: fold_lane(evs) for name, evs in events.items()}


def read_view(conn: sa.Connection, plan_id: str) -> tuple[PlanView, dict[str, list[LaneRow]]]:
    """計画の進行表の畳み込みと、レーンごとの順番表の行。"""
    view, _ = read_plan(conn, plan_id)
    return view, folded(read_lanes(conn, view))


def _new_key(events: list[model.Event], drafts: Iterable[model.EventDraft]) -> str:
    added = [e.draft for e in events if e.draft.kind == "row_added"] + [d for d in drafts if d.kind == "row_added"]
    return f"{ROW_PREFIX}{len(added) + 1}"


def _with(events: list[model.Event], draft: model.EventDraft) -> list[model.Event]:
    """確かめるために、下書きを書いた後の事象の並び（番号と時刻は仮）。"""
    last = events[-1] if events else None
    seq = last.seq + 1 if last is not None else 1
    at = last.at if last is not None else datetime.min
    return [*events, model.Event(seq, at, model.recorded("lane", draft))]


def checked_write(conn: sa.Connection, plan_id: str, lane: str, drafts: list[model.EventDraft]) -> list[int]:
    """レーンの進行表へ組み換えと状態の下書きを書く唯一の関数。1 件ずつ計画の決まりで確かめ、新しく当たる箇所があれば `Refused`。

    計画の決まりの判定は書く前の状態と比べ、書き込みで新しく生まれた違反だけを断る（前からある違反で
    無関係の組み換えを止めない）。
    """
    view, _ = read_plan(conn, plan_id)
    if lane not in view.lanes:
        raise model.Refused(f"計画 {view.name} にレーン {lane} は無い")
    events = read_lanes(conn, view)
    last = events[lane][-1].seq if events[lane] else 0
    for draft in drafts:
        rows = folded(events)
        before = set(plan_errors(view, rows))
        errs = change_errors(view, rows[lane], lane, draft)
        events = {**events, lane: _with(events[lane], draft)}
        errs += [e for e in plan_errors(view, folded(events)) if e not in before]
        if errs:
            raise model.Refused("; ".join(errs))
    return store.append_events(conn, view.lanes[lane].table_id, drafts, last)


def create_lane(conn: sa.Connection, key: tuple[str, str, str], plan_id: str, inputs: list[dict[str, Any]]) -> str:
    """レーンの進行表を作り、最初の順番表の行を未着手で書く（作るのはオーガナイザー）。`key` は（リポジトリ, 計画, レーン）。

    計画の決まりの検査は、呼び手（計画の進行表の関数）が全レーンを作った後に 1 度だけ行う。
    """
    repo, plan, lane = key
    drafts = [
        model.EventDraft("row_added", "organizer", f"{ROW_PREFIX}{i}", payload={"input": inp, "index": None})
        for i, inp in enumerate(inputs, start=1)
    ]
    return store.create_table(conn, ("lane", repo, lane_table_name(plan, lane)), plan_id, None, drafts)


def _lane_rows(conn: sa.Connection, plan_id: str, lane: str) -> tuple[PlanView, dict[str, list[LaneRow]]]:
    view, rows = read_view(conn, plan_id)
    if lane not in view.lanes:
        raise model.Refused(f"計画 {view.name} にレーン {lane} は無い")
    return view, rows


def _row(rows: list[LaneRow], key: str, lane: str) -> LaneRow:
    for r in rows:
        if r.key == key:
            return r
    raise model.Refused(f"レーン {lane} に行 {key} は無い")


@dataclass(frozen=True)
class RowChange:
    """順番表の 1 行の変更の依頼。`index` は並びの位置（0 が先頭。None なら末尾）。"""

    actor: str
    reason: Optional[str] = None
    index: Optional[int] = None


def add_row(conn: sa.Connection, plan_id: str, lane: str, inp: dict[str, Any], change: RowChange) -> str:
    """順番表へ行を足す（未着手）。足した行の鍵を返す。"""
    view, _ = _lane_rows(conn, plan_id, lane)
    key = _new_key(store.read_events(conn, view.lanes[lane].table_id), [])
    payload = {"input": inp, "index": change.index}
    checked_write(conn, plan_id, lane, [model.EventDraft("row_added", change.actor, key, reason=change.reason, payload=payload)])
    return key


def move_row(conn: sa.Connection, plan_id: str, lane: str, key: str, change: RowChange) -> None:
    _, rows = _lane_rows(conn, plan_id, lane)
    _row(rows[lane], key, lane)
    draft = model.EventDraft("row_moved", change.actor, key, reason=change.reason, payload={"index": change.index})
    checked_write(conn, plan_id, lane, [draft])


def cancel_row(conn: sa.Connection, plan_id: str, lane: str, key: str, change: RowChange) -> None:
    _, rows = _lane_rows(conn, plan_id, lane)
    _row(rows[lane], key, lane)
    checked_write(conn, plan_id, lane, [model.EventDraft("row_cancelled", change.actor, key, reason=change.reason)])


def carry_issues(conn: sa.Connection, plan_id: str, lane: str, from_row: str, issues: list[int], reason: str) -> str:
    """設計承認ゲートで外した課題を、同じレーンの順番表の次の行（外した行のすぐ後ろ）へ足す（受け入れ条件 3）。

    after 付きの新しいレーンを作らない。承認ゲートの答えからこの関数を呼ぶのは承認ゲートの子（#3295）である。
    """
    _, rows = _lane_rows(conn, plan_id, lane)
    src = _row(rows[lane], from_row, lane)
    index = [r.key for r in rows[lane]].index(from_row) + 1
    inp = {"kind": "sprint", "issues": sorted(set(issues)), "args": list(src.input["args"]) if "args" in src.input else []}
    return add_row(conn, plan_id, lane, inp, RowChange("conductor", reason, index))


def start_row(conn: sa.Connection, plan_id: str, lane: str, key: str, sprint_id: str) -> list[str]:
    """スプリントの開始: 行へスプリントの進行表の ID を書き、実行中にする（書くのはレーンの conductor）。

    計画が記録した形の番号と自分のものを比べ、違えば `FormatMismatch`。NDF の版だけが違えば止めずに、
    行の事象（`row_started` の `ndf_version_mismatch`）に残し、警告の文を返す（P3-1）。
    """
    view, rows = _lane_rows(conn, plan_id, lane)
    if view.state != RUNNING:
        raise model.Refused(f"計画 {view.name} は{model.label(view.state)}で、スプリントを始められない")
    nxt = next_row(view, rows, lane)
    if nxt is None or nxt["row"] != key:
        raise model.Refused(f"行 {lane}/{key} はレーンの次に始める行でない（次の行: {nxt['row'] if nxt else '無し'}）")
    if nxt["waiting_after"]:
        raise model.Refused(f"行 {lane}/{key} の after がまだ満たされていない: {', '.join(nxt['waiting_after'])}")
    version = ndf_version()
    warnings = format_check(view, current_formats(), version)
    payload: dict[str, Any] = {"sprint_id": sprint_id}
    if warnings:
        payload["ndf_version_mismatch"] = {"plan": view.ndf_version, "self": version}
    checked_write(conn, plan_id, lane, [model.EventDraft("row_started", "conductor", key, payload=payload)])
    return warnings


def _sprint_row(conn: sa.Connection, sprint_id: str) -> Optional[tuple[str, str, LaneRow]]:
    """スプリントの進行表を指すレーンの行（計画の ID, レーン, 行）。レーンの外のスプリントなら None。"""
    sprint = store.read_table(conn, sprint_id)
    parent = store.read_table(conn, sprint["parent_id"]) if sprint and sprint["parent_id"] else None
    if parent is None or parent["level"] != "lane" or parent["parent_id"] is None:
        return None
    view, _ = read_plan(conn, parent["parent_id"])
    lane = next((n for n, s in view.lanes.items() if s.table_id == parent["id"]), None)
    rows = fold_lane(store.read_events(conn, parent["id"])) if lane else []
    row = next((r for r in rows if r.sprint_id == sprint_id), None)
    return (view.table_id, lane, row) if lane and row else None


def end_row(conn: sa.Connection, sprint_id: str, outcome: str, actor: str) -> Optional[dict[str, Any]]:
    """スプリントの終わりをレーンの進行表の行へ写す唯一の関数（決定 17）。次に始める行を返す。

    スプリントの進行表の `completed` か `cancelled` を書いたのと同じトランザクションの中で呼ぶ。
    レーンの行が無ければ None。同じスプリントの行が既に終わっていれば書かずに同じ結果を返す（冪等）。
    """
    found = _sprint_row(conn, sprint_id)
    if found is None:
        return None
    plan_id, lane, row = found
    if row.state == RUNNING:
        draft = model.EventDraft("row_ended", actor, row.key, state=outcome, payload={"sprint_id": sprint_id})
        checked_write(conn, plan_id, lane, [draft])
    view, rows = _lane_rows(conn, plan_id, lane)
    return next_row(view, rows, lane)


def _mark(conn: sa.Connection, sprint_id: str, build: Callable[[LaneRow], model.EventDraft]) -> Optional[int]:
    found = _sprint_row(conn, sprint_id)
    if found is None:
        return None
    plan_id, lane, row = found
    return checked_write(conn, plan_id, lane, [build(row)])[0]


def record_answer(conn: sa.Connection, sprint_id: str, answer: dict[str, Any]) -> Optional[int]:
    """オーガナイザーが送った答えを、スプリントを持つレーンの行へ載せる（3346-5）。

    載った時点が、そのレーンの conductor が次の `ndf progress next` を打つ契機になる。レーンの外なら None。
    """
    return _mark(conn, sprint_id, lambda r: model.EventDraft("row_answered", "organizer", r.key, payload={"answer": answer}))


def record_resumed(conn: sa.Connection, sprint_id: str, actor: str) -> Optional[int]:
    """conductor が答えの後の `ndf progress next` を打った印。レーンの外なら None。"""
    return _mark(conn, sprint_id, lambda r: model.EventDraft("row_resumed", actor, r.key))


def cancel_pending(conn: sa.Connection, view: PlanView, reason: str) -> list[str]:
    """計画の中止: 全レーンの未着手の行を中止にする（オーガナイザー）。実行中の行のスプリントの ID を返す。

    計画ごと止めるので、別のレーンの after が指す行（I18）の決まりは当てない。
    """
    running = []
    for name, evs in read_lanes(conn, view).items():
        rows = fold_lane(evs)
        drafts = [model.EventDraft("row_cancelled", "organizer", r.key, reason=reason) for r in rows if r.state == PENDING]
        running += [r.sprint_id for r in rows if r.state == RUNNING and r.sprint_id]
        if drafts:
            store.append_events(conn, view.lanes[name].table_id, drafts, evs[-1].seq)
    return running
