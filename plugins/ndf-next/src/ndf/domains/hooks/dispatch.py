"""guard の表を順に呼び、判定を 1 つにまとめ、注入を切り、出力を契約表の形にする（#3345 の決定 88・90〜92）。

- 表（`contracts/hooks/guards.json`）のうち、事象と Tool が当たり、`NDF_HOOK_OFF` で止めておらず、ランタイムの
  契約表で外していない行を `order` の昇順に呼ぶ。`strict` の行はエントリポイントが先に呼ぶので、ここでは呼ばない
- 締め切りの残りが余白を下回ったら、残りの guard を呼ばずに通す
- guard の例外はその guard を `pass` にして、計測に例外の種類を書く
- 同じ拒否は数え手で数え、上限を超えたら案内へ下げる。差し戻しは利用者の入力ごとの合計で数え、上限を超えたら通す
- 複数の判定は `deny` > `pushback` > `advise` > `pass` の順で 1 つにまとめる
"""

from __future__ import annotations

import hashlib
import importlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ndf.domains.hooks import tables
from ndf.domains.hooks.budget import Budget
from ndf.domains.hooks.context import HookContext
from ndf.domains.hooks.counter import Counter
from ndf.domains.hooks.verdict import PASS, REASON_KINDS, Verdict, advise, strength

_DOWNGRADED = "（同じ拒否が続いたため、止めずに案内した）"


@dataclass(frozen=True)
class GuardSpec:
    """guard の表の 1 行。"""

    name: str
    module: str
    events: tuple[str, ...]
    tools: tuple[str, ...]
    order: int
    strict: bool
    suggests: tuple[dict, ...]
    deny_key: str
    limits: tuple[str, ...] = ()
    params: dict = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "GuardSpec":
        return cls(
            name=row["name"],
            module=row["module"],
            events=tuple(row["events"]),
            tools=tuple(row["tools"]),
            order=int(row["order"]),
            strict=bool(row["strict"]),
            suggests=tuple(row["suggests"]),
            deny_key=row["deny_key"],
            limits=tuple(row["limits"]),
            params=dict(row["params"]),
        )

    def applies(self, event: str, tool: str) -> bool:
        return event in self.events and (not self.tools or tool in self.tools)


@dataclass(frozen=True)
class GuardServices:
    """guard へ渡す部品。`params` は表の行の値。"""

    state_dir: Path
    counter: Counter
    params: dict


@dataclass(frozen=True)
class GuardRun:
    """1 つの guard の所要と判定（計測の 1 行の `guards` の要素）。"""

    name: str
    ms: float
    kind: str
    error: str = ""


@dataclass
class Outcome:
    verdict: Verdict
    runs: list[GuardRun] = field(default_factory=list)
    stopped: str = ""
    limited: bool = False
    notes: dict = field(default_factory=dict)


def load_specs() -> list[GuardSpec]:
    return sorted((GuardSpec.from_row(r) for r in tables.guards()), key=lambda s: s.order)


def select(specs: list[GuardSpec], ctx: HookContext, off: frozenset[str]) -> list[GuardSpec]:
    """この事象で呼ぶ guard（`strict` を除く）。"""
    rt = tables.runtimes().get(ctx.runtime)
    skipped = rt["skipped"] if rt else {}
    return [
        s for s in specs if not s.strict and s.applies(ctx.event, ctx.tool) and not tables.is_off(s.name, off) and s.name not in skipped
    ]


def deny_key(spec: GuardSpec, verdict: Verdict, ctx: HookContext) -> str:
    """同じ拒否を数える鍵（guard・理由・対象の hash）。"""
    targets = {"command": ctx.command, "range": verdict.key, "reason": ""}
    target = targets[spec.deny_key] if spec.deny_key in targets else verdict.key
    digest = hashlib.sha256(f"{verdict.reason}\0{target}".encode("utf-8")).hexdigest()[:16]
    return f"{spec.name}:{digest}"


def run(
    ctx: HookContext,
    specs: list[GuardSpec],
    budget: Budget,
    counter: Counter,
    state_dir: Path,
    clock: Callable[[], float] = time.monotonic,
) -> Outcome:
    """表の順に guard を呼び、判定を 1 つにまとめる。"""
    start = clock()
    outcome = Outcome(PASS)
    verdicts: list[Verdict] = []
    for spec in specs:
        if clock() - start > budget.usable:
            outcome.stopped = "deadline"
            break
        began = clock()
        got, error = _call(spec, ctx, GuardServices(state_dir, counter, spec.params))
        if got.kind == "deny" and not counter.allow_deny(deny_key(spec, got, ctx)):
            got = advise(f"{got.reason}{_DOWNGRADED}")
        outcome.runs.append(GuardRun(spec.name, round((clock() - began) * 1000, 3), got.kind, error))
        outcome.notes.update(got.notes)
        verdicts.append(got)
    outcome.verdict = combine(verdicts)
    if outcome.verdict.kind == "pushback" and not counter.allow_pushback():
        outcome.limited = True
        outcome.notes["limited_reason"] = outcome.verdict.reason[:200]
        outcome.verdict = PASS
    return outcome


def _call(spec: GuardSpec, ctx: HookContext, svc: GuardServices) -> tuple[Verdict, str]:
    try:
        got = importlib.import_module(spec.module).check(ctx, svc)
    except Exception as exc:  # noqa: BLE001 利用者の入力: guard の失敗は通す側へ倒し、種類を計測に残す（決定 91）
        return PASS, type(exc).__name__
    return (got if isinstance(got, Verdict) else PASS), ""


def combine(verdicts: list[Verdict]) -> Verdict:
    """最も強い判定の種類で 1 つにまとめる。同じ種類の文は順につなぐ。"""
    if not verdicts:
        return PASS
    kind = min((v.kind for v in verdicts), key=strength)
    if kind == "pass":
        return PASS
    texts = [v.text for v in verdicts if v.kind == kind and v.text]
    joined = "\n\n".join(texts)
    return Verdict(kind, reason=joined) if kind in REASON_KINDS else advise(joined)


def clip(text: str, limit: int, notes_dir: Path) -> str:
    """注入の文を `limit` 字以下にする。超える分は全文をファイルへ書き、文には先頭とパスを入れる（IH8）。"""
    if len(text) <= limit:
        return text
    if limit <= 0:
        return ""
    notes_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    path = notes_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{digest}.md"
    path.write_text(text, encoding="utf-8")
    suffix = f"…（全文: {path}）"
    if len(suffix) >= limit:
        return suffix[-limit:]
    return text[: limit - len(suffix)] + suffix


def render(outcome: Outcome, runtime: str, event: str, budget: Budget, notes_dir: Path) -> tuple[int, str]:
    """終了コード（いつも 0）と標準出力。契約表に形の無い判定は何も出さない。入れた字数を `outcome.notes` に書く。"""
    kind = outcome.verdict.kind
    spec = tables.event_spec(runtime, event)
    if not spec or kind not in spec["outputs"]:
        outcome.notes["inject_chars"] = 0
        return 0, ""
    text = clip(outcome.verdict.text, budget.inject_chars, notes_dir)
    outcome.notes["inject_chars"] = len(text)
    return 0, tables.output_for(runtime, event, kind, text)
