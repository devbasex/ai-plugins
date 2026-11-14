from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from ndf.adapters import cli

# 偽の kiro-cli: cli-agents の x.json を書き換え、x.json.bak を残す
FAKE_KIRO = (
    "from pathlib import Path\n"
    "d = Path('.amazonq/cli-agents')\n"
    "(d / 'x.json.bak').write_text((d / 'x.json').read_text())\n"
    "(d / 'x.json').write_text('{\"rewritten\": true}')\n"
)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    for name in (cli.ENV_RUN_ID, cli.ENV_RUN_LOG, cli.ENV_RUN_LOG_SINK):
        monkeypatch.delenv(name, raising=False)
    cli.close()
    yield
    cli.close()


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return done.stdout


def worktree(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    agents = work / ".amazonq" / "cli-agents"
    agents.mkdir(parents=True)
    (agents / "x.json").write_text('{"original": true}')
    git(work, "init", "-q")
    git(work, "add", ".")
    git(work, "commit", "-q", "-m", "init")
    return work


def kiro(work: Path, tmp_path: Path) -> cli.Launch:
    return cli.Launch("kiro", (sys.executable, "-c", FAKE_KIRO), work, cli.AgentPaths.at(tmp_path / "run" / "kiro"), 30)


def test_kiro_rewrites_are_restored_and_new_bak_removed(tmp_path):
    work = worktree(tmp_path)
    (work / ".amazonq" / "cli-agents" / "mine.bak").write_text("利用者の控え")
    before = git(work, "status", "--porcelain", "--", ".amazonq")

    status = cli.run_agent(kiro(work, tmp_path), poll=0.05)

    agents = work / ".amazonq" / "cli-agents"
    assert status.state == "done" and status.restore == "restored"
    assert (agents / "x.json").read_text() == '{"original": true}'
    assert not (agents / "x.json.bak").exists()
    assert (agents / "mine.bak").read_text() == "利用者の控え"
    assert git(work, "status", "--porcelain", "--", ".amazonq") == before
    assert json.loads((tmp_path / "run" / "kiro-launch.json").read_text())["restore"] == "restored"
    assert not (tmp_path / "run" / "kiro-launch-amazonq").exists()


def test_outside_worktree_runs_without_backup(tmp_path):
    work = tmp_path / "plain"
    (work / ".amazonq" / "cli-agents").mkdir(parents=True)
    (work / ".amazonq" / "cli-agents" / "x.json").write_text("{}")

    status = cli.run_agent(kiro(work, tmp_path), poll=0.05)

    assert status.restore == "restore_skipped"
    assert json.loads((tmp_path / "run" / "kiro-launch.json").read_text())["restore"] == "restore_skipped"


def test_restore_failure_keeps_backup_and_reports(tmp_path, monkeypatch):
    work = worktree(tmp_path)

    def broken(*_args):
        raise OSError("書けない")

    monkeypatch.setattr(cli, "_restore", broken)
    status = cli.run_agent(kiro(work, tmp_path), poll=0.05)

    assert status.restore == "restore_failed"
    assert (tmp_path / "run" / "kiro-launch-amazonq" / "x.json").read_text() == '{"original": true}'
    assert "戻せない" in (tmp_path / "run" / "kiro.err.log").read_text()


def test_other_agents_are_not_guarded(tmp_path):
    work = worktree(tmp_path)
    spec = cli.Launch("codex", (sys.executable, "-c", FAKE_KIRO), work, cli.AgentPaths.at(tmp_path / "run" / "codex"), 30)

    status = cli.run_agent(spec, poll=0.05)

    assert status.restore is None
    assert (work / ".amazonq" / "cli-agents" / "x.json.bak").exists()
