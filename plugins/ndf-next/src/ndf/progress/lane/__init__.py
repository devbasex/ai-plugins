"""レーンの進行表（#3097）: レーンの中のスプリントの順番表と、書き込みが通る計画の決まり（レーンをまたぐ制約）。

進行表は 3 段ある。計画の進行表（オーガナイザーが持つ。`ndf.progress.plan`）→ レーンの進行表（レーンの conductor が
持つ。このモジュール）→ スプリントの進行表（`ndf progress`）。

- レーンの進行表の行はスプリント 1 つで、状態は 未着手・実行中（スプリントの進行表の ID を持つ）・完了・中止
- レーンの進行表へ事象を書くのはこのモジュールの関数だけで、書く前に計画の決まり（触る場所の重なり I13・
  別のレーンの `after` が指す行 I18・レーンの間の課題の重なり I19・上限 I20・`after` の輪 I21・行の位置 I12a）で確かめる
- 計画の決まりは計画の進行表の事象を畳み込んで読む（`fold_plan`）。畳み込み（`lane.fold`）と判定（`lane.rules`）は
  純粋な関数で、DB を読まない。型と状態の語は `lane.types` にあり、このモジュールが同じ名前で公開する
- 答えの事象（`row_answered`）の後に conductor が `ndf progress next` を打った印（`row_resumed`）が無い行は、
  「答えの後の next 待ち」として読める（`awaiting_next`）
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

import sqlalchemy as sa

from ndf import identity
from ndf.core import durable
from ndf.progress import model, store
from ndf.progress.lane.fold import fold_lane as fold_lane
from ndf.progress.lane.fold import fold_plan as fold_plan
from ndf.progress.lane.rules import (
    after_met as after_met,
    change_errors as change_errors,
    lane_state as lane_state,
    next_row as next_row,
    plan_complete as plan_complete,
    plan_errors as plan_errors,
    touch_hit as touch_hit,
    wakeable_lanes as wakeable_lanes,
)
from ndf.progress.lane.types import (
    CANCELLED as CANCELLED,
    COMPLETED as COMPLETED,
    DONE_STATES as DONE_STATES,
    PAUSED as PAUSED,
    PENDING as PENDING,
    PLAN_ROW as PLAN_ROW,
    RUNNING as RUNNING,
    WAITING_AFTER as WAITING_AFTER,
    Lanes as Lanes,
    LaneRow as LaneRow,
    LaneSpec as LaneSpec,
    PlanView as PlanView,
    Request as Request,
)

# 行の鍵の接頭辞（r1・r2…）
ROW_PREFIX = "r"
# 計画が記録する形の番号の鍵。違えばレーンを起動しない（#3344 の決定 86）
FORMAT_KEYS = ("store_format", "format", "table_version")
# 計画の決まりを確かめて書く操作の排他の鍵の接頭辞（store.run_lock の鍵と値を分ける）
PLAN_LOCK_PREFIX = "plan-write:"


class FormatMismatch(Exception):
    """計画が記録した形の番号と自分のものが違う。"""

    kind = "format_mismatch"


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
    return fold_plan(plan_id, events, meta["repo"]), events


def read_lanes(conn: sa.Connection, view: PlanView) -> dict[str, list[model.Event]]:
    """計画のレーンごとの、レーンの進行表の事象。"""
    return {name: store.read_events(conn, spec.table_id) for name, spec in view.lanes.items()}


def folded(events: Mapping[str, list[model.Event]]) -> dict[str, list[LaneRow]]:
    return {name: fold_lane(evs) for name, evs in events.items()}


def read_view(conn: sa.Connection, plan_id: str) -> tuple[PlanView, dict[str, list[LaneRow]]]:
    """計画の進行表の畳み込みと、レーンごとの順番表の行。"""
    view, _ = read_plan(conn, plan_id)
    return view, folded(read_lanes(conn, view))


def _new_key(events: list[model.Event]) -> str:
    added = [e.draft for e in events if e.draft.kind == "row_added"]
    return f"{ROW_PREFIX}{len(added) + 1}"


def _with(events: list[model.Event], draft: model.EventDraft) -> list[model.Event]:
    """確かめるために、下書きを書いた後の事象の並び（番号と時刻は仮）。"""
    last = events[-1] if events else None
    seq = last.seq + 1 if last is not None else 1
    at = last.at if last is not None else datetime.min
    return [*events, model.Event(seq, at, model.recorded("lane", draft))]


def lock_plan(conn: sa.Connection, plan_id: str) -> None:
    """計画の決まりを確かめて書く操作を、計画ごとにトランザクションの終わりまで直列にする。

    レーンをまたぐ決まり（I13・I19 など）は全レーンを読んで確かめるが、書くのは 1 つのレーンの進行表だけである。
    PostgreSQL では別のレーンへの同時の書き込みが互いの未確定の変更を見ずに検査を通るため、計画の鍵の
    `pg_advisory_xact_lock` で待ち合わせる。SQLite は書き込みのトランザクションが 1 つに限られるので何もしない。
    """
    if conn.dialect.name != "postgresql":
        return
    key = int.from_bytes(hashlib.sha256(f"{PLAN_LOCK_PREFIX}{plan_id}".encode()).digest()[:4], "big", signed=True)
    conn.execute(sa.text("SELECT pg_advisory_xact_lock(:space, :key)"), {"space": store.PG_LOCK_SPACE, "key": key})


def checked_write(conn: sa.Connection, plan_id: str, lane: str, drafts: list[model.EventDraft]) -> list[int]:
    """レーンの進行表へ組み換えと状態の下書きを書く唯一の関数。1 件ずつ計画の決まりで確かめ、新しく当たる箇所があれば `Refused`。

    計画の決まりの判定は書く前の状態と比べ、書き込みで新しく生まれた違反だけを断る（前からある違反で
    無関係の組み換えを止めない）。読む前に計画の排他（`lock_plan`）を取る。
    """
    lock_plan(conn, plan_id)
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
    key = _new_key(store.read_events(conn, view.lanes[lane].table_id))
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
    inp = {"kind": "sprint", "issues": sorted(set(issues)), "args": src.args}
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
    lock_plan(conn, view.table_id)
    running = []
    for name, evs in read_lanes(conn, view).items():
        rows = fold_lane(evs)
        drafts = [model.EventDraft("row_cancelled", "organizer", r.key, reason=reason) for r in rows if r.state == PENDING]
        running += [r.sprint_id for r in rows if r.active]
        if drafts:
            store.append_events(conn, view.lanes[name].table_id, drafts, evs[-1].seq)
    return running
