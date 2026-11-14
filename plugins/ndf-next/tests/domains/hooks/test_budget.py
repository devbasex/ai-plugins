"""hook の予算と上限の順序（M3-H4・IH3）と、止め方の変数（M3-H12）。"""

from __future__ import annotations

import copy
import json

from ndf.contracts import facts, limits
from ndf.domains.hooks import budget, tables

from ._hooks import call_entry, sample


def test_declared_budgets_keep_the_order():
    # CI の検査: 部品の上限 ≤ 締め切り − 余白、締め切り ≤ 登録の timeout ≤ ランタイムの上限
    assert budget.check_budget(tables.policies(), budget.budget_registrations()) == []


def test_every_runtime_event_has_a_budget_with_all_values():
    for runtime, rt in tables.runtimes().items():
        for event in rt["events"]:
            found = budget.budget_for(runtime, event)
            assert found is not None, (runtime, event)
            assert found.deadline > found.margin >= 0 and found.timeout >= found.deadline


def test_runtime_specific_budget_is_read_first():
    assert budget.budget_for("codex", "SessionEnd").policy == "hook.budget.session-end.codex"
    assert budget.budget_for("claude", "SessionEnd").policy == "hook.budget.session-end"
    assert budget.budget_for("codex", "SessionEnd").timeout <= tables.runtimes()["codex"]["events"]["SessionEnd"]["timeout_max"]


def test_component_limit_longer_than_the_deadline_is_a_violation():
    broken = copy.deepcopy(tables.policies())
    broken["hook.ask-forward"]["seconds"] = 8
    errors = budget.check_budget(broken, budget.budget_registrations())
    assert any("hook.ask-forward" in e and "PreToolUse" in e for e in errors)


def test_registration_without_timeout_or_shorter_than_the_deadline_is_a_violation():
    regs = [
        {"runtime": "claude", "event": "Stop", "timeout": None},
        {"runtime": "claude", "event": "PreToolUse", "timeout": 0.5},
        {"runtime": "codex", "event": "SessionEnd", "timeout": 10},
        {"runtime": "claude", "event": "Nope", "timeout": 3},
    ]
    errors = budget.check_budget(tables.policies(), regs)
    assert len(errors) == 4
    assert any("timeout が無い" in e for e in errors) and any("上限 3" in e for e in errors)


def test_registration_files_are_read_when_present(tmp_path):
    (tmp_path / "hooks").mkdir()
    reg = {"hooks": {"Stop": [{"matcher": "", "hooks": [{"type": "command", "command": "x", "timeout": 6}]}]}}
    (tmp_path / "hooks" / "claude.json").write_text(json.dumps(reg), encoding="utf-8")
    regs = budget.registrations_of(tmp_path)
    assert {"runtime": "claude", "event": "Stop", "timeout": 6} in regs
    assert all(r["runtime"] != "claude" or r["event"] == "Stop" for r in regs)
    assert any(r["runtime"] == "codex" for r in regs)


def test_session_start_advises_on_a_broken_budget(tmp_path, monkeypatch):
    broken = copy.deepcopy(tables.policies())
    broken["hook.counter-lock"]["seconds"] = 30
    monkeypatch.setattr(tables, "policies", lambda: broken)
    out = call_entry("claude", "SessionStart", sample("claude", "SessionStart", tmp_path))
    assert "予算の検査で破れ" in out["hookSpecificOutput"]["additionalContext"]
    monkeypatch.undo()
    assert call_entry("claude", "SessionStart", sample("claude", "SessionStart", tmp_path)) is None


def test_hook_budget_policies_pass_the_limits_schema():
    # 予算の鍵は上限の宣言の形（D1）で読める
    for name in tables.policies():
        if name.startswith("hook.budget."):
            assert limits.value(name, "timeout") >= limits.value(name, "deadline")


def test_hook_off_is_the_only_switch_in_the_env_table():
    hooks_rows = facts.ids("env", area="hooks")
    assert hooks_rows == ("NDF_HOOK_OFF",)
    # 閾値は上限の宣言から読み、環境変数では受けない（今の NDF の止める変数を読まない）
    assert not [n for n in facts.ids("env") if n.endswith("_GUARD")]


def test_hook_off_all_and_named_switch_guards_off(tmp_path, monkeypatch):
    payload = sample("claude", "PreToolUse", tmp_path, tool_name="Bash", tool_input={"command": "sleep 999"})
    assert call_entry("claude", "PreToolUse", payload) is not None
    monkeypatch.setenv("NDF_HOOK_OFF", "read-repeat, sleep")
    assert call_entry("claude", "PreToolUse", payload) is None
    monkeypatch.setenv("NDF_HOOK_OFF", "all")
    assert call_entry("claude", "PreToolUse", payload) is None
