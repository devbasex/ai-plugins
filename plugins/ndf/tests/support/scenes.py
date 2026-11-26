"""形の違う場面を、1 つのテストの関数の表（`pytest.mark.parametrize`）に並べる。

場面は `test_` で始まらない関数で、引数の名前は fixture か、表の行が渡す値を指す。`scene` が表の 1 行を作り、
`play` が表の行の値の外の引数を fixture として `request` から引いて場面を呼ぶ。同じ前提を共有する場面を 1 つのテストに
まとめ、確かめる入力と期待は場面ごとに残す（行の `id` が何を確かめるかを表す）。
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

import pytest

# `pytest.mark.parametrize` の引数の名前
ARGS = ("scene", "values")


def scene(fn: Callable[..., Any], id: str, marks: Any = (), **values: Any) -> Any:
    """場面 `fn` を、表の値 `values` で呼ぶ 1 行。"""
    return pytest.param(fn, values, id=id, marks=marks)


def label(name: str, index: int, values: tuple[Any, ...]) -> str:
    """場面の名前に、表の値（短い文字列・数・真偽・None だけのとき）か、値の並びの番号を添えた `id`。"""
    simple = all(v is None or isinstance(v, (str, int, bool)) and len(str(v)) <= 24 for v in values)
    return f"{name}-{'-'.join(str(v) for v in values) if simple else index}"


def play(request: pytest.FixtureRequest, fn: Callable[..., Any], values: dict[str, Any]) -> None:
    """表の値の外の引数を fixture として引き、場面を呼ぶ。"""
    names = [n for n in inspect.signature(fn).parameters if n not in values]
    fn(**{n: request.getfixturevalue(n) for n in names}, **values)
