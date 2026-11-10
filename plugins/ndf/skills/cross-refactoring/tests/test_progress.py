"""修正の前進・停滞・振動の判定（#2457 決定 4・I6・受け入れ条件 16・17）。"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture(scope="module")
def progress(refactor):
    return importlib.import_module("refactor_lib.progress")


@pytest.mark.parametrize(
    "history, expected",
    [
        ([["a", "b"]], "first"),
        ([["a", "b"], []], "passed"),
        ([["a", "b"], ["a"]], "progress"),  # 件数が減った
        ([["a"], ["c"]], "progress"),  # 別のテストへ移った
        ([["a"], ["c"], ["d"], ["e"], ["f"]], "progress"),  # 前進の間は回数の上限が無い
        ([["a", "b"], ["a", "b"]], "stalled"),  # 同じ集合が 2 回続く
        ([["a"], ["b"], ["a"]], "oscillating"),  # 前に見た集合へ戻る
        ([["b"], ["a"], ["c"], ["a"]], "oscillating"),
    ],
)
def test_compare(progress, history, expected):
    assert progress.classify_progress(history, same=2) == expected


def test_the_order_of_the_ids_does_not_matter(progress):
    assert progress.classify_progress([["a", "b"], ["b", "a"]], same=2) == "stalled"


def test_the_stagnation_count_comes_from_the_declaration(progress):
    """停滞の回数は `limits.json` の `cross-refactoring.fix-progress` から読む（既定 2）。"""
    assert progress.same_sets() == 2
    assert progress.classify_progress([["a"], ["a"]], same=3) == "progress"
    assert progress.classify_progress([["a"], ["a"], ["a"]], same=3) == "stalled"
