from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from ndf.adapters import agents, cli

# 偽の kiro-cli: cli-agents の x.json を書き換え、x.json.bak を残す
FAKE_KIRO = (
    "from pathlib import Path\n"
    "d = Path('.amazonq/cli-agents')\n"
    "(d / 'x.json.bak').write_text((d / 'x.json').read_text())\n"
    "(d / 'x.json').write_text('{\"rewritten\": true}')\n"
)


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

    monkeypatch.setattr(agents, "_restore", broken)
    status = cli.run_agent(kiro(work, tmp_path), poll=0.05)

    assert status.restore == "restore_failed"
    assert (tmp_path / "run" / "kiro-launch-amazonq" / "x.json").read_text() == '{"original": true}'
    assert "戻せない" in (tmp_path / "run" / "kiro.err.log").read_text()


def test_unrestored_backup_from_earlier_run_is_kept(tmp_path):
    work = worktree(tmp_path)
    old = tmp_path / "run" / "kiro-launch-amazonq"
    old.mkdir(parents=True)
    (old / "x.json").write_text("前の起動の元の設定")

    status = cli.run_agent(kiro(work, tmp_path), poll=0.05)

    kept = sorted((tmp_path / "run").glob("kiro-launch-amazonq-unrestored-*"))
    assert status.restore == "restored"
    assert [(k / "x.json").read_text() for k in kept] == ["前の起動の元の設定"]


def test_other_agents_are_not_guarded(tmp_path):
    work = worktree(tmp_path)
    spec = cli.Launch("codex", (sys.executable, "-c", FAKE_KIRO), work, cli.AgentPaths.at(tmp_path / "run" / "codex"), 30)

    status = cli.run_agent(spec, poll=0.05)

    assert status.restore is None
    assert (work / ".amazonq" / "cli-agents" / "x.json.bak").exists()


def test_restore_does_not_follow_links_kiro_swapped_in(tmp_path):
    work = worktree(tmp_path)
    outside = tmp_path / "user-file.json"
    outside.write_text("利用者のファイル")
    swap = f"from pathlib import Path\np = Path('.amazonq/cli-agents/x.json')\np.unlink()\np.symlink_to({str(outside)!r})\n"
    spec = cli.Launch("kiro", (sys.executable, "-c", swap), work, cli.AgentPaths.at(tmp_path / "run" / "kiro"), 30)

    status = cli.run_agent(spec, poll=0.05)

    x = work / ".amazonq" / "cli-agents" / "x.json"
    assert status.restore == "restored"
    assert outside.read_text() == "利用者のファイル"
    assert not x.is_symlink() and x.read_text() == '{"original": true}'


# ---- 現状固定: 戻す前に種類の変わったものを消す処理（agents._clear_replaced）の経路


def run_kiro_script(work: Path, tmp_path: Path, script: str) -> cli.AgentStatus:
    spec = cli.Launch("kiro", (sys.executable, "-c", script), work, cli.AgentPaths.at(tmp_path / "run" / "kiro"), 30)
    return cli.run_agent(spec, poll=0.05)


def test_restore_brings_back_file_kiro_deleted(tmp_path):
    work = worktree(tmp_path)

    status = run_kiro_script(work, tmp_path, "from pathlib import Path\nPath('.amazonq/cli-agents/x.json').unlink()\n")

    assert status.restore == "restored"
    assert (work / ".amazonq" / "cli-agents" / "x.json").read_text() == '{"original": true}'


def test_restore_brings_back_whole_dir_kiro_deleted(tmp_path):
    work = worktree(tmp_path)

    status = run_kiro_script(work, tmp_path, "import shutil\nshutil.rmtree('.amazonq/cli-agents')\n")

    assert status.restore == "restored"
    assert (work / ".amazonq" / "cli-agents" / "x.json").read_text() == '{"original": true}'


