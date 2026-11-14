"""周に入れる課題の選定（RF7）。

候補の行（課題 1 つの事実）ごとに、選定から外す理由を優先の順で 1 つ決め、外す理由の無い行だけを枠の数まで選ぶ。

- 「直して出し直す」と答えた課題（先頭に選ぶ課題 `first`）は、棚卸しの判断待ちより先に選定へ残す（#2722）
- 外す理由のある行は枠を使わない。枠を数えるのは選んだ行だけである（#2746）
- 開いている子の課題を持つ親の課題は選ばない（#2788）

外す理由の優先の順は `RULES` の並びで、上ほど先に当たる。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Optional

from ndf.domains.loop import record_decide as rec

# 外す理由の鍵
PRIOR = "prior-loop-wait"
KEEP = "pick-first"
WAITING = "backlog-wait"
PENDING = "held-lap"
BLOCKED = "gate-or-merge-wait"
EXCLUDED = "excluded-in-loop"
NEEDS = "unmet-prerequisite"
PARENT = "open-sub-issues"
CREATED = "created-in-lap"
CAPACITY = "lap-full"


class CandidateError(ValueError):
    """候補の行の形が違う。"""


@dataclass(frozen=True)
class Row:
    """候補の課題 1 つの事実（棚卸しや GitHub から集めたもの）。"""

    issue: int
    prior: bool = False
    waiting: bool = False
    blocked: Optional[str] = None
    needs: tuple[int, ...] = ()
    parent: bool = False


@dataclass(frozen=True)
class ExclusionFacts:
    """ループの記録から求める、選定の事実。"""

    pending: frozenset[int] = frozenset()
    excluded: frozenset[int] = frozenset()
    created: frozenset[int] = frozenset()
    first: tuple[int, ...] = ()
    size: int = 1


@dataclass(frozen=True)
class Selection:
    """選定の結果。"""

    chosen: tuple[int, ...]
    skipped: tuple[tuple[int, str], ...]

    def items(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"issue": n, "chosen": True} for n in self.chosen]
        out += [{"issue": n, "chosen": False, "reason": k, "text": REASONS[k]} for n, k in self.skipped]
        return out


# 外す理由の表（鍵・文・当たる条件）。並びが優先の順で、先頭に選ぶ課題（KEEP。文なし）より前の理由だけが
# その課題を外す。枠（CAPACITY）は行の条件でなく選んだ数で当たるので表の外に置く
RULES: tuple[tuple[str, Optional[str], Callable[[Row, ExclusionFacts], bool]], ...] = (
    (PRIOR, "前のループの判断待ち", lambda row, facts: row.prior),
    (KEEP, None, lambda row, facts: row.issue in facts.first),
    (WAITING, "棚卸しの判断待ち", lambda row, facts: row.waiting),
    (PENDING, "保留した周の課題", lambda row, facts: row.issue in facts.pending),
    (BLOCKED, "未回答・差し戻し・マージ待ち", lambda row, facts: row.blocked is not None),
    (EXCLUDED, "同じループで外した", lambda row, facts: row.issue in facts.excluded),
    (NEEDS, "前提の課題が閉じていない", lambda row, facts: bool(row.needs)),
    (PARENT, "開いている子の課題を持つ親の課題", lambda row, facts: row.parent),
    (CREATED, "周の中で起票した", lambda row, facts: row.issue in facts.created),
)
ORDER = tuple(key for key, _, _ in RULES)
REASONS = {**{key: text for key, text, _ in RULES if text is not None}, CAPACITY: "枠を使い切った"}


def exclusion(row: Row, facts: ExclusionFacts) -> Optional[str]:
    """選定から外す理由の鍵を優先の順で 1 つ。外さないなら None（先頭に選ぶ課題はそこで残す）。"""
    for key, _, hits in RULES:
        if hits(row, facts):
            return None if key == KEEP else key
    return None


def _ordered(rows: Sequence[Row], first: Sequence[int]) -> list[Row]:
    """先頭に選ぶ課題を指定の順で前へ寄せ、残りは候補の順のまま。"""
    by_issue = {r.issue: r for r in rows}
    head = [by_issue[n] for n in first if n in by_issue]
    return head + [r for r in rows if r.issue not in set(first)]


def select(rows: Sequence[Row], facts: ExclusionFacts) -> Selection:
    """候補から周に入れる課題を選ぶ。外す理由のある行は枠を使わない（#2746）。"""
    if facts.size < 1:
        raise CandidateError(f"周の枠は 1 以上: {facts.size}")
    chosen: list[int] = []
    skipped: list[tuple[int, str]] = []
    for row in _ordered(rows, facts.first):
        reason = exclusion(row, facts)
        if reason is None and len(chosen) >= facts.size:
            reason = CAPACITY
        if reason is None:
            chosen.append(row.issue)
        else:
            skipped.append((row.issue, reason))
    return Selection(tuple(chosen), tuple(skipped))


def facts_of(state: rec.LoopState, first: Sequence[int], size: int) -> ExclusionFacts:
    """ループの記録から選定の事実を求める。保留した周の残りの課題・ループで外した課題・最後の周で起票した課題。"""
    held = [lap for lap in state.laps if lap.outcome == rec.HELD]
    pending = frozenset(n for lap in held for n in lap.remaining)
    excluded = frozenset(n for lap in state.laps for n, _ in lap.excluded)
    created = frozenset(state.laps[-1].created) if state.laps else frozenset()
    return ExclusionFacts(pending=pending, excluded=excluded, created=created, first=tuple(first), size=size)


def _ints(value: Any, where: str) -> tuple[int, ...]:
    if not isinstance(value, list) or not all(isinstance(v, int) and not isinstance(v, bool) for v in value):
        raise CandidateError(f"{where} は整数の並び: {value!r}")
    return tuple(value)


def _flag(raw: Mapping[str, Any], key: str) -> bool:
    value = raw[key] if key in raw else False
    if not isinstance(value, bool):
        raise CandidateError(f"候補の {key} は真偽: {value!r}")
    return value


def row_of(raw: Any) -> Row:
    """候補の行（JSON の値）を読む。課題の番号は必須、ほかは省けば外す理由にならない。"""
    if not isinstance(raw, dict) or not isinstance(raw.get("issue"), int):
        raise CandidateError(f"候補の行は issue（整数）を持つ辞書: {raw!r}")
    blocked = raw["blocked"] if "blocked" in raw else None
    if blocked is not None and not isinstance(blocked, str):
        raise CandidateError(f"候補の blocked は文字列か null: {blocked!r}")
    needs = _ints(raw["needs"], "候補の needs") if "needs" in raw else ()
    return Row(raw["issue"], _flag(raw, "prior"), _flag(raw, "waiting"), blocked, needs, _flag(raw, "parent"))


def rows_of(raw: Any) -> list[Row]:
    """候補の一覧（JSON の配列）を読む。同じ課題が 2 回あれば `CandidateError`。"""
    if not isinstance(raw, list):
        raise CandidateError("候補は配列")
    rows = [row_of(r) for r in raw]
    seen: set[int] = set()
    for r in rows:
        if r.issue in seen:
            raise CandidateError(f"候補に課題 #{r.issue} が 2 回ある")
        seen.add(r.issue)
    return rows
