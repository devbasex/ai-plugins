"""guard が勧める Tool を、ほかの guard が止めない（M3-H8・IH6）。"""

from __future__ import annotations

import pytest

from ndf.domains.hooks import tables

from ._hooks import call_entry, deny_reason, records, sample


def _suggestions():
    return [(g["name"], i, s) for g in tables.guards() for i, s in enumerate(g["suggests"])]


def test_sleep_guard_suggests_monitor_and_background_start():
    sleep = next(g for g in tables.guards() if g["name"] == "sleep")
    tools = {(s["tool"], s["input"].get("run_in_background") is True) for s in sleep["suggests"]}
    assert {("Monitor", False), ("Bash", True)} <= tools


@pytest.mark.parametrize(("guard", "index", "suggestion"), _suggestions(), ids=lambda v: v if isinstance(v, str) else "")
@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_suggested_tool_calls_pass_every_guard(guard, index, suggestion, runtime, tmp_path):
    payload = sample(runtime, "PreToolUse", tmp_path, tool_name=suggestion["tool"], tool_input=suggestion["input"])
    out = call_entry(runtime, "PreToolUse", payload)
    assert deny_reason(out) is None, f"{guard} が勧める {suggestion['tool']} を止めた: {out}"
    assert all(g["kind"] != "deny" for g in records()[-1]["guards"])
