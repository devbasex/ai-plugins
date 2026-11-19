"""`ndf progress answer` の引数（ndf.progress.run.parser）の今の振る舞いを固定する（I-002 の直す前）。

answer の machine_id・stage・--reason は、command の disable・enable・retry・finish と同じ形で受ける。
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from ndf.contracts import facts, result
from ndf.progress import command, run


def _sub(ap: argparse.ArgumentParser, name: str) -> argparse.ArgumentParser:
    for act in ap._actions:
        if isinstance(act, argparse._SubParsersAction):
            return act.choices[name]
    raise AssertionError("サブコマンドが無い")


def _shape(p: argparse.ArgumentParser, dest: str) -> tuple:
    (act,) = [a for a in p._actions if a.dest == dest]
    return (tuple(act.option_strings), act.required, act.help, act.nargs, act.default)


def test_answer_parses_positionals_and_options():
    a = run.parser().parse_args(["answer", "m1", "design", "--answer", "approved", "--reason", "ok"])
    assert (a.action, a.machine_id, a.stage, a.answer, a.reason, a.notes, a.cwd) == (
        "answer",
        "m1",
        "design",
        "approved",
        "ok",
        None,
        None,
    )


def test_answer_accepts_notes_and_cwd(tmp_path: Path):
    a = run.parser().parse_args(
        ["answer", "m1", "design", "--answer", "request_changes", "--reason", "r", "--notes", "n.md", "--cwd", str(tmp_path)]
    )
    assert (a.notes, a.cwd) == ("n.md", tmp_path)


@pytest.mark.parametrize(
    "argv",
    [
        ["answer", "m1", "design", "--answer", "approved"],  # --reason が無い
        ["answer", "m1", "design", "--reason", "r"],  # --answer が無い
        ["answer", "m1", "--answer", "approved", "--reason", "r"],  # stage が無い
        ["answer", "--answer", "approved", "--reason", "r"],  # machine_id も stage も無い
        ["answer", "m1", "design", "--answer", "no-such-word", "--reason", "r"],  # 表に無い回答の語
    ],
)
def test_answer_rejects_missing_or_unknown(argv):
    with pytest.raises(SystemExit) as e:
        run.parser().parse_args(argv)
    assert e.value.code == 2


def test_answer_choices_are_the_answers_table():
    (act,) = [a for a in _sub(run.parser(), "answer")._actions if a.dest == "answer"]
    assert list(act.choices) == list(facts.ids(result.ANSWERS))


@pytest.mark.parametrize("other", ["disable", "enable", "retry", "finish"])
@pytest.mark.parametrize("dest", ["machine_id", "stage", "reason"])
def test_answer_shares_shape_with_command_stage_subcommands(other, dest):
    """answer の machine_id・stage・--reason は command の工程を指すサブコマンドと同じ形。"""
    ans = _sub(run.parser(), "answer")
    cmd = _sub(command.parser(), other)
    assert _shape(ans, dest) == _shape(cmd, dest)


@pytest.mark.parametrize("other", ["disable", "enable", "retry", "finish"])
def test_answer_positional_order_matches_command(other):
    def positionals(p: argparse.ArgumentParser) -> list[str]:
        return [a.dest for a in p._actions if not a.option_strings]

    assert positionals(_sub(run.parser(), "answer")) == positionals(_sub(command.parser(), other)) == ["machine_id", "stage"]


def test_answer_option_set():
    opts = {a.dest for a in _sub(run.parser(), "answer")._actions if a.option_strings}
    assert opts == {"help", "answer", "reason", "notes", "cwd"}
