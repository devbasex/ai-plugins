"""#1959: Skill を回す起動は、起動の設定の Skill の名前の指定を解き、壊れているか未承認なら起動しない。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf import identity
from ndf.adapters import claude, launch_settings, model_choice
from ndf.contracts import result
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


# --- #2944: Skill の起動のモデルと effort ------------------------------------------------------------

REFUSED = {"is_error": True, "api_error_status": 404, "result": "There's an issue with the selected model", "modelUsage": {}}
REFUSED["usage"] = dict.fromkeys(claude.REFUSED_TOKENS, 0)


class _Rows:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def record_cli(self, usage, meta):
        self.rows.append(dict(meta))


@pytest.fixture
def unit_rows(tmp_path: Path, monkeypatch, launch_chooser) -> list[dict]:
    """値を選ぶ処理を差し込み、`NDF_UNIT_ID` の単位で最後の組（sonnet・high）を割り付ける。"""
    monkeypatch.setattr(model_choice, "_random_value", lambda: 0.99)
    monkeypatch.setattr(claude, "list_plugins", lambda **_: [])
    monkeypatch.setenv("NDF_USAGE_DIR", str(tmp_path / "store"))
    monkeypatch.setenv(identity.env_name("unit-id"), "run-1:design")
    for key in ("launch-model", "launch-effort", "claude-effort-level"):
        monkeypatch.delenv(identity.env_name(key), raising=False)
    (tmp_path / ".git").mkdir()
    rows = _Rows()
    monkeypatch.setattr(claude, "launch_recorder", rows)
    return rows.rows


def _replying(replies: list[claude.ClaudeReply], seen: list[list[str]]):
    def fake(argv, prompt, timeout, cwd, options):
        seen.append(list(argv))
        reply = replies.pop(0)
        options.log.report(reply)
        return reply

    return fake


def test_skill_launches_with_the_unit_arm_and_writes_the_row(tmp_path: Path, unit_rows) -> None:
    seen: list[list[str]] = []
    done = claude.ClaudeReply(True, result.result("design", result.OK, "済"), "", 0.0, launched=True)
    run = invoke.StageRun("skill", "design", {"issues": [5], "mode": "light"}, ("pr",), "none", "s", {"skill": "design"})
    invoke.invoke(run, tmp_path, tmp_path, call_claude=_replying([done], seen))
    ((argv,),) = [seen]
    assert argv[argv.index("--model") + 1 :][:3] == ["sonnet", "--effort", "high"]
    ((row,),) = [unit_rows]
    assert (row["layer"], row["unit"], row["mode"], row["outcome"]) == ("skill", "run-1:design", "light", "ok")


def test_skill_without_a_unit_is_not_assigned(tmp_path: Path, unit_rows, monkeypatch) -> None:
    monkeypatch.delenv(identity.env_name("unit-id"))
    seen: list[list[str]] = []
    invoke.invoke(_skill_run(), tmp_path, tmp_path, call_claude=_replying([claude.ClaudeReply(True, None, "", 0.0, launched=True)], seen))
    assert "--model" not in seen[0] and "assign_id" not in unit_rows[0] and unit_rows[0]["outcome"] == "bad_report"


def test_skill_switches_to_the_base_once_after_a_refusal(tmp_path: Path, unit_rows) -> None:
    refused = claude._parse_reply(json.dumps(REFUSED))._replace(launched=True)
    seen: list[list[str]] = []
    replies = [refused, claude.ClaudeReply(True, None, "", 0.0, launched=True)]
    got = invoke.invoke(_skill_run(), tmp_path, tmp_path, call_claude=_replying(replies, seen))
    assert len(seen) == 2 and "--model" not in seen[1] and got.status == "stopped" and got.cause["kind"] == "bad_report"
    assert [(r["request_try"], r["outcome"]) for r in unit_rows] == [(1, "unknown"), (2, "bad_report")]


def test_a_broken_effort_stops_the_skill_without_launching(tmp_path: Path, unit_rows, monkeypatch) -> None:
    monkeypatch.setenv(identity.env_name("launch-effort"), "huge")
    seen: list = []
    got = invoke.invoke(_skill_run(), tmp_path, tmp_path, call_claude=lambda *a: seen.append(a))
    assert seen == [] and got.cause["kind"] == "precondition"


def test_the_switch_reads_the_plugin_list_once_and_checks_the_answer_once(tmp_path: Path, unit_rows, monkeypatch) -> None:
    listed, checked = [], []
    monkeypatch.setattr(claude, "list_plugins", lambda **_: listed.append(1) or [])
    real = result.received
    monkeypatch.setattr(result, "received", lambda obj: checked.append(1) or real(obj))
    refused = claude._parse_reply(json.dumps(REFUSED))._replace(launched=True)
    done = claude.ClaudeReply(True, result.result("design", result.OK, "済"), "", 0.0, launched=True)
    got = invoke.invoke(_skill_run(), tmp_path, tmp_path, call_claude=_replying([refused, done], []))
    assert got.status == "ok" and unit_rows[-1]["outcome"] == "ok" and (len(listed), len(checked)) == (1, 1)


def test_the_skill_does_not_relaunch_when_recording_the_switch_used_up_the_time(tmp_path: Path, unit_rows, monkeypatch) -> None:
    from ndf.adapters import claude_choice, model_chooser

    now = [0.0]
    monkeypatch.setattr(claude_choice, "_monotonic", lambda: now[0])
    real = model_chooser.base_fallback

    def slow_record(choice, reason):  # 切り替えの記録の錠の待ちで期限を使い切る
        now[0] += invoke.limits.seconds(invoke.SKILL_LIMIT)
        return real(choice, reason)

    monkeypatch.setattr(model_chooser, "base_fallback", slow_record)
    seen: list[list[str]] = []
    refused = claude._parse_reply(json.dumps(REFUSED))._replace(launched=True)
    got = invoke.invoke(_skill_run(), tmp_path, tmp_path, call_claude=_replying([refused], seen))
    assert len(seen) == 1 and got.status == "stopped"
