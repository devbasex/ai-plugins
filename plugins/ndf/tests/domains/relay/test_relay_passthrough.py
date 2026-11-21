"""`ndf relay` のパススルー（`_passthrough`）: 下限の確かめ → 本物の claude を exec。起動できなければ理由を出して 127。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ndf.adapters import cli_floor
from ndf.adapters.claude import CLAUDE
from ndf.domains.relay import cli as relay_cli


@pytest.fixture
def seen(monkeypatch):
    """`require` と `execvp` を差し替え、呼ばれた順に記録する。"""
    log: list[tuple] = []
    monkeypatch.setattr(relay_cli.cli_floor, "require", lambda name, argv0, *a, **k: log.append(("require", name, argv0)))
    monkeypatch.setattr(relay_cli.os, "execvp", lambda file, argv: log.append(("exec", file, list(argv))))
    return log


def _parsed(notice: bool = False, passthrough: str = "") -> SimpleNamespace:
    return SimpleNamespace(notice=notice, passthrough=passthrough)


def test_compares_the_floor_then_execs_claude_with_the_same_args(seen, capsys):
    relay_cli._passthrough(["-p", "x"], _parsed())
    assert seen == [("require", "claude", CLAUDE), ("exec", CLAUDE, [CLAUDE, "-p", "x"])]
    assert capsys.readouterr().err == ""


def test_notice_names_the_reason_before_exec(seen, capsys):
    relay_cli._passthrough([], _parsed(notice=True, passthrough="--print"))
    err = capsys.readouterr().err
    assert err == "ndf-relay: ラッパーを始めない（--print）。claude をそのまま起動する\n"
    assert seen[-1] == ("exec", CLAUDE, [CLAUDE])


def test_an_old_claude_returns_127_with_the_reason_and_does_not_exec(monkeypatch, capsys):
    too_old = cli_floor.TooOld("claude", "/x/claude", "0.0.1", "9.9.9")

    def refuse(*a, **k):
        raise too_old

    monkeypatch.setattr(relay_cli.cli_floor, "require", refuse)
    monkeypatch.setattr(relay_cli.os, "execvp", lambda *a: pytest.fail("古い claude を exec した"))
    assert relay_cli._passthrough(["-p", "x"], _parsed()) == 127
    assert capsys.readouterr().err == f"ndf-relay: claude を起動できない（{too_old}）\n"


def test_an_exec_failure_returns_127_with_the_reason(monkeypatch, capsys):
    monkeypatch.setattr(relay_cli.cli_floor, "require", lambda *a, **k: None)

    def fail(file, argv):
        raise FileNotFoundError(2, "No such file or directory", file)

    monkeypatch.setattr(relay_cli.os, "execvp", fail)
    assert relay_cli._passthrough(["-p", "x"], _parsed()) == 127
    err = capsys.readouterr().err
    assert err.startswith("ndf-relay: claude を起動できない（") and err.endswith("）\n")
    assert "No such file or directory" in err


def test_other_errors_from_the_floor_are_not_swallowed(monkeypatch):
    def broken(*a, **k):
        raise ValueError("読めない")

    monkeypatch.setattr(relay_cli.cli_floor, "require", broken)
    monkeypatch.setattr(relay_cli.os, "execvp", lambda *a: pytest.fail("確かめが壊れたのに exec した"))
    with pytest.raises(ValueError):
        relay_cli._passthrough([], _parsed())


def test_other_errors_from_exec_are_not_swallowed(monkeypatch):
    monkeypatch.setattr(relay_cli.cli_floor, "require", lambda *a, **k: None)

    def broken(file, argv):
        raise ValueError("壊れた")

    monkeypatch.setattr(relay_cli.os, "execvp", broken)
    with pytest.raises(ValueError):
        relay_cli._passthrough([], _parsed())