def test_restore_replaces_dir_kiro_put_in_place_of_file(tmp_path):
    work = worktree(tmp_path)
    script = (
        "from pathlib import Path\np = Path('.amazonq/cli-agents/x.json')\np.unlink()\np.mkdir()\n(p / 'inner.json').write_text('kiro')\n"
    )

    status = run_kiro_script(work, tmp_path, script)

    x = work / ".amazonq" / "cli-agents" / "x.json"
    assert status.restore == "restored"
    assert x.is_file() and x.read_text() == '{"original": true}'


def test_restore_replaces_file_kiro_put_in_place_of_dir(tmp_path):
    work = worktree(tmp_path)
    sub = work / ".amazonq" / "cli-agents" / "sub"
    sub.mkdir()
    (sub / "a.json").write_text("元の a")
    script = "import shutil\nfrom pathlib import Path\np = Path('.amazonq/cli-agents/sub')\nshutil.rmtree(p)\np.write_text('kiro')\n"

    status = run_kiro_script(work, tmp_path, script)

    assert status.restore == "restored"
    assert sub.is_dir() and (sub / "a.json").read_text() == "元の a"


def test_restore_descends_into_subdirs_and_drops_nested_links(tmp_path):
    work = worktree(tmp_path)
    nested = work / ".amazonq" / "cli-agents" / "sub" / "deep"
    nested.mkdir(parents=True)
    (nested / "a.json").write_text("元の a")
    outside = tmp_path / "user-file.json"
    outside.write_text("利用者のファイル")
    script = f"from pathlib import Path\np = Path('.amazonq/cli-agents/sub/deep/a.json')\np.unlink()\np.symlink_to({str(outside)!r})\n"

    status = run_kiro_script(work, tmp_path, script)

    a = nested / "a.json"
    assert status.restore == "restored"
    assert outside.read_text() == "利用者のファイル"
    assert not a.is_symlink() and a.read_text() == "元の a"


def test_restore_keeps_link_that_was_in_backup(tmp_path):
    work = worktree(tmp_path)
    target = tmp_path / "target.json"
    target.write_text("リンク先")
    link = work / ".amazonq" / "cli-agents" / "link.json"
    link.symlink_to(target)

    status = run_kiro_script(work, tmp_path, "print('noop')\n")

    assert status.restore == "restored"
    assert link.is_symlink() and link.resolve() == target.resolve()
    assert target.read_text() == "リンク先"


def test_restore_puts_back_link_kiro_replaced_with_file(tmp_path):
    work = worktree(tmp_path)
    target = tmp_path / "target.json"
    target.write_text("リンク先")
    link = work / ".amazonq" / "cli-agents" / "link.json"
    link.symlink_to(target)
    script = "from pathlib import Path\np = Path('.amazonq/cli-agents/link.json')\np.unlink()\np.write_text('kiro')\n"

    status = run_kiro_script(work, tmp_path, script)

    assert status.restore == "restored"
    assert link.is_symlink() and link.resolve() == target.resolve()
    assert target.read_text() == "リンク先"


def test_restore_leaves_files_kiro_added_that_are_not_bak(tmp_path):
    # 現状固定: 控えに無い名前は消さない（増えた `.bak` だけを消す）
    work = worktree(tmp_path)
    sub = work / ".amazonq" / "cli-agents" / "sub"
    sub.mkdir()
    (sub / "a.json").write_text("元の a")
    script = (
        "from pathlib import Path\nd = Path('.amazonq/cli-agents')\n(d / 'new.json').write_text('kiro')\n"
        "(d / 'sub' / 'new.json').write_text('kiro')\n"
    )

    status = run_kiro_script(work, tmp_path, script)

    agents_dir = work / ".amazonq" / "cli-agents"
    assert status.restore == "restored"
    assert (agents_dir / "new.json").read_text() == "kiro"
    assert (sub / "new.json").read_text() == "kiro"
    assert (sub / "a.json").read_text() == "元の a"
