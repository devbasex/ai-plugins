"""プランの形（`plan.json`）。読んで実行の前に確かめ、変えられない値にする。

プランは名前・ステップの並び・触るファイルの宣言（`touches`）・前提のプラン（`needs`）を持つ。キューの中の役として、
前の段の承認ゲートを人へ届ける役（`accepts_gate`）と、PR を `{queue_prs}` に入れないリリースのプラン（`release`）を持てる。
ステップは打つコマンド（`run`）と、止まったときの行き先（`on_cause`・`on_fail`）・承認の答えの行き先（`on_answer`）を持つ。
外部への操作のステップは、済んだかを相手の状態から確かめるコマンド（`probe`）を持てる（B9）。
行き先の値はプランのステップの id か `end`・`stop`・`retry` のどれかで、`on_cause` の種類は止まった理由の表にある（IS3）。
`on_answer` の答えは答えの語彙（`contracts/facts/answers.json`。結果 JSON の `routes` の鍵になる）にある。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ndf.contracts import causes, facts, limits, result
from ndf.contracts.routing import END, PLAN_TARGETS, STOP

# ステップを打つ作業ディレクトリの語。worktree は作業場所（使えなければリポジトリの根）、repo はリポジトリの根
IN_WORKTREE, IN_REPO = "worktree", "repo"
PLACES = (IN_WORKTREE, IN_REPO)
# 上限を書かないプランの、ステップの数の上限（ステップの数の倍）
LIMIT_PER_STEP = 3
# 承認の答えの行き先に書ける語（ステップの id のほか）
ANSWER_TARGETS = (END, STOP)


class PlanError(Exception):
    """プランが読めない・形が違う。止まった理由は invalid。"""


@dataclass(frozen=True)
class Step:
    """プランのステップ 1 つ。"""

    id: str
    run: tuple[str, ...]
    next: Optional[str] = None
    on_fail: Optional[str] = None
    on_cause: Mapping[str, str] = field(default_factory=dict)
    retry: Mapping[str, limits.Policy] = field(default_factory=dict)
    exit_cause: Mapping[int, str] = field(default_factory=dict)
    on_answer: Mapping[str, str] = field(default_factory=dict)
    timeout: Optional[float] = None
    place: str = IN_WORKTREE
    probe: Optional[tuple[str, ...]] = None


@dataclass(frozen=True)
class Plan:
    """プラン。`touches` が None なら触るファイルの宣言が無い（すべてのプランと重なるものとして扱う。#2165）。"""

    name: str
    steps: tuple[Step, ...]
    limit: int
    repo: Optional[str] = None
    worktree: Optional[str] = None
    touches: Optional[tuple[str, ...]] = None
    needs: tuple[str, ...] = ()
    state_dir: Optional[str] = None
    accepts_gate: bool = False
    release: bool = False
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False)

    @property
    def order(self) -> tuple[str, ...]:
        return tuple(s.id for s in self.steps)

    def step(self, step_id: str) -> Optional[Step]:
        found = [s for s in self.steps if s.id == step_id]
        return found[0] if found else None

    def following(self, step_id: str) -> Optional[str]:
        """並びの次のステップの id。最後のステップなら None。"""
        order = self.order
        at = order.index(step_id)
        return order[at + 1] if at + 1 < len(order) else None

    def digest(self) -> str:
        """プランの中身の sha256（キーの並びに依らない）。"""
        return hashlib.sha256(json.dumps(self.raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _opt(raw: Mapping[str, Any], key: str, default: Any) -> Any:
    return raw[key] if key in raw else default


def _policy(step_id: str, kind: str, values: Mapping[str, Any]) -> limits.Policy:
    unknown = sorted(set(values) - set(limits.VALUE_KEYS))
    if unknown:
        raise PlanError(f"ステップ {step_id} の retry.{kind} に知らない鍵: {', '.join(unknown)}")
    return limits.Policy(name=f"step:{step_id}:{kind}", summary="ステップの再試行の上書き", **values)


def _exit_cause(step_id: str, raw: Mapping[str, str]) -> dict[int, str]:
    out: dict[int, str] = {}
    for code, kind in raw.items():
        if not str(code).lstrip("-").isdigit():
            raise PlanError(f"ステップ {step_id} の exit_cause の鍵が終了コードでない: {code}")
        out[int(code)] = kind
    return out


def _step(raw: Mapping[str, Any]) -> Step:
    if not isinstance(raw, Mapping) or not isinstance(raw.get("id"), str) or not raw["id"]:
        raise PlanError(f"ステップに id が無い: {raw!r}")
    sid = raw["id"]
    run = raw.get("run")
    if not isinstance(run, list) or not run or not all(isinstance(a, str) for a in run):
        raise PlanError(f"ステップ {sid} の run は空でない文字列の配列にする")
    retry = {kind: _policy(sid, kind, values) for kind, values in dict(_opt(raw, "retry", {})).items()}
    probe = raw.get("probe")
    if probe is not None and (not isinstance(probe, list) or not probe or not all(isinstance(a, str) for a in probe)):
        raise PlanError(f"ステップ {sid} の probe は空でない文字列の配列にする")
    return Step(
        id=sid,
        run=tuple(run),
        next=raw.get("next"),
        on_fail=raw.get("on_fail"),
        on_cause=dict(_opt(raw, "on_cause", {})),
        retry=retry,
        exit_cause=_exit_cause(sid, dict(_opt(raw, "exit_cause", {}))),
        on_answer=dict(_opt(raw, "on_answer", {})),
        timeout=raw.get("timeout"),
        place=_opt(raw, "in", IN_WORKTREE),
        probe=tuple(probe) if probe is not None else None,
    )


def _target_errors(step: Step, ids: set[str]) -> list[str]:
    """行き先の値の誤り（IS3）。"""
    errs = []
    named = [("next", step.next), ("on_fail", step.on_fail)]
    for label, target in named:
        if target is not None and target not in ids and target not in ANSWER_TARGETS:
            errs.append(f"ステップ {step.id} の {label} が知らないステップ: {target}")
    for kind, target in step.on_cause.items():
        if target not in ids and target not in PLAN_TARGETS:
            errs.append(f"ステップ {step.id} の on_cause.{kind} が知らない行き先: {target}")
    for answer, target in step.on_answer.items():
        if target not in ids and target not in ANSWER_TARGETS:
            errs.append(f"ステップ {step.id} の on_answer.{answer} が知らない行き先: {target}")
    return errs


def _kind_errors(step: Step, table: Mapping[str, Any]) -> list[str]:
    """止まった理由の種類の誤り（IS3）。"""
    kinds = [*step.on_cause, *step.retry, *step.exit_cause.values()]
    return [f"ステップ {step.id} が知らない止まった理由の種類を持つ: {k}" for k in kinds if k not in table]


def _answer_errors(step: Step, answers: set[str]) -> list[str]:
    """承認の答えの語の誤り（答えの語彙に無い答えは結果 JSON の routes の鍵にできない）。"""
    return [f"ステップ {step.id} の on_answer.{a} は答えの語彙に無い" for a in step.on_answer if a not in answers]


def errors(plan: Plan) -> list[str]:
    """プランの形の誤り。空なら流せる。"""
    ids = [s.id for s in plan.steps]
    errs = [f"ステップの id が重なる: {i}" for i in sorted({i for i in ids if ids.count(i) > 1})]
    table = causes.table()
    answers = set(facts.ids(result.ANSWERS))
    for step in plan.steps:
        errs += _target_errors(step, set(ids))
        errs += _kind_errors(step, table)
        errs += _answer_errors(step, answers)
        if step.place not in PLACES:
            errs.append(f"ステップ {step.id} の in は {' か '.join(PLACES)}: {step.place}")
    if plan.limit < 1:
        errs.append(f"limit は 1 以上: {plan.limit}")
    return errs


def _strings(raw: Mapping[str, Any], key: str) -> Optional[tuple[str, ...]]:
    if key not in raw:
        return None
    value = raw[key]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise PlanError(f"{key} は文字列の配列にする")
    return tuple(value)


def _flag(raw: Mapping[str, Any], key: str) -> bool:
    value = _opt(raw, key, False)
    if not isinstance(value, bool):
        raise PlanError(f"{key} は真偽値にする")
    return value


def parse(raw: Any) -> Plan:
    """JSON の値からプランを作り、形を確かめる。誤りは `PlanError`。"""
    if not isinstance(raw, Mapping) or not isinstance(raw.get("name"), str) or not raw["name"]:
        raise PlanError("プランに name が無い")
    steps_raw = raw.get("steps")
    if not isinstance(steps_raw, list) or not steps_raw:
        raise PlanError(f"プラン {raw['name']} に steps が無い")
    steps = tuple(_step(s) for s in steps_raw)
    needs = _strings(raw, "needs")
    plan = Plan(
        name=raw["name"],
        steps=steps,
        limit=int(_opt(raw, "limit", LIMIT_PER_STEP * len(steps))),
        repo=raw.get("repo"),
        worktree=raw.get("worktree"),
        touches=_strings(raw, "touches"),
        needs=needs if needs is not None else (),
        state_dir=raw.get("state_dir"),
        accepts_gate=_flag(raw, "accepts_gate"),
        release=_flag(raw, "release"),
        raw=dict(raw),
    )
    errs = errors(plan)
    if errs:
        raise PlanError(f"プラン {plan.name} の形が違う: {'; '.join(errs)}")
    return plan


def load(path: Path) -> Plan:
    """プランのファイルを読む。読めない・JSON でない・形が違うときは `PlanError`。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PlanError(f"プラン {path} を読めない: {exc.strerror or exc}") from None
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise PlanError(f"プラン {path} が JSON として壊れている: {exc}") from None
    return parse(raw)
