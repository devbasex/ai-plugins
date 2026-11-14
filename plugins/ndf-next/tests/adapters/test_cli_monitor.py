from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from ndf.adapters import cli

POLL = 0.05


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    for name in (cli.ENV_RUN_ID, cli.ENV_RUN_LOG, cli.ENV_RUN_LOG_SINK):
        monkeypatch.delenv(name, raising=False)
    cli.close()
    yield
    cli.close()


def launch(tmp_path: Path, script: str, agent: str = "codex", timeout: float = 30) -> cli.Launch:
    return cli.Launch(agent, (sys.executable, "-c", script), tmp_path, cli.AgentPaths.at(tmp_path / "run" / agent), timeout)


@pytest.mark.parametrize(
    "text",
    [
        "quota exceeded",
        "ERROR: Rate limit exceeded for this model",
        "ERROR: You've hit your usage limit",
        "exceeded retry limit, last status: 429",
        "Monthly request limit reached",
        '{"type":"result","api_error_status":429}',
        "HTTP/1.1 429 Too Many Requests",
    ],
)
def test_usage_limit_reads_cli_lines(text):
    assert cli.usage_limit(f"start\n{text}\nnext") == text


@pytest.mark.parametrize(
    "text",
    [
        "plugins/x.py:12 [reviewer] codex が quota exceeded と誤判定する",
        "[PR-COMMENT] [reviewer] rate limit exceeded の扱いを直す",
        "+ quota exceeded",
        "# quota exceeded",
        "- quota exceeded のとき",
    ],
)
def test_usage_limit_ignores_comment_snapshots_and_quotes(text):
    assert cli.usage_limit(f"start\n{text}\n") is None


def test_early_warnings_by_kind(tmp_path):
    paths = cli.AgentPaths.at(tmp_path / "a")
    assert cli.early_warnings(paths) == {}

    paths.err.write_text("ok\nError: something broke\n")
    assert cli.early_warnings(paths) == {"stderr": "Error: something broke"}

    paths.out.write_text(json.dumps({"subtype": "success", "permission_denials": [{"tool": "Bash"}]}))
    assert set(cli.early_warnings(paths)) == {"stderr", "denials"}


def test_warned_reports_each_kind_once():
    warned = cli.Warned()

    assert warned.new({"stderr": "a"}) == [("stderr", "a")]
    assert warned.new({"stderr": "a", "denials": "b"}) == [("denials", "b")]
    assert warned.new({"stderr": "a", "denials": "b"}) == []


def test_denials_warning_follows_earlier_stderr_warning_once(tmp_path, capsys):
    script = (
        "import json, sys, time\n"
        "print('Error: early', file=sys.stderr, flush=True)\n"
        "time.sleep(0.4)\n"
        "print(json.dumps({'subtype': 'success', 'permission_denials': [1, 2]}), flush=True)\n"
        "time.sleep(0.4)\n"
    )

    status = cli.run_agent(launch(tmp_path, script, agent="claude"), poll=POLL)

    err = capsys.readouterr().err
    assert status.state == "done" and status.exit_code == 0
    assert status.warnings == ("denials", "stderr")
    assert err.count("早期の警告（stderr）") == 1
    assert err.count("早期の警告（denials）: permission_denials 2 件") == 1


def test_usage_limit_line_stops_agent(tmp_path):
    script = "import sys, time\nprint('ERROR: quota exceeded', file=sys.stderr, flush=True)\ntime.sleep(30)\n"

    status = cli.run_agent(launch(tmp_path, script), poll=POLL)

    assert status.state == "usage_limit" and status.detail == "ERROR: quota exceeded"


def test_comment_snapshot_in_err_log_does_not_stop_agent(tmp_path):
    script = "import sys\nprint('[PR-COMMENT] [u] quota exceeded と出た', file=sys.stderr)\n"

    status = cli.run_agent(launch(tmp_path, script), poll=POLL)

    assert status.state == "done"


def test_timeout_and_failure(tmp_path):
    slow = cli.run_agent(launch(tmp_path, "import time\ntime.sleep(30)\n", timeout=0.3), poll=POLL)
    failed = cli.run_agent(launch(tmp_path, "raise SystemExit(3)\n"), poll=POLL)

    assert slow.state == "timeout"
    assert (failed.state, failed.exit_code) == ("failed", 3)


def test_child_receives_run_id(tmp_path):
    log = cli.open_run("parent", run_id="p", location="repo")
    script = "import os\nprint(os.environ['NDF_RUN_ID'], os.environ['NDF_RUN_LOG'])\n"

    cli.run_agent(launch(tmp_path, script), poll=POLL)

    assert (tmp_path / "run" / "codex.out").read_text().split() == ["p", str(log.path)]


def test_stall_seconds_falls_back_to_default():
    assert cli.stall_seconds("codex") == 180
    assert cli.stall_seconds("unknown-agent") == cli.limits.seconds("monitor.stall-default")


def test_stop_ends_process_group():
    proc = subprocess.Popen([sys.executable, "-c", "import time\ntime.sleep(30)\n"], start_new_session=True)

    cli.stop(proc)

    assert proc.poll() is not None
    cli.stop(proc)
