"""現状固定（I-003）: Skill を回す起動（`invoke._call_skill`）の、準備・起動と切り替え・結果の組み立てをまたぐ振る舞い。

`claude -p` は起動せず、偽の呼び出しを `invoke` の `call_claude` へ渡す。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ndf import identity
from ndf.adapters import claude, claude_choice, launch_settings, model_choice
from ndf.contracts import result
from ndf.progress import invoke

REFUSED = {"is_error": True, "api_error_status": 404, "result": "There's an issue with the selected model", "modelUsage": {}}
REFUSED["usage"] = dict.fromkeys(claude.REFUSED_TOKENS, 0)


def _skill_run() -> invoke.StageRun:
    return invoke.StageRun("skill", "design", {"issues": [5]}, ("pr",), "none", "s", {"skill": "design"})


def _refused() -> claude.ClaudeReply:
    return claude.parse_reply(json.dumps(REFUSED))._replace(launched=True)


def _done(**outputs) -> claude.ClaudeReply:
    return claude.ClaudeReply(True, result.result("skill", result.OK, "済", outputs=outputs), "", 0.0, launched=True)


@pytest.fixture(autouse=True)
def _installed_plugins(monkeypatch):
    monkeypatch.setattr(claude, "list_plugins", lambda **_: [])


@pytest.fixture
def unit(tmp_path: Path, monkeypatch, launch_chooser):
    """値を選ぶ処理を差し込み、`NDF_UNIT_ID` の単位で最後の組（sonnet・high）を割り付ける。"""
    monkeypatch.setattr(model_choice, "_random_value", lambda: 0.99)
    monkeypatch.setenv("NDF_USAGE_DIR", str(tmp_path / "store"))
    monkeypatch.setenv(identity.env_name("unit-id"), "run-1:design")
    for key in ("launch-model", "launch-effort", "claude-effort-level"):
        monkeypatch.delenv(identity.env_name(key), raising=False)
    (tmp_path / ".git").mkdir()
    return launch_chooser


def _approve(root: Path, monkeypatch, spec: dict) -> str:
    """工程 design の起動の指定を書いて承認し、承認の対象のハッシュを返す。"""
    monkeypatch.setattr(launch_settings.launch_approval, "repo_key", lambda r: str(r))
    (root / ".ndf").mkdir(exist_ok=True)
    (root / ".ndf" / "launch.json").write_text(json.dumps({"version": 1, "permissions": {"steps": {"design": spec}}}))
    sha = launch_settings.approval_target(launch_settings.load(root)).sha256
    launch_settings.launch_approval.append(str(root), sha, launch_settings.launch_approval.Action.APPROVE, approver="人")
    return sha


def test_the_relaunch_gets_the_remaining_time_and_the_bound_but_the_first_launch_has_no_bound(tmp_path, unit, monkeypatch):
    monkeypatch.setattr(claude_choice, "_monotonic", lambda: now[0])
    now = [0.0]
    limit = invoke.limits.seconds(invoke.SKILL_LIMIT)
    replies, seen = [_refused(), _done(pr=3)], []

    def fake(argv, prompt, timeout, cwd, options):
        seen.append((prompt, timeout, cwd, options.left))
        now[0] += 10
        return replies.pop(0)

    got = invoke.invoke(_skill_run(), tmp_path, tmp_path, call_claude=fake)
    assert got.status == "ok" and got.outputs == {"pr": 3}
    first, second = seen
    assert first == (invoke.skill_prompt(_skill_run()), limit, tmp_path, None)
    assert second[:3] == (first[0], limit - 10, tmp_path)
    now[0] += 5
    assert second[3]() == limit - 25  # 起動し直しの期限の残りは、呼ぶたびに測る


def test_each_launch_runs_under_the_env_of_the_picked_value_and_the_env_is_restored(tmp_path, unit, monkeypatch):
    """子の環境の変更は起動の間だけで、切り替えた後の起動は基準の組の変更を使う。呼び出しが例外でも元へ戻す。"""
    name = "NDF_TEST_PICKED"
    monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(claude_choice.ChoiceRun, "env_changes", lambda self: {name: str(self.request_try)})
    replies, seen = [_refused(), _done(pr=3)], []

    def fake(argv, prompt, timeout, cwd, options):
        seen.append(os.environ.get(name))
        return replies.pop(0)

    invoke.invoke(_skill_run(), tmp_path, tmp_path, call_claude=fake)
    assert seen == ["1", "2"] and name not in os.environ

    def broken(*a, **k):
        raise RuntimeError("落ちた")

    with pytest.raises(RuntimeError):
        invoke.invoke(_skill_run(), tmp_path, tmp_path, call_claude=broken)
    assert name not in os.environ


def test_the_model_that_answered_is_observed_once(tmp_path, unit, monkeypatch):
    observed: list[str] = []
    monkeypatch.setattr(unit, "observed", lambda choice, model: observed.append(model))
    done = _done(pr=3)._replace(model_usage={"claude-x": {"inputTokens": 1}})
    invoke.invoke(_skill_run(), tmp_path, tmp_path, call_claude=lambda *a, **k: done)
    assert observed == ["claude-x"]


def test_an_approval_revoked_before_the_switch_stops_without_relaunching(tmp_path, unit, monkeypatch):
    sha = _approve(tmp_path, monkeypatch, {"load": ["LSP"]})
    seen: list = []

    def fake(argv, prompt, timeout, cwd, options):
        seen.append(argv)
        approval = launch_settings.launch_approval
        approval.append(str(tmp_path), sha, approval.Action.REVOKE, approver="人")
        return _refused()

    got = invoke.invoke(_skill_run(), tmp_path, tmp_path, call_claude=fake)
    assert len(seen) == 1 and got.status == "stopped" and got.outputs == {} and got.warnings == ()
    assert got.report["tool"] == "skill" and got.cause["kind"] == "precondition"
    assert got.cause["detail"].startswith("Skill design を基準の組で起動し直さない: ")
    assert "取り消された" in got.cause["detail"]


@pytest.mark.parametrize("raises", [False, True])
def test_the_mcp_copies_live_through_the_relaunch_and_are_removed_after(tmp_path, unit, monkeypatch, raises):
    (tmp_path / "mcp.json").write_text(json.dumps({"mcpServers": {"s": {"command": "x"}}}))
    _approve(tmp_path, monkeypatch, {"mcp": ["mcp.json"]})
    replies, seen = [_refused(), _done(pr=3)], []

    def fake(argv, prompt, timeout, cwd, options):
        copy = Path(argv[argv.index("--mcp-config") + 1])
        seen.append((copy, copy.exists()))
        if raises:
            raise RuntimeError("落ちた")
        return replies.pop(0)

    if raises:
        with pytest.raises(RuntimeError):
            invoke.invoke(_skill_run(), tmp_path, tmp_path, call_claude=fake)
    else:
        assert invoke.invoke(_skill_run(), tmp_path, tmp_path, call_claude=fake).status == "ok"
    assert [there for _, there in seen] == [True] * (1 if raises else 2)
    assert len({copy for copy, _ in seen}) == 1 and not seen[0][0].exists()


def test_the_notice_follows_the_warnings_of_the_result(tmp_path, monkeypatch):
    unreadable = claude.PluginArgs(["--plugin-dir", "p"], claude.LIST_UNREADABLE)
    monkeypatch.setattr(claude, "plugin_args", lambda root, cwd=None: unreadable)
    got = invoke.invoke(_skill_run(), tmp_path, tmp_path, call_claude=lambda *a, **k: _done(pr=3, branch="b"))
    assert len(got.warnings) == 2 and "branch" in got.warnings[0] and got.warnings[1] == claude.LIST_UNREADABLE
