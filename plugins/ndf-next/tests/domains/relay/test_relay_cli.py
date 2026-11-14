"""`ndf relay` の副命令と、待ちを抱えた実行の一覧の読み取り（#2898・#2464・決定 86・決定 92）。"""

from __future__ import annotations

import io
import json
import subprocess

import pytest

from ndf import cli as top
from ndf import identity
from ndf.domains.relay import accounts, cli, marks, pending


def _last(capsys) -> dict:
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_relay_is_a_subcommand_of_ndf():
    assert top.COMMANDS["relay"] == cli.__name__


def test_help_lists_every_subcommand(capsys):
    # --help の一覧と実装が食い違わない（#2898）
    assert cli.main(["--help"]) == 0
    out = capsys.readouterr().out
    assert all(name in out for name in cli.COMMANDS)
    assert set(cli.COMMANDS) == set(cli.HANDLERS)
    assert cli.main(["account", "--help"]) == 0
    out = capsys.readouterr().out
    assert all(name in out for name in cli.ACCOUNT_COMMANDS)
    assert cli.main(["fly"]) != 0


def test_account_use_show_clear(capsys):
    assert cli.main(["account", "use", "work"]) == 0
    assert accounts.pinned(identity.load().state_dir) == "work"
    assert cli.main(["account", "show"]) == 0
    assert _last(capsys)["items"] == [{"account": "work"}]
    assert cli.main(["account", "use", "a b"]) != 0
    assert _last(capsys)["cause"]["kind"] == "invalid"
    assert cli.main(["account", "clear"]) == 0


def test_decide_reads_the_section_state(monkeypatch, capsys):
    data = {
        "next_block": "/goal x",
        "question": False,
        "child_alive": True,
        "child_exit": None,
        "starts_today": 0,
        "max_starts": 3,
        "limit_pick": {"kind": "switch", "account": "main", "reason": "上限"},
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(data)))
    assert cli.main(["decide"]) == 0
    assert _last(capsys)["items"][0]["kind"] == "account"
    monkeypatch.setattr("sys.stdin", io.StringIO('{"unknown": 1}'))
    assert cli.main(["decide"]) != 0


def test_mark_sets_the_relay_mark(capsys):
    assert cli.main(["mark", "s-9"]) == 0
    assert marks.take(identity.load().state_dir, "s-9") is True
    assert cli.main(["mark", "../bad"]) != 0


def test_ls_with_nothing_registered(capsys):
    assert cli.main(["ls"]) == 0
    assert _last(capsys)["items"] == []


def test_env_override_must_be_a_positive_number(monkeypatch, capsys):
    monkeypatch.setenv("NDF_RELAY_MAX_NAMED", "many")
    assert cli.main(["ls"]) != 0
    assert "NDF_RELAY_MAX_NAMED" in _last(capsys)["summary"]


class _Done:
    def __init__(self, stdout: str, code: int = 0, stderr: str = "") -> None:
        self.stdout, self.returncode, self.stderr = stdout, code, stderr


def test_pending_reads_the_plan_command_result(tmp_path):
    ok = json.dumps({"tool": "plan", "status": "ok", "summary": "2 件", "items": [{"run": "a"}, {"run": "b"}]})
    got = pending.read(tmp_path, 5, runner=lambda *a, **k: _Done("log\n" + ok + "\n"))
    assert got == pending.Pending([{"run": "a"}, {"run": "b"}], "")


@pytest.mark.parametrize(
    "runner",
    [
        lambda *a, **k: _Done("", 2, "知らない副命令: plan"),
        lambda *a, **k: _Done(json.dumps({"status": "stopped", "summary": "壊れた"})),
        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("bin/ndf")),
        lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("ndf", 5)),
    ],
)
def test_pending_reports_why_the_list_could_not_be_read(tmp_path, runner):
    got = pending.read(tmp_path, 5, runner=runner)
    assert got.runs is None and got.note
