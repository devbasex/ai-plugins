"""`ndf progress` の引数の解析器（command.parser）の振る舞いを固定する。工程を指すサブコマンドの形が対象。"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from ndf.progress import command

STAGE_ACTIONS = ("disable", "enable", "retry", "finish")


def _sub(name: str) -> argparse.ArgumentParser:
    ap = command.parser()
    (sub,) = [a for a in ap._actions if isinstance(a, argparse._SubParsersAction)]
    return sub.choices[name]


def test_subcommands_keep_their_order():
    ap = command.parser()
    (sub,) = [a for a in ap._actions if isinstance(a, argparse._SubParsersAction)]
    assert list(sub.choices) == [
        "new",
        "show",
        "disable",
        "enable",
        "retry",
        "finish",
        "check",
        "cancel",
        "restore",
        "docs-only",
        "gathered",
        "reread",
        "bump-assemble",
        "bump-present",
        "bump-record",
    ]


@pytest.mark.parametrize("name", STAGE_ACTIONS)
def test_stage_actions_take_machine_stage_and_reason(name):
    extra = ["--output", "o.json"] if name == "finish" else []
    a = command.parser().parse_args([name, "m1", "計画", "--reason", "r", *extra])
    assert (a.action, a.machine_id, a.stage, a.reason, a.cwd) == (name, "m1", "計画", "r", None)


@pytest.mark.parametrize("name", STAGE_ACTIONS)
def test_stage_actions_require_reason(name, capsys):
    extra = ["--output", "o.json"] if name == "finish" else []
    with pytest.raises(SystemExit) as e:
        command.parser().parse_args([name, "m1", "計画", *extra])
    assert e.value.code == 2 and "--reason" in capsys.readouterr().err


def test_finish_requires_output(capsys):
    with pytest.raises(SystemExit) as e:
        command.parser().parse_args(["finish", "m1", "計画", "--reason", "r"])
    assert e.value.code == 2 and "--output" in capsys.readouterr().err


def test_finish_keeps_output_and_cwd():
    a = command.parser().parse_args(["finish", "m1", "計画", "--reason", "r", "--output", "o.json", "--cwd", "/tmp/x"])
    assert (a.output, a.cwd) == ("o.json", Path("/tmp/x"))


@pytest.mark.parametrize("name", ("disable", "enable", "retry"))
def test_only_finish_takes_output(name, capsys):
    with pytest.raises(SystemExit) as e:
        command.parser().parse_args([name, "m1", "計画", "--reason", "r", "--output", "o.json"])
    assert e.value.code == 2 and "--output" in capsys.readouterr().err


@pytest.mark.parametrize("name", STAGE_ACTIONS)
def test_stage_actions_keep_their_options(name):
    opts = {o for act in _sub(name)._actions for o in act.option_strings}
    want = {"-h", "--help", "--reason", "--cwd"} | ({"--output"} if name == "finish" else set())
    assert opts == want


@pytest.mark.parametrize("name", STAGE_ACTIONS)
def test_stage_actions_keep_their_help(name):
    ap = command.parser()
    (sub,) = [a for a in ap._actions if isinstance(a, argparse._SubParsersAction)]
    helps = {c.dest: c.help for c in sub._choices_actions}
    assert helps[name] == f"工程に {name} を書く"


def test_finish_output_help_is_kept():
    (out,) = [act for act in _sub("finish")._actions if "--output" in act.option_strings]
    assert out.required and out.help == "工程の出力の規約に合う JSON のファイル"


def test_check_takes_machine_stage_output_and_cwd_without_reason():
    a = command.parser().parse_args(["check", "m1", "計画", "--output", "o.json", "--cwd", "/tmp/x"])
    assert (a.action, a.machine_id, a.stage, a.output, a.cwd) == ("check", "m1", "計画", "o.json", Path("/tmp/x"))
    opts = {o for act in _sub("check")._actions for o in act.option_strings}
    assert opts == {"-h", "--help", "--output", "--cwd"}


def test_check_requires_output(capsys):
    with pytest.raises(SystemExit) as e:
        command.parser().parse_args(["check", "m1", "計画"])
    assert e.value.code == 2 and "--output" in capsys.readouterr().err
