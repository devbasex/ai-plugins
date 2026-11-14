from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from ndf import identity
from ndf.adapters import agents, claude, cli, run_log

MINIMAL_HEAD = ["-p", "--output-format", "json", "--no-session-persistence", "--setting-sources", "", "--strict-mcp-config"]


def test_full_args_disable_installed_ndf_and_load_this_plugin(tmp_path):
    argv = claude.claude_args(claude.ClaudeCall(system="sys", permission=claude.Permission.FULL), root=tmp_path)

    settings = json.loads(argv[argv.index("--settings") + 1])
    assert settings == {"enabledPlugins": claude.DISABLED_PLUGINS}
    assert all(enabled is False for enabled in claude.DISABLED_PLUGINS.values())
    assert argv[argv.index("--plugin-dir") + 1] == str(tmp_path)
    assert argv[0] == claude.CLAUDE and "--append-system-prompt" in argv


def test_full_args_default_to_own_root():
    argv = claude.claude_args(claude.ClaudeCall(system="sys", permission=claude.Permission.FULL, resume="abc"))

    assert argv[argv.index("--plugin-dir") + 1] == str(identity.load().root)
    assert argv[-2:] == ["--resume", "abc"]


def test_minimal_args_stay_minimal_without_plugin_settings():
    no_tools = claude.claude_args(claude.ClaudeCall(system="sys"))
    with_tools = claude.claude_args(
        claude.ClaudeCall(system="sys", permission=claude.Permission.TOOLS, tools="Read,Grep", model="m"), base=("x", "y")
    )

    assert no_tools == ["claude", *MINIMAL_HEAD, "--disable-slash-commands", "--system-prompt", "sys", "--tools", ""]
    assert with_tools[:2] == ["x", "y"] and with_tools[-2:] == ["--model", "m"]
    for argv in (no_tools, with_tools):
        assert "--settings" not in argv and "--plugin-dir" not in argv


def test_json_schema_is_passed_as_json():
    argv = claude.claude_args(claude.ClaudeCall(system="sys", json_schema={"type": "object"}))

    assert json.loads(argv[argv.index("--json-schema") + 1]) == {"type": "object"}


DANGEROUS = ("--dangerously-skip-permissions", "bypassPermissions")


def test_each_permission_has_its_own_tools_and_never_skips_permissions(tmp_path):
    """判断は Tool を使わせず、Tool を許す起動も acceptEdits までにする（#3364）。"""
    judge = claude.claude_args(claude.ClaudeCall(system="sys"))
    tools = claude.claude_args(claude.ClaudeCall(system="sys", permission=claude.Permission.TOOLS, tools="Read"))
    full = claude.claude_args(claude.ClaudeCall(system="sys", permission=claude.Permission.FULL), root=tmp_path)

    assert judge[judge.index("--tools") + 1] == "" and "--permission-mode" not in judge and "--allowed-tools" not in judge
    assert tools[tools.index("--allowed-tools") + 1] == "Read" and tools[tools.index("--permission-mode") + 1] == "acceptEdits"
    assert full[full.index("--allowed-tools") + 1] == claude.FULL_TOOLS and full[full.index("--permission-mode") + 1] == "acceptEdits"
    for argv in (judge, tools, full):
        assert not set(argv) & set(DANGEROUS)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"permission": claude.Permission.TOOLS},
        {"permission": claude.Permission.JUDGE, "tools": "Read"},
        {"permission": claude.Permission.FULL, "tools": "Read"},
        {"permission": claude.Permission.JUDGE, "resume": "abc"},
    ],
)
def test_call_refuses_tools_or_resume_outside_their_permission(kwargs):
    with pytest.raises(ValueError):
        claude.ClaudeCall(system="sys", **kwargs)


def test_launch_args_subcommand_prints_the_same_values(capsys, last_result):
    assert cli.main([]) == 0

    out = last_result(capsys.readouterr().out)
    assert out["status"] == "ok"
    assert out["items"] == [{"args": claude.plugin_args(identity.load().root)}]


def test_launch_args_refuses_arguments(capsys):
    assert cli.main(["x"]) != 0
    assert json.loads(capsys.readouterr().out)["cause"]["kind"] == "invalid"


