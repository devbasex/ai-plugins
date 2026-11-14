"""進行表の型と純粋な判断。

進行表の状態の正本は進行の事象（追記だけ）で、行の今の状態は事象を畳み込んで求める（`fold`）。
事象の種類ごとの書き手と遷移は契約 `contracts/workflow/progress.json` が持ち（I6 の正本）、
事象を追記する関数（`store.append_events`）は書く前に `check_event` を通す。

工程の定義の型（`Snapshot`・`StageDef`・`StepDef`）と、定義の順序の決まり（`check_order`）・
有効と無効の決め方（`resolve_enabled`）もここに置く。ファイルを読む検査は `definition` にある。
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from ndf import contracts

TABLE = "workflow/progress.json"
SCHEMA = "workflow/progress.schema.json"
# 行の事象の from に書く「まだ無い行」の印
NEW = "new"
# 進行表の識別子に入れるリポジトリの短い鍵の長さ（sha256 の 16 進の桁数）
REPO_KEY_LENGTH = 12


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


# --- 工程の定義の型 ----------------------------------------------------------------------


@dataclass(frozen=True)
class StepDef:
    """工程の中のステップ（DBOS のステップ 1 つ）。`output_ref` は出力の規約の参照。"""

    id: str
    stage: str
    call: str
    kind: str
    range: str
    subject: Optional[str] = None
    gate: Optional[str] = None
    args: dict[str, Any] = field(default_factory=dict)
    output_ref: Optional[str] = None


@dataclass(frozen=True)
class StageDef:
    """工程（工程表の行）。`name` が行の鍵、`id` は工程の文書の名前。"""

    id: str
    name: str
    phase: str
    steps: tuple[StepDef, ...]
    when: tuple[str, ...] = ()
    per_issue: bool = False
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    input_ref: Optional[str] = None
    output_ref: Optional[str] = None


@dataclass(frozen=True)
class Snapshot:
    """モードの定義のスナップショット。進行表の定義の改訂に JSON で残す。"""

    mode: str
    version: str
    sha256: str
    height: int
    stages: tuple[StageDef, ...]

    def stage(self, name: str) -> StageDef:
        for s in self.stages:
            if s.name == name:
                return s
        raise Refused(f"モード {self.mode} の工程に {name} は無い")

    def steps(self) -> list[StepDef]:
        return [st for s in self.stages for st in s.steps]


@dataclass(frozen=True)
class Enablement:
    """工程の有効と無効。`source` は決めた順位（definition・project・sprint）。"""

    enabled: bool
    reason: Optional[str]
    source: str


def snapshot_dict(snap: Snapshot) -> dict[str, Any]:
    """スナップショットを JSON の形にする（定義の改訂に書く）。"""

    def stage(s: StageDef) -> dict[str, Any]:
        steps = [{k: v for k, v in vars(st).items() if k != "stage"} for st in s.steps]
        return {**{k: (list(v) if isinstance(v, tuple) else v) for k, v in vars(s).items() if k != "steps"}, "steps": steps}

    return {
        "mode": snap.mode,
        "version": snap.version,
        "sha256": snap.sha256,
        "height": snap.height,
        "stages": [stage(s) for s in snap.stages],
    }


def snapshot_from(raw: Mapping[str, Any]) -> Snapshot:
    """`snapshot_dict` の逆。"""

    def stage(s: Mapping[str, Any]) -> StageDef:
        steps = tuple(StepDef(stage=s["name"], **st) for st in s["steps"])
        rest = {k: (tuple(v) if isinstance(v, list) else v) for k, v in s.items() if k != "steps"}
        return StageDef(steps=steps, **rest)

    return Snapshot(raw["mode"], raw["version"], raw["sha256"], raw["height"], tuple(stage(s) for s in raw["stages"]))


# --- 順序の決まり（I8〜I10） ---------------------------------------------------------------


def _where(step: StepDef) -> str:
    return f"{step.stage}/{step.id}"


def _range_errors(steps: list[StepDef]) -> list[str]:
    """review・test の範囲は差分だけ、verify の範囲はリリースした版だけ（I9）。"""
    errs = []
    for st in steps:
        if st.kind in ("review", "test") and st.range not in ("sprint_diff", "issue_diff"):
            errs.append(f"I9: {_where(st)} の {st.kind} の範囲 {st.range} が差分（sprint_diff・issue_diff）でない")
        if st.kind == "verify" and st.range != "released":
            errs.append(f"I9: {_where(st)} の verify の範囲 {st.range} がリリースした版（released）でない")
    return errs


def _after_release_errors(steps: list[StepDef]) -> list[str]:
    """同じ範囲の review・test がリリースより後ろにある（I8a）。verify がリリースより前・リリースが無い（I8b）。"""
    errs = []
    released: dict[str, StepDef] = {}
    for st in steps:
        if st.kind in ("review", "test") and st.range in released:
            errs.append(
                f"I8a: {_where(st)} の {st.kind} が、同じ範囲 {st.range} をリリースする {_where(released[st.range])} より後ろにある"
            )
        if st.kind == "verify" and not released:
            errs.append(f"I8b: {_where(st)} の verify より前にリリースのステップが無い")
        if st.kind == "release":
            released.setdefault(st.range, st)
    return errs


def _duplicate_errors(steps: list[StepDef]) -> list[str]:
    """同じ（範囲, 対象）の review が 2 つ・同じ範囲の test が 2 つ（I10）。"""
    reviews = Counter((st.range, st.subject) for st in steps if st.kind == "review")
    tests = Counter(st.range for st in steps if st.kind == "test")
    errs = [f"I10: 範囲 {r} の対象 {s} のレビューが {n} つある" for (r, s), n in sorted(reviews.items(), key=str) if n > 1]
    return errs + [f"I10: 範囲 {r} のテストが {n} つある" for r, n in sorted(tests.items()) if n > 1]


def check_order(steps: list[StepDef]) -> list[str]:
    """モードの全ステップを並びの順に平らにした一覧で、順序・範囲・重複の決まりを確かめ、破った理由を返す。"""
    return _range_errors(steps) + _after_release_errors(steps) + _duplicate_errors(steps)


def check_unique_stage_names(stages: Iterable[StageDef]) -> list[str]:
    """同じ工程の名前を 2 つの工程が受け持たない（工程の定義の登録の決まり）。"""
    return [f"工程の名前 {n} を 2 つの工程が受け持つ" for n in contracts.duplicates(s.name for s in stages)]


# --- 有効と無効（決定 8） -------------------------------------------------------------------


def _by_definition(stage: StageDef, facts: Optional[Mapping[str, bool]]) -> Enablement:
    if facts is None:
        return Enablement(True, None, "definition")
    for word in stage.when:
        if not facts[word]:
            return Enablement(False, word, "definition")
    return Enablement(True, None, "definition")


def resolve_enabled(
    snap: Snapshot,
    facts: Optional[Mapping[str, bool]],
    declared: Mapping[str, Mapping[str, Any]],
    disables: Mapping[str, str],
) -> dict[str, Enablement]:
    """工程ごとの有効と理由を 3 つの順位で決める。スプリントの引数 > プロジェクトの宣言 > 定義の条件。

    `facts` が None なら定義の条件はまだ見ない（進行表の作成の時点。条件は工程を流すときに見る）。
    `declared` は `.ndf/project.json` の `workflow.stages`（工程の名前 → `{"enabled": 真偽, "reason": 理由}`）。
    """
    out = {}
    for stage in snap.stages:
        if stage.name in disables:
            out[stage.name] = Enablement(False, disables[stage.name], "sprint")
        elif stage.name in declared:
            decl = declared[stage.name]
            enabled = bool(decl["enabled"])
            out[stage.name] = Enablement(enabled, None if enabled else str(decl["reason"]), "project")
        else:
            out[stage.name] = _by_definition(stage, facts)
    return out


def unknown_stages(snap: Snapshot, names: Iterable[str]) -> list[str]:
    """モードの工程に無い名前。"""
    known = {s.name for s in snap.stages}
    return sorted(set(names) - known)
