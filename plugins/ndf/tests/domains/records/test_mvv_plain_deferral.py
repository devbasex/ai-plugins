"""通常の指摘の、利用者の判断に委ねた見送りの判定（`holding_keys`・`plain_fix_clears`・`defer_holds`）の振る舞いを固定する。"""

from __future__ import annotations

from typing import Any

import pytest

from ndf.domains.records import mvv_decide as md

AT = "2026-11-24T00:00:0{}+00:00"


def _r(outcome: str, n: int = 1, **extra: Any) -> dict[str, Any]:
    """通常の指摘（項目の無い）の記録の行（`n` 秒の時刻）。"""
    return {"outcome": outcome, "at": AT.format(n), **extra}


DEFER_HOLDS = {
    "needs_user": ("needs_user", True),
    "none": (None, True),
    "unknown": ("later", True),
    "filed": ("filed", False),
    "not_applicable": ("not_applicable", False),
}


@pytest.mark.parametrize(("defer", "expected"), DEFER_HOLDS.values(), ids=DEFER_HOLDS.keys())
def test_defer_holds_only_for_the_unsettled_kinds(defer: Any, expected: bool) -> None:
    assert md.defer_holds(defer) is expected


# 記録の並び → 保留の候補か
HOLDING = {
    "needs-user-last": ([_r("deferred", defer="needs_user")], True),
    "no-defer-last": ([_r("deferred")], True),
    "unknown-defer-last": ([_r("deferred", defer="later")], True),
    "filed-last": ([_r("deferred", defer="filed")], False),
    "not-applicable-last": ([_r("deferred", defer="not_applicable")], False),
    "with-item": ([_r("deferred", defer="needs_user", item="Value 1")], False),
    "fixed-last": ([_r("deferred"), _r("fixed", 2)], False),
    "unresolved-last": ([_r("deferred"), _r("unresolved", 2)], False),
    "redline-last": ([_r("redline")], False),
    "withheld-fixed-skipped": ([_r("deferred"), _r("fixed", 2, withheld=True)], True),
    "withheld-deferred-not-skipped": ([_r("deferred", withheld=True)], True),
    "only-withheld-fixed": ([_r("fixed", 1, withheld=True)], False),
    "empty": ([], False),
    # 伏せた直しの手前の非伏せの直しは外さない
    "fixed-then-withheld-fixed": ([_r("deferred"), _r("fixed", 2), _r("fixed", 3, withheld=True)], False),
    # 前のレッドラインは候補の判定に効かない（解けるかは plain_fix_clears が決める）
    "redline-then-deferred": ([_r("redline"), _r("deferred", 2)], True),
}


@pytest.mark.parametrize(("seq", "expected"), HOLDING.values(), ids=HOLDING.keys())
def test_holding_keys_pick_keys_whose_last_record_is_a_plain_user_deferral(seq: list[dict[str, Any]], expected: bool) -> None:
    assert md.holding_keys({"k": seq}) == ({"k"} if expected else set())


def test_holding_keys_judge_each_key_separately() -> None:
    seqs = {"a": [_r("deferred")], "b": [_r("deferred", defer="filed")], "c": [_r("deferred"), _r("fixed", 2)], "d": []}
    assert md.holding_keys(seqs) == {"a"}


# （記録の並び, 直した時刻の秒, 解いてよいか）
CLEARS = {
    "needs-user-then-later-fix": ([_r("deferred", 1, defer="needs_user")], 2, True),
    "no-defer-then-later-fix": ([_r("deferred", 1)], 2, True),
    "unknown-defer": ([_r("deferred", 1, defer="later")], 2, True),
    "filed": ([_r("deferred", 1, defer="filed")], 2, False),
    "not-applicable": ([_r("deferred", 1, defer="not_applicable")], 2, False),
    "with-item": ([_r("deferred", 1, item="Value 1")], 2, False),
    "fix-before-deferral": ([_r("deferred", 3)], 2, False),
    "fix-at-same-time": ([_r("deferred", 2)], 2, False),
    "last-is-fixed": ([_r("deferred", 1), _r("fixed", 2)], 3, False),
    "last-is-unresolved": ([_r("deferred", 1), _r("unresolved", 2)], 3, False),
    "empty": ([], 2, False),
    "only-withheld-fixed": ([_r("fixed", 1, withheld=True)], 2, False),
    "withheld-fixed-skipped": ([_r("deferred", 1), _r("fixed", 2, withheld=True)], 3, True),
    "open-redline-before": ([_r("redline", 1), _r("deferred", 2)], 3, False),
    "redline-fixed-before": ([_r("redline", 1), _r("fixed", 2), _r("deferred", 3)], 4, True),
    "withheld-deferred": ([_r("deferred", 1, withheld=True)], 2, True),
}


@pytest.mark.parametrize(("seq", "fixed", "expected"), CLEARS.values(), ids=CLEARS.keys())
def test_plain_fix_clears_only_a_later_fix_of_a_plain_user_deferral(seq: list[dict[str, Any]], fixed: int, expected: bool) -> None:
    assert md.plain_fix_clears(seq, AT.format(fixed)) is expected


@pytest.mark.parametrize(("seq", "fixed", "expected"), CLEARS.values(), ids=CLEARS.keys())
def test_plain_fix_clears_implies_the_key_is_a_holding_candidate(seq: list[dict[str, Any]], fixed: int, expected: bool) -> None:
    if md.plain_fix_clears(seq, AT.format(fixed)):
        assert md.holding_keys({"k": seq}) == {"k"}
