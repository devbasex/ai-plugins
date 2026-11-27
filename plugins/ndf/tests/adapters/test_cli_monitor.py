from __future__ import annotations

import json

import pytest

from ndf.adapters import agents
from ndf.contracts import limits

POLL = 0.05


@pytest.mark.parametrize(
    "text",
    [
        "quota exceeded",
        "ERROR: Rate limit exceeded for this model",
        "ERROR: You've hit your usage limit",
        "You've hit your 5-hour limit · resets 12:30am (UTC)",
        "exceeded retry limit, last status: 429",
        "Monthly request limit reached",
        '{"type":"result","api_error_status":429}',
        "HTTP/1.1 429 Too Many Requests",
    ],
)
def test_usage_limit_reads_cli_lines(text):
    assert agents.usage_limit(f"start\n{text}\nnext") == text


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
    assert agents.usage_limit(f"start\n{text}\n") is None


def test_early_warnings_by_kind(tmp_path):
    paths = agents.AgentPaths.at(tmp_path / "a")
    assert agents.early_warnings(paths) == {}

    paths.err.write_text("ok\nError: something broke\n")
    assert agents.early_warnings(paths) == {"stderr": "Error: something broke"}

    paths.out.write_text(json.dumps({"subtype": "success", "permission_denials": [{"tool": "Bash"}]}))
    assert set(agents.early_warnings(paths)) == {"stderr", "denials"}


def test_denials_are_read_from_the_last_line_of_a_stream(tmp_path):
    # #3945: stream-json は 1 歩ごとの行の後に結果の行を出す。結果の行の拒否を読む
    paths = agents.AgentPaths.at(tmp_path / "a")
    steps = [{"type": "system", "subtype": "init"}, {"type": "assistant"}]
    last = {"type": "result", "subtype": "success", "permission_denials": [{"tool": "Bash"}]}
    paths.out.write_text("\n".join(json.dumps(x) for x in [*steps, last]) + "\n")
    assert set(agents.early_warnings(paths)) == {"denials"}
    paths.out.write_text("\n".join(json.dumps(x) for x in steps) + "\n")
    assert agents.early_warnings(paths) == {}


def test_denials_survive_line_separators_inside_the_result(tmp_path):
    # 回答本文の U+2028・U+2029 は行の区切りではない。結果の JSON を割らずに拒否を読む
    paths = agents.AgentPaths.at(tmp_path / "a")
    last = {"type": "result", "subtype": "success", "result": "a b c", "permission_denials": [1]}
    paths.out.write_text(json.dumps({"type": "assistant"}) + "\n" + json.dumps(last, ensure_ascii=False) + "\n\n")
    assert set(agents.early_warnings(paths)) == {"denials"}


def test_warned_reports_each_kind_once():
    warned = agents.Warned()

    assert warned.new({"stderr": "a"}) == [("stderr", "a")]
    assert warned.new({"stderr": "a", "denials": "b"}) == [("denials", "b")]
    assert warned.new({"stderr": "a", "denials": "b"}) == []


def test_stall_seconds_falls_back_to_default():
    assert agents.stall_seconds("codex") == 180
    assert agents.stall_seconds("unknown-agent") == limits.seconds("monitor.stall-default")


def test_start_failure_is_a_failed_status(tmp_path):
    spec = agents.Launch("codex", (str(tmp_path / "no-such-cli"),), tmp_path, agents.AgentPaths.at(tmp_path / "run" / "codex"), 30)

    status = agents.run_agent(spec, poll=POLL)

    assert (status.state, status.exit_code) == ("failed", None)
    assert status.detail.startswith("起動できない")


@pytest.mark.parametrize(("reason", "launched"), [(None, True), ("承認が取り消された。/ndf:launch-config で承認する", False)])
def test_the_check_just_before_the_start_decides_whether_the_child_starts(tmp_path, monkeypatch, reason, launched):
    """#1959: 起動の直前の照らし直し（`Launch.verify`）が理由を返せば子を起動せず refused。None なら起動する。"""
    from ndf.adapters import cli_floor

    monkeypatch.setattr(cli_floor, "require", lambda *a, **k: False)
    started: list[agents.Launch] = []
    monkeypatch.setattr(
        agents, "_start_and_watch", lambda spec, poll: started.append(spec) or agents.AgentStatus(spec.agent, "done", 0, "", ())
    )
    spec = agents.Launch("codex", ("codex",), tmp_path, agents.AgentPaths.at(tmp_path / "run"), 30, verify=lambda: reason)
    status = agents._guarded_run(spec, None)
    assert bool(started) is launched
    assert status.state == ("done" if launched else agents.AgentState.REFUSED)
    assert launched or status.detail == reason
