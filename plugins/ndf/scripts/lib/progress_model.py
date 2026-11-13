"""進行表のモデル（#3092 の決定 1・13・16・17・19・20）。純粋な処理で、DB も DBOS も import しない。

進行表はスプリント・レーン・計画の 3 階層で、状態の正本は追記だけの進行の事象（`Event`）である。行の今の状態は
行の鍵ごとに最新の `state` を持つ事象から畳み込む（`fold`）。事象の種類と書き手の役の組（`WRITERS`）と、
状態の遷移（`check_event`）・レーンをまたぐ決まり（`check_lane_change`）の判定はここだけが持ち、追記の関数
（`progress_db.append_events`）が書く直前に呼ぶ。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from typing import Optional

LEVELS = ("plan", "lane", "sprint")

# 行の状態（スプリント）と、その表示の語
PENDING, RUNNING, FINISHED, FAILED, DISABLED, CANCELLED = "pending", "running", "finished", "failed", "disabled", "cancelled"
COMPLETED = "completed"  # 進行表全体・レーンの行・計画の完了
TERMINAL = (FINISHED, DISABLED, CANCELLED)
LABELS = {PENDING: "未着手", RUNNING: "実行中", FINISHED: "終了", FAILED: "失敗", DISABLED: "無効", CANCELLED: "中止", COMPLETED: "完了"}
TABLE_KEY = ""  # 進行表全体の事象の行の鍵

_S, _L, _P, _ALL = ("sprint",), ("lane",), ("plan",), LEVELS
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
    "created": PENDING,
    "started": RUNNING,
    "finished": FINISHED,
    "failed": FAILED,
    "skipped": DISABLED,
    "disabled": DISABLED,
    "enabled": PENDING,
    "retry": PENDING,
    "manual_finished": FINISHED,
    "cancelled": CANCELLED,
    "completed": COMPLETED,
    "row_added": PENDING,
    "row_started": RUNNING,
    "row_cancelled": CANCELLED,
    "plan_started": RUNNING,
    "plan_completed": COMPLETED,
    "plan_cancelled": CANCELLED,
}
# スプリントの行の遷移（状態の図）。（種類 → 許す前の状態。None は行がまだ無い）
_FROM = {
    "created": (None,),
    "carried": (None,),
    "started": (PENDING,),
    "finished": (RUNNING,),
    "failed": (PENDING, RUNNING),
    "skipped": (PENDING,),
    "disabled": (PENDING,),
    "enabled": (DISABLED,),
    "retry": (FAILED,),
    "manual_finished": (PENDING, FAILED),
    "adopted": (None, PENDING),
    "cancelled": (PENDING, RUNNING, FAILED),
    "answered": (RUNNING, FAILED, PENDING),
    "row_added": (None,),
    "row_started": (PENDING,),
    "row_ended": (RUNNING,),
    "row_cancelled": (PENDING,),
    "row_moved": (PENDING,),
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
    return r.state if (r := rows.get(TABLE_KEY)) else None


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


def with_rev(drafts: list, rev: int) -> list:
    return [replace(d, rev=rev) if d.rev is None else d for d in drafts]
