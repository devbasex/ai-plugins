"""判断のループを DBOS を起こさずに確かめる。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from ndf.core import loop
from ndf.core.loop import DecideLoopError, Next


def _load_counting() -> ModuleType:
    """隣の _counting.py を読む（importlib のモードでは隣のモジュールを名前で import できない）。"""
    spec = importlib.util.spec_from_file_location("_counting", Path(__file__).with_name("_counting.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


decide = _load_counting().decide


def test_run_alternates_decide_and_perform_until_finish():
    performed: list[int] = []

    def perform(act: int) -> int:
        performed.append(act)
        return act * 10

    assert loop.run(decide, perform, []) == [10, 20, 30]
    assert performed == [1, 2, 3]


def test_first_decide_gets_no_result_and_finish_skips_perform():
    calls: list[object] = []

    def decide(state: str, result: object) -> Next[str, str]:
        calls.append(result)
        return Next.finish(state + "!")

    def perform(act: str) -> str:
        raise AssertionError("終わりの手の後に手を実行した")

    assert loop.run(decide, perform, "s") == "s!"
    assert calls == [None]


def test_next_constructors():
    assert Next.step("a", 1) == Next(act="a", state=1, finished=False)
    assert Next.finish(2) == Next(act=None, state=2, finished=True)


@pytest.mark.parametrize("bad", [None, "next", {"act": 1}])
def test_decide_returning_non_next_raises(bad):
    with pytest.raises(DecideLoopError):
        loop.run(lambda state, result: bad, lambda act: act, 0)


def test_unfinished_next_without_act_raises():
    with pytest.raises(DecideLoopError):
        loop.run(lambda state, result: Next(act=None, state=state, finished=False), lambda act: act, 0)


def test_perform_exception_propagates_unwrapped():
    class Boom(Exception):
        pass

    def perform(act: int) -> int:
        raise Boom(act)

    with pytest.raises(Boom):
        loop.run(decide, perform, [])
