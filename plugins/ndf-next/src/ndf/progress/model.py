"""進行表の型と純粋な判断。

進行表の状態の正本は進行の事象（追記だけ）で、行の今の状態は事象を畳み込んで求める（`fold`）。
事象の種類ごとの書き手と遷移は契約 `contracts/workflow/progress.json` が持ち（I6 の正本）、
事象を追記する関数（`store.append_events`）は書く前に `check_event` を通す。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Optional

from ndf import contracts

TABLE = "workflow/progress.json"
SCHEMA = "workflow/progress.schema.json"
# 行の事象の from に書く「まだ無い行」の印
NEW = "new"
# 進行表の識別子に入れるリポジトリの短い鍵の長さ（sha256 の 16 進の桁数）
REPO_KEY_LENGTH = 12
# 進行表のワークフロー（耐久ワークフロー）が事象を書くときの書き手（progress.json の actors の鍵）
WORKFLOW_ACTOR = "workflow"
# 承認ゲートを MVV 判定で省ける進め方（文脈の pace）・省いた答えの答えた者・判定の結果を持つ文脈の鍵（context.schema.json）
MVV_PACES = ("fast", "auto")
MVV_SERVICE, MVV_KEY = "service:mvv", "mvv_gate"
# 承認ゲートの問いの id の長さ（sha256 の頭）
QUESTION_ID_LENGTH = 16


class ProgressTableError(contracts.ContractError):
    """進行表の状態と事象の表（progress.json）が読めない・形が違う・語が食い違う。"""


class Refused(Exception):
    """不変条件・書き手・遷移で操作を断った。`current_id` は同じ進行表が既にあるときの今の識別子。"""

    def __init__(self, reason: str, current_id: Optional[str] = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.current_id = current_id


# --- 状態と事象の表 ----------------------------------------------------------------------


@dataclass(frozen=True)
class EventRule:
    """事象の種類 1 つの決まり。`from_` は書ける今の行の状態（None ならどれでも）、`state` は事象の後の状態。"""

    level: str
    kind: str
    actors: frozenset[str]
    scope: str
    from_: Optional[tuple[str, ...]]
    state: Optional[str | tuple[str, ...]]


def _state_names(raw: dict[str, Any]) -> set[str]:
    names = {s for states in raw["row_states"].values() for s in states}
    return names | {s for t in raw["table_states"].values() for s in t["states"]}


def _listed(value: Optional[str | list[str]]) -> list[str]:
    """表の 1 つの値か配列を配列にする。無ければ空。"""
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


def _event_errors(raw: dict[str, Any], names: set[str]) -> list[str]:
    errs = []
    for ev in raw["events"]:
        where = f"{ev['level']}/{ev['kind']}"
        errs += [f"{where}: 知らない書き手 {a}" for a in ev["actors"] if a not in raw["actors"]]
        errs += [f"{where}: 知らない状態 {s}" for s in _listed(ev.get("from")) if s != NEW and s not in names]
        errs += [f"{where}: 知らない状態 {s}" for s in _listed(ev.get("state")) if s not in names]
    dup = contracts.duplicates(f"{ev['level']}/{ev['kind']}" for ev in raw["events"])
    return errs + [f"事象の種類が 2 つある: {d}" for d in dup]


def _consistency_errors(raw: dict[str, Any]) -> list[str]:
    """状態の語・書き手の語が表の中で食い違っていないか。"""
    names = _state_names(raw)
    errs = [f"表示の語が無い状態: {s}" for s in sorted(names - set(raw["labels"]))]
    errs += [f"終わりの状態の一覧に知らない状態: {s}" for s in raw["terminal"] if s not in names]
    errs += [
        f"{lv}: 初めの状態 {t['initial']} が状態の一覧に無い" for lv, t in raw["table_states"].items() if t["initial"] not in t["states"]
    ]
    kinds = {ev["kind"] for ev in raw["events"]}
    errs += [f"理由が要る事象の一覧に知らない種類: {k}" for k in raw["reason_required"] if k not in kinds]
    return errs + _event_errors(raw, names)


@contracts.cached_reader
def table() -> dict[str, Any]:
    """進行表の状態と事象の表。形と語の食い違いを確かめてから返す（1 プロセスに 1 回だけ読む）。"""
    raw = contracts.load_as(TABLE, ProgressTableError)
    with contracts.raised_as(ProgressTableError):
        contracts.require_schema(raw, SCHEMA, ProgressTableError, "進行表の状態と事象の表")
    errs = _consistency_errors(raw)
    if errs:
        raise ProgressTableError(f"進行表の状態と事象の表が食い違う: {'; '.join(errs)}")
    return raw


@contracts.cached_reader
def rules() -> dict[tuple[str, str], EventRule]:
    """（階層, 事象の種類）→ 決まり。"""
    out = {}
    for ev in table()["events"]:
        state = ev.get("state")
        out[(ev["level"], ev["kind"])] = EventRule(
            ev["level"],
            ev["kind"],
            frozenset(ev["actors"]),
            ev["scope"],
            tuple(ev["from"]) if "from" in ev else None,
            tuple(state) if isinstance(state, list) else state,
        )
    return out


def label(state: Optional[str]) -> str:
    """状態の表示の語（未着手・実行中など）。状態が無ければ空。"""
    return table()["labels"][state] if state else ""


def gate_label(gate: str) -> str:
    """承認ゲートの種類の表示の語（設計 Pull Request のマージ・本番系へ届く操作）。"""
    return table()["gates"][gate]


def question_id(table_id: str, rev: int, stage: str, step: str, anchor: int) -> str:
    """承認ゲートの問いの id。待ちに入った事象の番号（`anchor`）を含むため、差し戻しで流し直した問いは別の id になる。"""
    text = f"{table_id}\0{rev}\0{stage}\0{step}\0{anchor}"
    return hashlib.sha256(text.encode()).hexdigest()[:QUESTION_ID_LENGTH]


def mvv_skip(done: Mapping[str, Mapping[str, Any]], before: Optional[str], gate: Optional[str]) -> Optional[dict[str, Any]]:
    """承認ゲート `gate` を省くと決めた MVV 判定。無ければ None。

    読むのは承認ゲートの直前のステップ `before`（専用の判定 `ndf records mvv gate`）の済んだ結果（`done`）の出力 `MVV_KEY`
    だけである（#3745）。先のステップ（設計など）が出力に省けると書いても採らず、直前の判定が止めた承認ゲートは省かない。
    """
    report = done[before] if before is not None and before in done else None
    held = _outputs(report).get(MVV_KEY) if report is not None else None
    return held if isinstance(held, dict) and held.get("gate") == gate and held.get("skip") is True else None


def _outputs(report: Mapping[str, Any]) -> Mapping[str, Any]:
    """済んだステップの出力（形が違えば空）。"""
    got = report["outputs"] if "outputs" in report else None
    return got if isinstance(got, dict) else {}


def mvv_answer(step: str, kind: str, answer: str, held: Optional[Mapping[str, Any]], gate_id: str) -> tuple[dict[str, Any], str]:
    """MVV 判定で承認ゲートを省いた答えの事象の payload と理由。答えた者は `MVV_SERVICE`、判定の結果（`mvv_skip`）を `MVV_KEY` に添える。

    人の答えと同じく問いの id（`gate_id`）・区分（`gate`）・見た対象（`seen`。判定した PR の head）を持つ。答えを読む前の照合
    （`gate.recheck`）が今の head と比べ、判定の後に進んでいれば待ちへ戻して人へ問う。承認した head（`ndf answer head`）もここから読む。
    """
    gate = held.get("gate") if held is not None else None
    head = held.get("head") if held is not None else None
    payload = {
        "answer": answer,
        "step": step,
        "kind": kind,
        "notes": None,
        "by": MVV_SERVICE,
        "gate": gate,
        "gate_id": gate_id,
        # 形は gate.Target.as_dict（承認資料は作らないので sha256 は無い）
        "seen": {"head": head if isinstance(head, str) else None, "material_sha256": None},
        MVV_KEY: dict(held) if held is not None else None,
    }
    return payload, f"MVV 判定で承認ゲート {gate} を省いた"


def branch_prefix(kind: str) -> str:
    """ブランチの接頭辞（sprint・issue・design・release）。"""
    return table()["branch_prefixes"][kind]


def is_terminal(state: Optional[str]) -> bool:
    return state in table()["terminal"]


# --- 事象と行 ----------------------------------------------------------------------------


@dataclass(frozen=True)
class EventDraft:
    """書く前の事象。`row_key` が無ければ進行表全体の事象。`rev` はスプリントの定義の改訂。"""

    kind: str
    actor: str
    row_key: Optional[str] = None
    state: Optional[str] = None
    reason: Optional[str] = None
    payload: Optional[dict[str, Any]] = None
    dedupe_key: Optional[str] = None
    rev: Optional[int] = None


@dataclass(frozen=True)
class Event:
    """書いた事象。`seq` は進行表ごとの番号、`at` は書いた時刻（UTC）。"""

    seq: int
    at: datetime
    draft: EventDraft

    def as_dict(self) -> dict[str, Any]:
        d = self.draft
        return {
            "seq": self.seq,
            "at": self.at.isoformat(),
            "rev": d.rev,
            "row_key": d.row_key,
            "kind": d.kind,
            "state": d.state,
            "actor": d.actor,
            "reason": d.reason,
            "payload": d.payload,
        }


@dataclass(frozen=True)
class RowState:
    """行の今の状態と、状態を最後に変えた事象。"""

    state: str
    seq: int
    at: Optional[datetime]
    reason: Optional[str] = None
    payload: Optional[dict[str, Any]] = None


def table_id(level: str, repo: str, name: str) -> str:
    """進行表の識別子 `<階層>:<リポジトリの短い鍵>:<名前>`。同じ 3 つの値から常に同じ文字列（決定 15）。"""
    key = hashlib.sha256(repo.encode()).hexdigest()[:REPO_KEY_LENGTH]
    return f"{level}:{key}:{name}"


def resulting_state(level: str, draft: EventDraft) -> Optional[str]:
    """事象の後の状態。決まりが 1 つの状態を持てばそれ、複数なら下書きの値、無ければ None。"""
    rule = rules()[(level, draft.kind)]
    return rule.state if isinstance(rule.state, str) else draft.state


def recorded(level: str, draft: EventDraft) -> EventDraft:
    """書く事象。状態は決まりで定まる値（`resulting_state`）にする。"""
    return replace(draft, state=resulting_state(level, draft))


def rows_for(level: str, events: Iterable[Event], draft: EventDraft) -> dict[str, RowState]:
    """下書きを確かめる行の今の状態。スプリントの行は下書きの定義の改訂の事象だけで畳む。"""
    return fold(events, draft.rev if level == "sprint" else None)


def fold(events: Iterable[Event], rev: Optional[int] = None) -> dict[str, RowState]:
    """事象から行の今の状態を畳み込む。`rev` を渡せばその改訂の事象だけを使う。状態の無い事象は状態を変えない。"""
    rows: dict[str, RowState] = {}
    for ev in events:
        d = ev.draft
        if d.row_key is None or (rev is not None and d.rev != rev) or d.state is None:
            continue
        rows[d.row_key] = RowState(d.state, ev.seq, ev.at, d.reason, d.payload)
    return rows


def table_state(level: str, events: Iterable[Event]) -> Optional[str]:
    """進行表全体の状態。状態を持たない階層（レーン）は None。"""
    spec = table()["table_states"].get(level)
    if spec is None:
        return None
    state = spec["initial"]
    for ev in events:
        if ev.draft.row_key is None and ev.draft.state is not None:
            state = ev.draft.state
    return state


# --- 書き手と遷移の確かめ ----------------------------------------------------------------


def check_writer(level: str, kind: str, actor: str) -> EventRule:
    """事象の種類と書き手の組が表にあるかを確かめ、決まりを返す。無ければ `Refused`（I6）。"""
    rule = rules().get((level, kind))
    if rule is None:
        raise Refused(f"{level} の進行表に事象 {kind} は無い")
    if actor not in rule.actors:
        raise Refused(f"{level} の進行表の事象 {kind} を {actor} は書けない（書けるのは {', '.join(sorted(rule.actors))}）")
    return rule


def _check_fields(level: str, rule: EventRule, draft: EventDraft) -> None:
    if draft.kind in table()["reason_required"] and (draft.reason is None or not draft.reason.strip()):
        raise Refused(f"事象 {draft.kind} には理由が要る")
    if level == "sprint" and draft.rev is None:
        raise Refused(f"スプリントの事象 {draft.kind} に定義の改訂が無い")
    if rule.scope == "row" and draft.row_key is None:
        raise Refused(f"事象 {draft.kind} には行の鍵が要る")
    if rule.scope == "table" and draft.row_key is not None:
        raise Refused(f"事象 {draft.kind} は進行表全体の事象で、行 {draft.row_key} には書けない")
    if isinstance(rule.state, tuple) and draft.state not in rule.state:
        raise Refused(f"事象 {draft.kind} の状態は {', '.join(rule.state)} のどれか（渡された値 {draft.state}）")


def check_event(level: str, rows: Mapping[str, RowState], whole: Optional[str], draft: EventDraft) -> None:
    """1 件の事象が、今の行の状態と進行表全体の状態から許されるかを確かめる。許されなければ `Refused`。

    `whole` は進行表全体の今の状態（`table_state`）。完了か中止の進行表には何も書けない。
    """
    rule = check_writer(level, draft.kind, draft.actor)
    _check_fields(level, rule, draft)
    if whole is not None and is_terminal(whole):
        raise Refused(f"進行表は{label(whole)}で、事象 {draft.kind} を書けない")
    if draft.row_key is None or rule.from_ is None:
        return
    current = rows[draft.row_key].state if draft.row_key in rows else NEW
    if current not in rule.from_:
        now = "まだ無い" if current == NEW else label(current)
        allowed = "・".join("まだ無い" if s == NEW else label(s) for s in rule.from_)
        raise Refused(f"行 {draft.row_key} は{now}で、事象 {draft.kind} を書けない（書けるのは {allowed} の行）")


def apply(level: str, rows: Mapping[str, RowState], draft: EventDraft, seq: int) -> dict[str, RowState]:
    """事象を書いた後の行の状態（続けて確かめる下書きのために、書く前に求める）。"""
    out = dict(rows)
    state = resulting_state(level, draft)
    if draft.row_key is not None and state is not None:
        out[draft.row_key] = RowState(state, seq, None, draft.reason, draft.payload)
    return out
