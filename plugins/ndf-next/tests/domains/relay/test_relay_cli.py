"""`ndf relay` の副命令と、待ちを抱えた実行の一覧の読み取り（#2898・#2464・決定 86・決定 92）。"""

from __future__ import annotations

import fcntl
import io
import json
import os
import subprocess

import pytest

from ndf import cli as top
from ndf import identity
from ndf.contracts import causes
from ndf.domains.relay import accounts, cli, marks, pending, registry


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


def test_account_use_show_clear(capsys, last_result):
    assert cli.main(["account", "use", "work"]) == 0
    assert accounts.pinned(identity.load().state_dir) == "work"
    assert cli.main(["account", "show"]) == 0
    assert last_result(capsys.readouterr().out)["items"] == [{"account": "work"}]
    assert cli.main(["account", "use", "a b"]) != 0
    assert last_result(capsys.readouterr().out)["cause"]["kind"] == "invalid"
    assert cli.main(["account", "clear"]) == 0


def test_decide_reads_the_section_state(monkeypatch, capsys, last_result):
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
    assert last_result(capsys.readouterr().out)["items"][0]["kind"] == "account"
    monkeypatch.setattr("sys.stdin", io.StringIO('{"unknown": 1}'))
    assert cli.main(["decide"]) != 0


def test_mark_sets_the_relay_mark(capsys):
    assert cli.main(["mark", "s-9"]) == 0
    assert marks.take(identity.load().state_dir, "s-9") is True
    assert cli.main(["mark", "../bad"]) != 0


def test_ls_with_nothing_registered(capsys, last_result):
    assert cli.main(["ls"]) == 0
    assert last_result(capsys.readouterr().out)["items"] == []


def test_env_override_must_be_a_positive_number(monkeypatch, capsys, last_result):
    monkeypatch.setenv("NDF_RELAY_MAX_NAMED", "many")
    assert cli.main(["ls"]) != 0
    assert "NDF_RELAY_MAX_NAMED" in last_result(capsys.readouterr().out)["summary"]


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


# 現状固定（I-018）: install・uninstall・status の結果 JSON（要約・項目・警告）と、--replace-old の断り
@pytest.fixture
def places(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("XDG_BIN_HOME", str(tmp_path / "bin"))
    monkeypatch.setenv("NDF_STATE_HOME", str(tmp_path / "old"))
    return tmp_path / "claude" / "ndf" / "relay.py", tmp_path / "bin" / "ndf-relay"


def _expect(copy, entry, states, warnings=None) -> dict:
    out = {
        "tool": "relay",
        "status": "ok",
        "summary": f"複製: {states[0]}（{copy}）・エントリポイント: {states[1]}（{entry}）",
        "items": [{"path": str(copy), "state": states[0]}, {"path": str(entry), "state": states[1]}],
        "metrics": {},
    }
    if warnings:
        out["warnings"] = warnings
    return out


def test_install_status_uninstall_report_both_places(places, monkeypatch, capsys, last_result):
    copy, entry = places
    base = os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", base)
    assert cli.main(["status"]) == 0
    hint = f'{entry.parent} が PATH に無い。export PATH="{entry.parent}:$PATH" を足す'
    assert last_result(capsys.readouterr().out) == _expect(copy, entry, ("absent", "absent"), [hint])
    monkeypatch.setenv("PATH", f"{entry.parent}{os.pathsep}{base}")
    assert cli.main(["install"]) == 0
    assert last_result(capsys.readouterr().out) == _expect(copy, entry, ("ours", "ours"))
    assert cli.main(["status"]) == 0
    assert last_result(capsys.readouterr().out) == _expect(copy, entry, ("ours", "ours"))
    assert cli.main(["uninstall"]) == 0
    assert last_result(capsys.readouterr().out) == _expect(copy, entry, ("absent", "absent"))
    assert not copy.exists() and not entry.exists()


def test_install_keeps_an_old_copy_and_replace_old_refuses_while_an_old_wrapper_runs(places, tmp_path, monkeypatch, capsys, last_result):
    copy, entry = places
    monkeypatch.setenv("PATH", str(entry.parent))
    copy.parent.mkdir(parents=True)
    copy.write_text("old\n", encoding="utf-8")
    assert cli.main(["install"]) == 0
    warn = "旧い NDF の複製がある。旧いラッパーを止めてから ndf relay install --replace-old で置き換える"
    assert last_result(capsys.readouterr().out) == _expect(copy, entry, ("foreign", "ours"), [warn])
    old = tmp_path / "old" / "relay" / "20261113T1"
    old.mkdir(parents=True)
    fd = os.open(old / registry.LOCK_FILE, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        assert cli.main(["install", "--replace-old"]) == causes.exit_code("precondition")
        why = "旧い NDF のラッパーが動いている: 20261113T1。止めてから置き換える"
        assert last_result(capsys.readouterr().out) == {
            "tool": "relay",
            "status": "stopped",
            "summary": why,
            "items": [],
            "metrics": {},
            "cause": {"kind": "precondition", "detail": why},
        }
        assert copy.read_text(encoding="utf-8") == "old\n"
        # --replace-old を付けなければ、旧いラッパーが動いていても断らずに旧い複製を残す
        assert cli.main(["install"]) == 0
        assert last_result(capsys.readouterr().out)["items"][0]["state"] == "foreign"
    finally:
        os.close(fd)
    assert cli.main(["install", "--replace-old"]) == 0
    assert last_result(capsys.readouterr().out) == _expect(copy, entry, ("ours", "ours"))
