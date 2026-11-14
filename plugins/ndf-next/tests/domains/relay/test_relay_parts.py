"""中継の部品: ブロックの読み・シグナルファイル・子の環境・アカウントの指定・PATH の入口・待ちの一覧・副命令。"""

from __future__ import annotations

import io
import json
import subprocess
import time
from pathlib import Path

import pytest

from ndf import identity
from ndf.contracts import facts
from ndf.domains.relay import accounts, blocks, cli, install, pending, session_env, signals

FENCE = "ndf-next"


def test_fenced_blocks_are_read_through_the_markdown_adapter():
    text = f"前置き\n\n```{FENCE}\n/goal A\n```\n```python\nx = 1\n```\n~~~{FENCE}\n/goal B\n~~~\n"
    assert blocks.fenced(text, FENCE) == ["/goal A", "/goal B"]
    assert blocks.last_block(text, FENCE) == "/goal B"
    assert blocks.last_block("本文だけ", FENCE) is None


def test_unclosed_block_runs_to_the_end():
    assert blocks.fenced(f"```{FENCE}\n/goal A\n続き", FENCE) == ["/goal A\n続き"]


def test_signal_files_are_written_atomically(tmp_path):
    assert signals.write_next(tmp_path, "s1", "/goal x", True, 0.1)
    assert signals.read_next(tmp_path)["block"] == "/goal x"
    payload = {"tool_use_id": "t", "tool_input": {"questions": [{"question": "q"}]}, "cwd": "/w"}
    assert signals.write_question(tmp_path / signals.QUESTION_FILE, "s1", payload, 0.1)
    assert json.loads((tmp_path / signals.QUESTION_FILE).read_text(encoding="utf-8"))["questions"] == [{"question": "q"}]
    assert not list(tmp_path.glob(".*.tmp"))


def test_relay_and_organizer_places_come_from_the_environment(tmp_path):
    assert signals.relay_dir({}) is None and signals.relay_dir({"NDF_RELAY_DIR": str(tmp_path / "none")}) is None
    assert signals.relay_dir({"NDF_RELAY_DIR": str(tmp_path)}) == tmp_path
    assert signals.organizer_question_path({"NDF_ORGANIZER_DIR": "/o", "NDF_ORGANIZER_LANE": "l1"}) == Path("/o/questions/l1.json")
    assert signals.organizer_question_path({"NDF_ORGANIZER_DIR": "/o", "NDF_ORGANIZER_LANE": "../x"}) is None


def test_child_env_drops_editor_git_auth_and_sets_relay_values(tmp_path):
    parent = {"PATH": "/bin", "GIT_ASKPASS": "/vscode/askpass.sh", "VSCODE_GIT_IPC_HANDLE": "/tmp/x.sock", "VSCODE_GIT_ASKPASS_MAIN": "m"}
    got = session_env.child_env(parent, tmp_path)
    assert got["PATH"] == "/bin" and not set(facts.ids("env", child="drop")) & set(got)
    assert got["CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION"] == "false"
    assert "NDF_CLAUDE_ACCOUNT" not in got
    accounts.use(tmp_path, "work")
    assert session_env.child_env(parent, tmp_path)["NDF_CLAUDE_ACCOUNT"] == "work"
    assert session_env.child_env({**parent, "NDF_CLAUDE_ACCOUNT": "main"}, tmp_path)["NDF_CLAUDE_ACCOUNT"] == "main"


def test_account_pin_round_trip(tmp_path):
    assert accounts.pinned(tmp_path) is None and accounts.chosen({}, tmp_path) is None
    accounts.use(tmp_path, "sub-1")
    assert accounts.pinned(tmp_path) == "sub-1" and accounts.chosen({}, tmp_path) == "sub-1"
    with pytest.raises(accounts.AccountError):
        accounts.use(tmp_path, "../x")
    assert accounts.clear(tmp_path) is True and accounts.clear(tmp_path) is False


def test_path_entry_install_status_uninstall(tmp_path):
    root = tmp_path / "plugin"
    environ = {"XDG_BIN_HOME": str(tmp_path / "bin")}
    assert install.status(root, environ).state == "absent"
    got = install.install(root, environ)
    assert got.state == "ours" and got.path.stat().st_mode & 0o111
    assert str(root / "bin" / "ndf") in got.path.read_text(encoding="utf-8")
    assert install.status(tmp_path / "other", environ).state == "stale"
    assert install.uninstall(root, environ).state == "absent" and not got.path.exists()
    got.path.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    assert install.install(root, environ).state == "foreign" and install.uninstall(root, environ).state == "foreign"
    assert got.path.read_text(encoding="utf-8") == "#!/bin/sh\necho mine\n"


def test_installed_entry_runs_the_plugin_command(tmp_path):
    root = tmp_path / "plugin"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "ndf").write_text('#!/bin/sh\necho "args:$*"\n', encoding="utf-8")
    (root / "bin" / "ndf").chmod(0o755)
    got = install.install(root, {"XDG_BIN_HOME": str(tmp_path / "bin")})
    done = subprocess.run([str(got.path), "status", "x y"], capture_output=True, text=True, timeout=30)
    assert done.stdout.strip() == "args:relay status x y"


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


def test_account_help_lists_every_subcommand(capsys):
    assert cli.main(["account", "--help"]) == 0
    out = capsys.readouterr().out
    assert all(name in out for name in cli.ACCOUNT_COMMANDS)


def test_relay_cli_account_and_decide(monkeypatch, capsys):
    assert cli.main(["account", "use", "work"]) == 0
    assert accounts.pinned(identity.load().state_dir) == "work"
    assert cli.main(["account", "show"]) == 0
    assert "work" in capsys.readouterr().out
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"next_block": "/goal x", "question": False, "child_alive": True, "child_exit": None, "starts_today": 0, "max_starts": 3})))
    assert cli.main(["decide"]) == 0
    assert '"switch"' in capsys.readouterr().out


def test_relay_cli_mark_sets_the_counter_mark(capsys):
    from ndf.domains.hooks.counter import Counter

    assert cli.main(["mark", "s-9"]) == 0
    with Counter.open("s-9", identity.load().state_dir) as c:
        assert c.data["relay_mark"] is True
    start = time.monotonic()
    assert cli.main(["mark", "../bad"]) != 0 and time.monotonic() - start < 5