def test_call_claude_reads_structured_output(tmp_path):
    fake = tmp_path / "claude"
    payload = {"result": "", "structured_output": {"decision": "fix"}, "total_cost_usd": 0.25}
    fake.write_text(f"#!/bin/sh\ncat > /dev/null\necho '{json.dumps(payload)}'\n")
    fake.chmod(0o755)

    got = claude.call_claude([str(fake)], "問い", 30)

    assert got == claude.ClaudeReply(True, {"decision": "fix"}, "", 0.25)


def fake_claude(tmp_path: Path, stdout: str) -> Path:
    """問いを読み捨て、`stdout` をそのまま標準出力へ書く偽の claude。"""
    out = tmp_path / "claude.stdout"
    out.write_text(stdout)
    fake = tmp_path / "claude"
    fake.write_text(f"#!/bin/sh\ncat > /dev/null\ncat '{out}'\n")
    fake.chmod(0o755)
    return fake


def test_call_claude_missing_command_is_not_ok(tmp_path):
    got = claude.call_claude([str(tmp_path / "missing")], "問い", 30)

    assert not got.ok and got.failure is claude.Failure.LAUNCH


@pytest.mark.parametrize("stdout", ["not json", json.dumps({"is_error": True, "result": "boom"})])
def test_call_claude_failures_are_not_ok(tmp_path, stdout):
    assert not claude.call_claude([str(fake_claude(tmp_path, stdout))], "問い", 30).ok


@pytest.mark.parametrize(
    ("stdout", "answer"),
    [(json.dumps({"result": '{"a": 1}'}), {"a": 1}), (json.dumps({"result": "fix です"}), None)],
)
def test_call_claude_reads_json_in_result_text(tmp_path, stdout, answer):
    assert claude.call_claude([str(fake_claude(tmp_path, stdout))], "問い", 30).answer == answer


def test_agent_paths_at_names_files_next_to_stem():
    paths = agents.AgentPaths.at(Path("/w/kiro"))

    names = (paths.out.name, paths.err.name, paths.launch.name, paths.progress.name)
    assert names == ("kiro.out", "kiro.err.log", "kiro-launch.json", "kiro-progress.log")


# ---------------------------------------------------------------- 現状固定: 子へ渡す環境と失敗の形（I-011・I-012）


@pytest.fixture
def no_run_env(monkeypatch):
    """実行の記録を開いていない状態から始め、終わったら手放す。"""
    # 実行の記録の隔離と close は conftest の autouse の _fresh_run_log が持つ。ここは子へ受け継がれるかを見る親の環境変数だけを置く
    monkeypatch.setenv("PIN_PARENT", "kept")


ENV_DUMP = (
    "import json, os, sys\n"
    "prompt = sys.stdin.read()\n"
    "seen = {k: os.environ.get(k) for k in ('NDF_RUN_ID', 'NDF_RUN_LOG', 'PIN_PARENT')}\n"
    "print(json.dumps({'result': json.dumps({'prompt': prompt, 'cwd': os.getcwd(), 'env': seen})}))\n"
)


def test_call_claude_passes_parent_env_prompt_and_cwd_without_run_log(tmp_path, no_run_env):
    got = claude.call_claude([sys.executable, "-c", ENV_DUMP], "問い", 30, cwd=tmp_path)

    assert (got.ok, got.cost, got.error) == (True, 0.0, None)
    env = {"NDF_RUN_ID": None, "NDF_RUN_LOG": None, "PIN_PARENT": "kept"}
    assert got.answer == {"prompt": "問い", "cwd": str(tmp_path), "env": env}


def test_call_claude_passes_open_run_log_to_child(no_run_env):
    log = run_log.open_run("entry", run_id="r1", location="repo")

    got = claude.call_claude([sys.executable, "-c", ENV_DUMP], "q", 30)

    assert got.answer["env"] == {"NDF_RUN_ID": "r1", "NDF_RUN_LOG": str(log.path), "PIN_PARENT": "kept"}


def test_call_claude_nonzero_exit_without_stdout_reports_stderr(no_run_env):
    script = "import sys; sys.stdin.read(); sys.stderr.write('boom'); sys.exit(3)"

    got = claude.call_claude([sys.executable, "-c", script], "q", 30)

    assert got == claude.ClaudeReply(False, None, "boom", 0.0, "claude が終了コード 3 で終えた: boom")


