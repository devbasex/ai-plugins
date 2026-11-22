"""修正担当が前のラウンドの会話を続けるかの判断 `resume_decide`（#4013。純粋な関数）。"""

from __future__ import annotations

from typing import Optional

import pytest

from ndf.domains.crossreview import resume_decide as rd

MARGIN = 50000


def _prev(
    context: Optional[int] = 244412,
    window: Optional[int] = 1000000,
    finished: bool = True,
    taken: bool = True,
    compacted: bool = False,
) -> rd.Previous:
    return rd.Previous("s1", finished, taken, compacted, context, window)


def test_continues_when_the_last_fixer_finished_and_the_context_fits() -> None:
    assert rd.choose(_prev(), 244412, MARGIN) == rd.Choice(True, None)


def test_a_context_that_does_not_fit_the_window_starts_a_new_conversation() -> None:
    assert rd.choose(_prev(131115, 200000), 131115, MARGIN) == rd.Choice(False, rd.LARGE)


def test_the_sum_equal_to_the_window_continues_and_one_more_does_not() -> None:
    assert rd.choose(_prev(400000), 550000, MARGIN) == rd.Choice(True, None)
    assert rd.choose(_prev(400001), 550000, MARGIN) == rd.Choice(False, rd.LARGE)
    assert rd.choose(_prev(0), 0, MARGIN) == rd.Choice(True, None)


@pytest.mark.parametrize(
    ("previous", "reason"),
    [
        (None, rd.FIRST),
        (_prev(finished=False), rd.FAILED),
        (_prev(taken=False), rd.NOT_TAKEN),
        (_prev(compacted=True), rd.COMPACTED),
        (_prev(context=None), rd.UNREAD),
        (_prev(window=None), rd.UNREAD),
        # 上の行が先に当たる
        (_prev(finished=False, taken=False), rd.FAILED),
    ],
)
def test_each_condition_starts_a_new_conversation_with_its_reason(previous: Optional[rd.Previous], reason: str) -> None:
    assert rd.choose(previous, 0, MARGIN) == rd.Choice(False, reason)


def test_growth_of_a_new_conversation_is_its_context() -> None:
    assert rd.grown(244412, None, False, 1000000) == 244412


def test_growth_of_a_continued_conversation_is_the_difference() -> None:
    assert rd.grown(300000, 131115, False, 1000000) == 168885
    assert rd.grown(100000, 131115, False, 1000000) == 0


def test_growth_after_an_automatic_summary_is_the_whole_window() -> None:
    assert rd.grown(1000, None, True, 1000000) == 1000000
    assert rd.grown(1000, None, True, None) == 0


def test_growth_without_a_readable_context_is_zero() -> None:
    assert rd.grown(None, None, False, 1000000) == 0
    assert rd.grown(None, 131115, False, 1000000) == 0
