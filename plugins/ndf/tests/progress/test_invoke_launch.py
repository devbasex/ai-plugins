"""#1959: Skill を回す起動は、起動の設定の Skill の名前の指定を解き、壊れているか未承認なら起動しない。"""

from __future__ import annotations

import json
from pathlib import Path

from ndf.adapters import claude, launch_settings
from ndf.progress import invoke


def _skill_run() -> invoke.StageRun:
    return invoke.StageRun("skill", "design", {"issues": [5]}, ("pr",), "none", "s", {"skill": "design"})


def test_skill_with_an_unapproved_spec_stops_without_launching(tmp_path: Path, monkeypatch) -> None:
    """条件 11: Skill の名前の指定が未承認なら、claude を呼ばずに precondition で止まり、承認の手順を示す。"""
    monkeypatch.setattr(launch_settings.launch_approval, "repo_key", lambda root: str(root))
    (tmp_path / ".ndf").mkdir()
    (tmp_path / ".ndf" / "launch.json").write_text(json.dumps({"version": 1, "permissions": {"steps": {"design": {"load": ["LSP"]}}}}))
    seen: list[list[str]] = []
    got = invoke.invoke(_skill_run(), tmp_path, tmp_path, call_claude=lambda argv, *a: seen.append(list(argv)))
    assert seen == [] and got.status == "stopped" and got.cause["kind"] == "precondition"
    assert "launch-config" in got.cause["detail"]


def test_skill_with_an_approved_spec_launches_with_the_spec_and_the_check(tmp_path: Path, monkeypatch) -> None:
    """条件 12: 承認した `load` は `--tools` に入り、子を起動する直前の照らし直しを渡す。"""
    monkeypatch.setattr(launch_settings.launch_approval, "repo_key", lambda root: str(root))
    monkeypatch.setattr(claude, "list_plugins", lambda **_: [])
    (tmp_path / ".ndf").mkdir()
    (tmp_path / ".ndf" / "launch.json").write_text(json.dumps({"version": 1, "permissions": {"steps": {"design": {"load": ["LSP"]}}}}))
    sha = launch_settings.approval_target(launch_settings.load(tmp_path)).sha256
    launch_settings.launch_approval.append(str(tmp_path), sha, launch_settings.launch_approval.Action.APPROVE, approver="人")
    seen: list[tuple[list[str], claude.LaunchOptions]] = []

    def fake(argv, prompt, timeout, cwd, options):
        seen.append((list(argv), options))
        return claude.ClaudeReply(False, None, "", 0.0, "落ちた")

    invoke.invoke(_skill_run(), tmp_path, tmp_path, call_claude=fake)
    ((argv, options),) = seen
    assert argv[argv.index("--tools") + 1].endswith(",LSP") and options.verify is not None and options.verify() is None
