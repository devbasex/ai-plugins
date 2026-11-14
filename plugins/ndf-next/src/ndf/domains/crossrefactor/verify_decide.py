"""検証の判定: 失敗を持ち込んだ項目にだけ帰す（#3339）と、修正の前進の判定。

- 失敗の鍵は `<suite>:<ファイル>`（suite の範囲の検査かテストを、そのファイルに当てて落ちたもの）。行数の上限のような
  共有の検査（ファイル全体に掛かる検査）も同じ鍵で数える
- 項目の検証は、項目の起点（テストの追加の前の head）と今の head で同じ鍵を比べ、起点で落ちていなかったものだけを
  その項目の失敗（`own`）とする。起点から落ちていたもの（`inherited`）は前の項目か元のコードのもので、この項目の
  修正の対象にしない。後の項目が前の項目の持ち込んだ違反を自分の失敗として拾って停滞しない
- 修正の前進は宣言 `cross-refactoring.fix-progress` で決める。同じ失敗の集合が `same_cause` 回続けば停滞、前に見た
  どれかの集合へ戻れば振動で、どちらも修正をやめて項目を戻す
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from ndf.contracts import limits

PROGRESS_POLICY = "cross-refactoring.fix-progress"


class Kind(StrEnum):
    """修正の前進の種類。"""

    PASSED = "passed"
    RETRY = "retry"
    STALLED = "stalled"
    OSCILLATING = "oscillating"


def failure_key(suite: str, path: str) -> str:
    """失敗の鍵（`<suite>:<ファイル>`）。"""
    return f"{suite}:{path}"


@dataclass(frozen=True)
class Attributed:
    """今の head で落ちた鍵を、この項目のもの（`own`）と起点から在ったもの（`inherited`）へ分けたもの。"""

    own: frozenset[str]
    inherited: frozenset[str]

    @property
    def passed(self) -> bool:
        return not self.own


def attribute(at_start: Iterable[str], now: Iterable[str]) -> Attributed:
    """起点で落ちた鍵 `at_start` と今落ちた鍵 `now` から、項目の失敗を決める。"""
    before = frozenset(at_start)
    after = frozenset(now)
    return Attributed(after - before, after & before)


@dataclass(frozen=True)
class Progress:
    """修正の 1 回の後の続け方。`kind` は `passed`・`retry`・`stalled`・`oscillating`。"""

    kind: Kind
    detail: str

    @property
    def gave_up(self) -> bool:
        return self.kind in (Kind.STALLED, Kind.OSCILLATING)


def fix_progress(history: Sequence[frozenset[str]]) -> Progress:
    """検証ごとの項目の失敗（`own`）の並び（古い順）から続け方を決める。並びが空なら `ValueError`。"""
    if not history:
        raise ValueError("検証の記録が無い")
    last = history[-1]
    if not last:
        return Progress(Kind.PASSED, "項目の失敗が無い")
    same = int(limits.value(PROGRESS_POLICY, "same_cause"))
    if len(history) >= same and all(h == last for h in history[-same:]):
        return Progress(Kind.STALLED, f"同じ失敗が {same} 回続いた: {', '.join(sorted(last))}")
    if any(h == last for h in history[:-1]):
        return Progress(Kind.OSCILLATING, f"前に見た失敗の集合へ戻った: {', '.join(sorted(last))}")
    return Progress(Kind.RETRY, f"項目の失敗が残る: {', '.join(sorted(last))}")
