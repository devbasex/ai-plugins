"""PR の CI のチェックを、承認資料に載せる件数へまとめる（純粋な関数）。

結論の区分は事実の表 `ci-outcomes` の `outcome` から引く。実行されなかったチェック（結論 `skipped`）は通過と
数えるが、分母に入れず内訳へ出す（`CI 23/23（スキップ 1）`。#3169）。失敗と読める表記を出さない。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import NamedTuple

from ndf.contracts import facts

# 結論の区分の表（結論の id → 区分 outcome）
OUTCOMES = "ci-outcomes"


class Tally(NamedTuple):
    """1 つの PR の CI の件数。`failed` と `pending` はチェックの名前。"""

    passed: int
    skipped: int
    failed: tuple[str, ...]
    pending: tuple[str, ...]

    @property
    def ran(self) -> int:
        """実行されたチェックの数（分母）。実行されなかったチェックを含めない。"""
        return self.passed + len(self.failed) + len(self.pending)

    @property
    def green(self) -> bool:
        """失敗も待ちも無いか。"""
        return not self.failed and not self.pending

    def label(self) -> str:
        """承認資料の 1 語（`CI 23/23`・`CI 23/23（スキップ 1）`・`CI 未実行`）。"""
        if self.ran == 0 and self.skipped == 0:
            return "CI 未実行"
        text = f"CI {self.passed}/{self.ran}"
        return f"{text}（スキップ {self.skipped}）" if self.skipped else text


def _outcomes() -> dict[str, str]:
    """事実の表の結論の id → 区分。"""
    return {r["id"]: r["outcome"] for r in facts.rows(OUTCOMES)}


def _kind(conclusion: str, known: Mapping[str, str]) -> str:
    if not conclusion:
        return "pending"
    return known[conclusion] if conclusion in known else "failed"


def outcome_of(conclusion: str) -> str:
    """結論（小文字。空はまだ終わっていない）の区分（`passed`・`failed`・`cancelled`・`pending`）。知らない結論は失敗。"""
    return _kind(conclusion, _outcomes())


def tally(checks: Iterable[tuple[str, str]]) -> Tally:
    """(チェックの名前, 結論) の並びを数える。取り消し（`cancelled`）は失敗と数える（通っていない）。"""
    passed = skipped = 0
    failed: list[str] = []
    pending: list[str] = []
    known = _outcomes()
    for name, conclusion in checks:
        kind = _kind(conclusion, known)
        if conclusion == "skipped":  # 実行されなかった（分母に入れず内訳へ。#3169）
            skipped += 1
        elif kind == "passed":
            passed += 1
        elif kind == "pending":
            pending.append(name)
        else:
            failed.append(name)
    return Tally(passed, skipped, tuple(sorted(failed)), tuple(sorted(pending)))