LIMIT_JSON = json.dumps({"is_error": True, "api_error_status": 429, "result": "You've hit your limit · resets 3pm"})


@pytest.mark.parametrize(
    "script",
    [
        f"import sys; sys.stdin.read(); print({LIMIT_JSON!r})",
        'import sys; sys.stdin.read(); sys.stderr.write("You\'ve hit your limit · resets 3pm\\n"); sys.exit(1)',
    ],
)
def test_call_claude_tells_usage_limit_apart(no_run_env, script):
    got = claude.call_claude([sys.executable, "-c", script], "q", 30)

    assert not got.ok and got.failure is claude.Failure.USAGE_LIMIT


def test_call_claude_other_error_can_be_retried(tmp_path):
    got = claude.call_claude([str(fake_claude(tmp_path, json.dumps({"is_error": True, "result": "boom"})))], "q", 30)

    assert not got.ok and got.failure is None


def test_call_claude_nonzero_exit_with_stdout_still_parses(no_run_env):
    script = 'import sys; sys.stdin.read(); print(\'{"result": "{}", "total_cost_usd": 1}\'); sys.exit(1)'

    got = claude.call_claude([sys.executable, "-c", script], "q", 30)

    assert got == claude.ClaudeReply(True, {}, "{}", 1.0)


def test_call_claude_timeout_is_not_ok(no_run_env):
    got = claude.call_claude([sys.executable, "-c", "import time; time.sleep(5)"], "q", 0.2)

    assert (got.ok, got.answer, got.raw, got.cost) == (False, None, "", 0.0)
    assert got.error.startswith("claude が期限を超えた: ") and got.failure is claude.Failure.TIMEOUT


START_DUMP = (
    "import json, os, sys\n"
    "seen = {k: os.environ.get(k) for k in ('NDF_RUN_ID', 'NDF_RUN_LOG', 'X_SPEC', 'PIN_PARENT')}\n"
    "own = os.getsid(0) == os.getpid()\n"
    "print(json.dumps({'env': seen, 'cwd': os.getcwd(), 'stdin': sys.stdin.read(), 'own_session': own}))\n"
    "sys.stderr.write('err')\n"
)


def _start_spec(tmp_path: Path) -> agents.Launch:
    paths = agents.AgentPaths.at(tmp_path / "deep" / "run" / "codex")
    env = {"X_SPEC": "s", "NDF_RUN_ID": "from-spec"}
    return agents.Launch("codex", (sys.executable, "-c", START_DUMP), tmp_path, paths, 30, env=env)


def test_start_writes_files_in_new_session_with_spec_env(tmp_path, no_run_env):
    spec = _start_spec(tmp_path)

    assert agents.run_agent(spec, poll=0.05).exit_code == 0

    got = json.loads(spec.paths.out.read_text())
    assert got["env"] == {"NDF_RUN_ID": "from-spec", "NDF_RUN_LOG": None, "X_SPEC": "s", "PIN_PARENT": "kept"}
    assert (got["cwd"], got["stdin"], got["own_session"]) == (str(tmp_path), "", True)
    assert spec.paths.err.read_text() == "err"


def test_start_run_log_env_overrides_spec_env(tmp_path, no_run_env):
    spec = _start_spec(tmp_path)
    log = run_log.open_run("entry", run_id="r2", location="repo")

    assert agents.run_agent(spec, poll=0.05).exit_code == 0

    got = json.loads(spec.paths.out.read_text())
    assert got["env"] == {"NDF_RUN_ID": "r2", "NDF_RUN_LOG": str(log.path), "X_SPEC": "s", "PIN_PARENT": "kept"}


def test_start_without_spec_env_keeps_parent_env(tmp_path, no_run_env):
    spec = agents.Launch("codex", (sys.executable, "-c", START_DUMP), tmp_path, agents.AgentPaths.at(tmp_path / "codex"), 30)

    assert agents.run_agent(spec, poll=0.05).exit_code == 0

    got = json.loads(spec.paths.out.read_text())
    assert got["env"] == {"NDF_RUN_ID": None, "NDF_RUN_LOG": None, "X_SPEC": None, "PIN_PARENT": "kept"}
