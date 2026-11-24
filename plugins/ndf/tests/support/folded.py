"""1 つのファイルの場合の関数を、1 つのテストの関数の parametrize へ束ねる（#3959）。

場合の関数は `test_` を外した名前で書き、`pytest.mark.parametrize` のマークはそのまま付けてよい。`cases` がマークを展開して
（関数, 引数）の組にし、`run` が残りの引数をフィクスチャとして解いて呼ぶ。テストの ID は関数の名前と引数の ID を並べる。
"""

from __future__ import annotations

import inspect
import itertools
from typing import Any, Callable

import pytest


PARAM = type(pytest.param())


def _id(value: Any, name: str, index: int) -> str:
    if isinstance(value, (str, int, float, bool)) or value is None:
        text = str(value)
        if len(text) <= 40 and text.isprintable():
            return text
    return f"{name}{index}"


def _expand(mark: Any) -> list[tuple[dict[str, Any], str, tuple]]:
    """1 つの parametrize のマークを、（引数, ID, 付けるマーク）の並びにする。"""
    names = mark.args[0]
    names = [n.strip() for n in names.split(",")] if isinstance(names, str) else list(names)
    ids = mark.kwargs["ids"] if "ids" in mark.kwargs else None
    ids = list(ids) if ids is not None and not callable(ids) else ids
    out = []
    for i, raw in enumerate(mark.args[1]):
        ps = raw if isinstance(raw, PARAM) else pytest.param(*(raw if len(names) > 1 else (raw,)))
        values = ps.values
        kwargs = dict(zip(names, values))
        if ps.id is not None:
            label = ps.id
        elif isinstance(ids, (list, tuple)):
            label = str(ids[i])
        elif callable(ids):
            got = [ids(v) for v in values]
            label = "-".join(str(g) if g is not None else _id(v, n, i) for g, v, n in zip(got, values, names))
        else:
            label = "-".join(_id(v, n, i) for v, n in zip(values, names))
        out.append((kwargs, label, tuple(ps.marks)))
    return out


def cases(*fns: Callable[..., None]) -> list[Any]:
    """場合の関数を `pytest.param((関数, 引数), id=...)` の並びにする。parametrize のマークは直積に展開する。"""
    params = []
    for fn in fns:
        marks = [m for m in getattr(fn, "pytestmark", []) if m.name == "parametrize"]
        others = tuple(m for m in getattr(fn, "pytestmark", []) if m.name != "parametrize")
        name = fn.__name__.lstrip("_")
        for combo in itertools.product(*[_expand(m) for m in reversed(marks)]) if marks else [()]:
            kwargs: dict[str, Any] = {}
            labels, extra = [], []
            for got, label, more in combo:
                kwargs.update(got)
                labels.append(label)
                extra.extend(more)
            label = name + ("[" + "-".join(labels) + "]" if labels else "")
            params.append(pytest.param((fn, kwargs), id=label, marks=(*others, *extra)))
    return params


def run(case: tuple[Callable[..., None], dict[str, Any]], request: pytest.FixtureRequest) -> None:
    """場合の関数を、束ねた引数と、残りの引数の名前のフィクスチャで呼ぶ。"""
    fn, kwargs = case
    rest = {n: request.getfixturevalue(n) for n in inspect.signature(fn).parameters if n not in kwargs}
    fn(**kwargs, **rest)
