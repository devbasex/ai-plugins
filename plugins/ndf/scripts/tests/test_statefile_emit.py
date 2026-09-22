"""statefile.emit が現在出力する bash 向けの値を固定する。"""
from __future__ import annotations

import importlib.util
import pathlib
import shlex

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "lib" / "statefile.py"


@pytest.fixture()
def mod():
    spec = importlib.util.spec_from_file_location("ndf_lib_statefile", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_emit_converts_booleans_to_shell_numbers(mod, capsys) -> None:
    mod.emit(FLAG=True, OFF=False)

    assert capsys.readouterr().out.splitlines() == ["FLAG=1", "OFF=0"]


def test_emit_converts_none_to_an_empty_shell_string(mod, capsys) -> None:
    mod.emit(EMPTY=None)

    assert capsys.readouterr().out == "EMPTY=''\n"


@pytest.mark.parametrize("value", ["/x y/z", 'a "b"'])
def test_emit_preserves_a_quoted_string_as_one_word(mod, capsys, value) -> None:
    mod.emit(VALUE=value)

    assert shlex.split(capsys.readouterr().out) == [f"VALUE={value}"]


def test_emit_joins_a_list_into_one_space_separated_word(mod, capsys) -> None:
    # NOTE: 現状固定。要素内の空白と要素間の空白は区別されず、1 語へ連結される。
    mod.emit(VALUES=["a b", "c"])

    assert shlex.split(capsys.readouterr().out) == ["VALUES=a b c"]


def test_register_after_save_calls_the_same_hook_only_once(mod, tmp_path) -> None:
    """現状固定。同じ差し込み口を 2 度登録しても保存後に 1 度だけ呼ぶ。"""
    calls = []

    def hook(path, state):
        calls.append((path, state))

    mod.register_after_save(hook)
    mod.register_after_save(hook)
    try:
        path = tmp_path / "state.json"
        state = {"round": 2}
        mod.save(path, state)
    finally:
        mod.unregister_after_save(hook)

    assert calls == [(path, state)]
