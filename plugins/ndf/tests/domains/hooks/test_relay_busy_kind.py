"""バックグラウンドの作業を、待つべき作業と止めてよい監視に分ける（#4049 の受け入れ条件 13）。構文解析は差し替えない。"""

from __future__ import annotations

import pytest

from ndf.domains.hooks.guards import relay_busy

PARAMS = {"wait_commands": ["ndf lanes wait", "ndf plan wait", "ndf ci watch"]}


@pytest.mark.parametrize("kind", ["shell", "monitor"])
@pytest.mark.parametrize(
    "command",
    [
        "ndf lanes wait p.json --after 3",
        "ndf plan wait p1.json p2.json",
        "ndf ci watch --pr 1 --required x --budget 60",
        "timeout 590 ndf plan wait p.json",
        "/usr/local/bin/ndf lanes wait p",
        'bash -c "ndf lanes wait p"',
        "bash -c 'ndf lanes wait p'",
        'timeout 600 bash -c "ndf ci watch --pr 1"',
        "ndf lanes wait p > log 2>&1",
    ],
)
def test_a_command_that_only_waits_is_a_monitor(kind, command):
    assert relay_busy.task_kind(kind, command, PARAMS) == "monitor"


@pytest.mark.parametrize("kind", ["shell", "monitor"])
@pytest.mark.parametrize(
    "command",
    [
        None,
        3,
        "",
        "ndf plan queue p.json",
        "ndf lanes",
        "python train.py | grep loss",
        "while :; do gh api repos/o/r/issues/1/comments; sleep 60; done",
        "until test -f x; do sleep 5; done",
        "for i in 1; do ndf lanes wait p; done",
        "ndf lanes wait 1 && rm x",
        "ndf lanes wait p &",
        "ndf lanes wait p; > output",
        "ndf lanes wait p | tail",
        "ndf lanes wait $(rm x)",
        'bash -c "ndf lanes wait p; rm x"',
        'bash -c "ndf lanes wait $ARGS"',
        'eval "$CMD"',
        "ndf lanes wait 'p",
        # Claude Code が切ったコマンドの残りは見えない
        "ndf lanes wait p… [+120 chars]",
    ],
)
def test_a_command_that_is_not_only_waiting_is_work(kind, command):
    assert relay_busy.task_kind(kind, command, PARAMS) == "work"


@pytest.mark.parametrize("kind", ["subagent", "workflow", "teammate", "cloud session", "MCP task", "new", "", None, ["shell"], {}])
def test_other_kinds_are_work_whatever_the_command_is(kind):
    assert relay_busy.task_kind(kind, "ndf lanes wait p", PARAMS) == "work"


@pytest.mark.parametrize("params", [{}, {"wait_commands": None}, {"wait_commands": [3]}, {"wait_commands": [""]}])
def test_without_the_wait_commands_everything_is_work(params):
    assert relay_busy.task_kind("shell", "ndf lanes wait p", params) == "work"


def test_a_failure_of_the_parser_is_work(monkeypatch):
    def broken(text, accept):
        raise RuntimeError("parser")

    monkeypatch.setattr(relay_busy, "only_commands", broken)
    assert relay_busy.task_kind("shell", "ndf lanes wait p", PARAMS) == "work"


def _task(kind: str, command: str) -> dict:
    return {"type": kind, "command": command}


@pytest.mark.parametrize(
    "tasks, expected",
    [
        ([], (0, 0)),
        ([{"type": "subagent"}, _task("shell", "ndf plan wait p.json")], (1, 1)),
        ([_task("shell", "ndf lanes wait p")], (0, 1)),
        ([_task("monitor", "ndf lanes wait p"), _task("monitor", "ndf lanes wait p")], (0, 2)),
        ([{"type": "monitor"}], (1, 0)),
        (["ndf lanes wait p", None, _task("shell", "ndf lanes wait p")], (2, 1)),
    ],
)
def test_work_count_counts_work_and_monitors(tasks, expected):
    assert relay_busy.work_count(tasks, PARAMS) == expected
