"""中継のプラグインの更新: 導入元の選び方・更新の結果の読み取り・錠の中で 2 つのコマンドを打つ手順（#3812）。

外部コマンド（`claude plugin`）は打たない。結果の行は 2026-11-19 に Claude Code 2.1.328 で実測した JSON の行から作る。
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from ndf.adapters import claude
from ndf.adapters.claude import Ran
from ndf.domains.relay import plugin_update
from ndf.domains.relay.plugin_update import Origin, Target

# 実測の行（Task 0 と設計の「目指す姿の根拠」）
MARKET_OK = '{"command":"marketplace update","outcome":"ok"}'
UPDATED = (
    '{"command":"update","outcome":"ok","plugin":"ndf@mtest","pluginId":"ndf@mtest","scope":"user",'
    '"message":"Plugin \\"ndf\\" updated from 1.0.0 to 1.0.1 for scope user. Restart to apply changes.",'
    '"updateOutcome":"updated","oldVersion":"1.0.0","newVersion":"1.0.1"}'
)
UP_TO_DATE = '{"command":"update","outcome":"ok","updateOutcome":"up_to_date","oldVersion":"11.0.7","newVersion":"11.0.7"}'
NOT_FOUND = (
    '{"command":"update","outcome":"failed","failureCode":"not_found","message":"Plugin \\"x\\" not found in /home/u/.claude/plugins"}'
)


def _row(root: Path, scope: str = "user", version: str = "11.0.7", plugin_id: str = "ndf@ai-plugins", enabled: bool = True) -> dict:
    return {"id": plugin_id, "version": version, "scope": scope, "enabled": enabled, "installPath": str(root)}


def _with(payload: str, **changes) -> str:
    data = json.loads(payload)
    data.update(changes)
    return json.dumps(data)


# --- find_origin（I4・受け入れ条件 6） ---


def test_i4_origin_is_the_enabled_ndf_install_whose_path_is_the_running_root(tmp_path):
    root = tmp_path / "cache" / "11.0.7"
    root.mkdir(parents=True)
    listed = [_row(tmp_path / "dev", plugin_id="ndf@inline"), _row(root, scope="project"), _row(root, plugin_id="other@ai-plugins")]
    assert plugin_update.find_origin(listed, root) == Origin("ndf@ai-plugins", "ai-plugins", "project")


def test_i4_origin_matches_through_a_symlink_and_skips_disabled_rows(tmp_path):
    root = tmp_path / "cache" / "11.0.7"
    root.mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(root)
    listed = [_row(root, scope="user", enabled=False), _row(link, scope="local")]
    assert plugin_update.find_origin(listed, root) == Origin("ndf@ai-plugins", "ai-plugins", "local")


def test_i4_two_scopes_on_another_path_pick_the_one_that_matches(tmp_path):
    root, other = tmp_path / "a", tmp_path / "b"
    listed = [_row(other, scope="user"), _row(root, scope="project")]
    assert plugin_update.find_origin(listed, root).scope == "project"


@pytest.mark.parametrize("listed", [None, [], [_row(Path("/elsewhere"))], [_row(Path("/x"), plugin_id="ndf@inline")]])
def test_i4_no_matching_install_gives_a_reason(tmp_path, listed):
    got = plugin_update.find_origin(listed, tmp_path)
    assert isinstance(got, str) and got


def test_i4_two_scopes_pointing_at_the_running_root_are_not_chosen(tmp_path):
    got = plugin_update.find_origin([_row(tmp_path, scope="user"), _row(tmp_path, scope="project")], tmp_path)
    assert got == plugin_update.SHARED_PATH


# --- find_target（I8） ---


def test_i8_target_is_the_row_with_the_origin_id_and_scope(tmp_path):
    origin = Origin("ndf@ai-plugins", "ai-plugins", "project")
    listed = [_row(tmp_path / "u", scope="user", version="11.0.7"), _row(tmp_path / "p", scope="project", version="11.1.0")]
    assert plugin_update.find_target(listed, origin) == Target("11.1.0", tmp_path / "p")


@pytest.mark.parametrize(
    "listed",
    [None, [], [_row(Path("/p"), scope="project")], [{**_row(Path("/p")), "version": None}], [{**_row(Path("/p")), "installPath": 3}]],
)
def test_i8_unknown_target_when_the_row_is_missing_or_malformed(listed):
    origin = Origin("ndf@ai-plugins", "ai-plugins", "user")
    assert plugin_update.find_target(listed, origin) == Target(None, None)


# --- read_update（受け入れ条件 3） ---


def test_read_update_reads_the_measured_up_to_date_and_updated_rows():
    ok = Ran(0, MARKET_OK, "")
    assert plugin_update.read_update(ok, Ran(0, UP_TO_DATE, ""))[:3] == ("up_to_date", "11.0.7", "11.0.7")
    got = plugin_update.read_update(ok, Ran(0, UPDATED, ""))
    assert got[:3] == ("updated", "1.0.0", "1.0.1") and got.target == Target(None, None)


def test_read_update_reads_the_last_json_line_after_human_lines():
    got = plugin_update.read_update(Ran(0, f"Updating...\n{MARKET_OK}\n", ""), Ran(0, f"Checking for updates\n{UPDATED}\n", ""))
    assert got.result == "updated"


@pytest.mark.parametrize(
    "market",
    [Ran(1, '{"outcome":"failed","failureCode":"network"}', ""), Ran(None, "", "120 秒で終わらない"), Ran(0, '{"outcome":"failed"}', "")],
)
def test_market_failure_is_failed_without_reading_the_plugin_result(market):
    got = plugin_update.read_update(market, None)
    assert got.result == "failed" and (got.before, got.after) == (None, None) and got.reason


@pytest.mark.parametrize(
    "plugin",
    [
        Ran(1, NOT_FOUND, ""),
        Ran(None, "", "起動できない: FileNotFoundError"),
        Ran(0, "not json", ""),
        Ran(0, _with(UP_TO_DATE, outcome="failed"), ""),
        None,
    ],
)
def test_plugin_failure_is_failed(plugin):
    got = plugin_update.read_update(Ran(0, MARKET_OK, ""), plugin)
    assert got.result == "failed" and got.reason


def test_failure_reason_keeps_the_code_and_drops_paths():
    got = plugin_update.read_update(Ran(0, MARKET_OK, ""), Ran(1, NOT_FOUND, ""))
    assert "not_found" in got.reason and "/home/u" not in got.reason and "\n" not in got.reason


@pytest.mark.parametrize(
    ("payload", "before", "after"),
    [
        (_with(UP_TO_DATE, newVersion="11.0.8"), "11.0.7", "11.0.8"),
        (_with(UPDATED, newVersion="1.0.0"), "1.0.0", "1.0.0"),
        (_with(UPDATED, updateOutcome="downgraded"), "1.0.0", "1.0.1"),
        (_with(UP_TO_DATE, oldVersion=None), None, "11.0.7"),
    ],
)
def test_contradicting_or_unknown_results_are_failed_with_what_was_read(payload, before, after):
    got = plugin_update.read_update(Ran(0, MARKET_OK, ""), Ran(0, payload, ""))
    assert (got.result, got.before, got.after) == ("failed", before, after) and "読めない結果" in got.reason


# --- major_changed（I7） ---


@pytest.mark.parametrize(("version", "expected"), [("11.1.0", False), (None, False), ("12.0.0", True)])
def test_i7_major_changed_compares_only_the_major(version, expected):
    assert plugin_update.major_changed("11.0.7", Target(version, None)) is expected


# --- run_plugin・list_plugins（アダプター） ---


class _Done:
    def __init__(self, code: int, out: str) -> None:
        self.returncode, self.stdout = code, out


def test_run_plugin_runs_claude_plugin_in_the_given_cwd_and_env_without_stdin(tmp_path):
    seen = []

    def run(argv, **k):
        seen.append((argv, k))
        return _Done(0, UP_TO_DATE)

    got = claude.run_plugin(["update", "ndf@ai-plugins"], 120, {"A": "1"}, tmp_path, run=run)
    assert got == Ran(0, UP_TO_DATE, "")
    ((argv, k),) = seen
    assert argv == [claude.CLAUDE, "plugin", "update", "ndf@ai-plugins"]
    assert (k["cwd"], k["env"], k["timeout"], k["stdin"]) == (tmp_path, {"A": "1"}, 120, subprocess.DEVNULL)


def test_run_plugin_folds_launch_failure_and_timeout(tmp_path):
    def missing(*a, **k):
        raise FileNotFoundError(2, "No such file", "/opt/secret/claude")

    def slow(*a, **k):
        raise subprocess.TimeoutExpired("claude", 120)

    gone = claude.run_plugin(["update"], 120, {}, tmp_path, run=missing)
    assert gone.code is None and "起動できない" in gone.error and "/opt/secret" not in gone.error
    late = claude.run_plugin(["update"], 120, {}, tmp_path, run=slow)
    assert late == Ran(None, "", "120 秒で終わらない")


def test_list_plugins_passes_the_given_env(tmp_path):
    seen = []
    assert claude.list_plugins(lambda *a, **k: seen.append(k.get("env")) or _Done(0, "[]"), cwd=tmp_path, env={"A": "1"}) == []
    assert seen == [{"A": "1"}]


# --- Updater（受け入れ条件 1・3・6、I4・I5・I8） ---


class FakeCli:
    """`claude plugin list --json` と `claude plugin <args>` の代わり。打たれた順を `calls` に残す。"""

    def __init__(self, lists, outputs):
        self.lists = list(lists)
        self.outputs = list(outputs)
        self.calls: list[tuple] = []

    def listed(self, cwd=None, env=None):
        self.calls.append(("list", cwd, env))
        return self.lists.pop(0)

    def run(self, args, timeout, env, cwd):
        self.calls.append((tuple(args), cwd, env))
        return self.outputs.pop(0)

    def commands(self):
        return [c[0] for c in self.calls if c[0] != "list"]


def _updater(tmp_path, cli, timeout=5.0, lock=None):
    return plugin_update.Updater(
        root=tmp_path / "root",
        cwd=tmp_path / "work",
        env={"CLAUDE_CONFIG_DIR": str(tmp_path / "claude")},
        timeout=timeout,
        lock=lock or tmp_path / "claude" / "ndf" / "plugin-update.lock",
        run=cli.run,
        listed=cli.listed,
    )


def test_updater_reads_the_origin_once_and_runs_market_then_plugin_then_confirms(tmp_path):
    before, after = [_row(tmp_path / "root", scope="project")], [_row(tmp_path / "root" / "..", scope="project", version="11.0.8")]
    cli = FakeCli(
        [before, before, after, after, after], [Ran(0, MARKET_OK, ""), Ran(0, UPDATED, ""), Ran(0, MARKET_OK, ""), Ran(0, UP_TO_DATE, "")]
    )
    up = _updater(tmp_path, cli)
    first = up()
    assert (first.result, first.before, first.after) == ("updated", "1.0.0", "1.0.1")
    assert first.target == Target("11.0.8", tmp_path / "root" / "..")
    assert up().result == "up_to_date"
    market = ("marketplace", "update", "ai-plugins", "--json")
    plugin = ("update", "ndf@ai-plugins", "-y", "--json", "--scope", "project")
    # 導入元は錠の前に 1 回だけ決め、錠の中で更新の前に有効かを確かめ、更新の後ごとに起動先の一覧を読み直す
    assert [c[0] for c in cli.calls] == ["list", "list", market, plugin, "list", "list", market, plugin, "list"]
    # 一覧と 2 つのコマンドは同じ cwd・env で打つ
    assert {(c[1], json.dumps(c[2], sort_keys=True)) for c in cli.calls} == {(tmp_path / "work", json.dumps(up.env, sort_keys=True))}


def test_i4_updater_skips_without_commands_and_reads_again_next_time(tmp_path):
    cli = FakeCli([[_row(tmp_path / "dev")], *[[_row(tmp_path / "root")]] * 3], [Ran(0, MARKET_OK, ""), Ran(0, UP_TO_DATE, "")])
    up = _updater(tmp_path, cli)
    got = up()
    assert (got.result, got.reason, got.target) == ("skipped", plugin_update.NO_MATCH, Target(None, None)) and cli.commands() == []
    assert up().result == "up_to_date"


def test_updater_fails_without_commands_when_the_list_cannot_be_read(tmp_path):
    cli = FakeCli([None], [])
    got = _updater(tmp_path, cli)()
    assert got.result == "failed" and got.target == Target(None, None) and cli.commands() == []


def test_i8_updater_confirms_the_target_even_when_the_update_output_is_unreadable(tmp_path):
    row = _row(tmp_path / "root")
    cli = FakeCli([[row], [row], [{**row, "version": "12.0.0"}]], [Ran(0, MARKET_OK, ""), Ran(0, "garbled", "")])
    got = _updater(tmp_path, cli)()
    assert got.result == "failed" and got.target.version == "12.0.0"


def test_i5_held_lock_fails_after_the_limit_without_commands_but_confirms_the_target(tmp_path, hold_lock):
    lock = tmp_path / "claude" / "ndf" / "plugin-update.lock"
    fd = hold_lock(lock)
    try:
        cli = FakeCli([[_row(tmp_path / "root", version="11.0.7")], [_row(tmp_path / "root", version="11.0.9")]], [])
        got = _updater(tmp_path, cli, timeout=0.05, lock=lock)()
    finally:
        os.close(fd)
    assert got.result == "failed" and "0.05 秒待っても終わらない" in got.reason
    assert cli.commands() == [] and got.target.version == "11.0.9"


def test_updater_folds_a_lock_that_cannot_be_made_into_failed(tmp_path):
    (tmp_path / "file").write_text("", encoding="utf-8")
    cli = FakeCli([[_row(tmp_path / "root")]] * 2, [])
    got = _updater(tmp_path, cli, lock=tmp_path / "file" / "ndf" / "plugin-update.lock")()
    assert got.result == "failed" and got.target.version == "11.0.7" and cli.commands() == []


def test_confirm_reads_the_list_once_without_the_lock_or_commands(tmp_path, hold_lock):
    lock = tmp_path / "claude" / "ndf" / "plugin-update.lock"
    fd = hold_lock(lock)
    try:
        cli = FakeCli([[_row(tmp_path / "root", version="11.1.0")], None], [])
        up = _updater(tmp_path, cli, timeout=5.0, lock=lock)
        assert up.confirm() == Target("11.1.0", tmp_path / "root")
        assert up.confirm() == Target(None, None)
    finally:
        os.close(fd)
    assert [c[0] for c in cli.calls] == ["list", "list"]


def test_i4_origin_is_kept_before_the_lock_wait_and_followed_after_another_wrapper_moved_it(tmp_path):
    # 旧バージョンのラッパーが錠を待つ間に、先行のラッパーが同じ導入を次のメジャーへ上げた形:
    # 錠の前の一覧は旧い導入先を、錠の中の一覧は上げた後の導入先を返す
    old, new = _row(tmp_path / "root", version="11.0.7"), _row(tmp_path / "cache" / "12.0.0", version="12.0.0")
    cli = FakeCli([[old], [new], [new]], [Ran(0, MARKET_OK, ""), Ran(0, UP_TO_DATE, "")])
    up = _updater(tmp_path, cli)
    got = up()
    assert up.origin == Origin("ndf@ai-plugins", "ai-plugins", "user") and cli.commands()[1][-1] == "user"
    assert got.result == "up_to_date" and got.target == Target("12.0.0", tmp_path / "cache" / "12.0.0")


def test_i4_held_origin_that_was_disabled_is_neither_updated_nor_launched(tmp_path):
    row = _row(tmp_path / "root")
    off = [{**row, "enabled": False, "version": "12.0.0"}]
    cli = FakeCli([[row], off, off], [])
    up = _updater(tmp_path, cli)
    got = up()  # 錠の前は有効、錠の中では無効になっている
    assert (got.result, got.reason, got.target) == ("skipped", plugin_update.ORIGIN_GONE, Target(None, None)) and cli.commands() == []
    assert up.confirm() == Target(None, None)


def test_i8_target_ignores_a_disabled_row(tmp_path):
    origin = Origin("ndf@ai-plugins", "ai-plugins", "user")
    assert plugin_update.find_target([_row(tmp_path, version="12.0.0", enabled=False)], origin) == Target(None, None)


# --- tool_env・wrapper_settings ---


def test_tool_env_drops_child_variables_but_keeps_the_config_dir(monkeypatch):
    from ndf.domains.relay import session_env

    environ = {
        "CLAUDECODE": "1",
        "UV_PROJECT_ENVIRONMENT": "/v",
        "CLAUDE_CONFIG_DIR": "/c",
        "PYTHONPATH": os.pathsep.join([str(session_env.NDF_SRC), "/mine"]),
        "PATH": "/bin",
    }
    got = session_env.tool_env(environ)
    assert got == {"CLAUDE_CONFIG_DIR": "/c", "PYTHONPATH": "/mine", "PATH": "/bin"}


def test_wrapper_settings_hand_the_updater_and_its_confirm(tmp_path, monkeypatch):
    from ndf.domains.relay import cli

    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.chdir(tmp_path)
    s = cli.wrapper_settings("w1", [])
    up = s.update
    assert isinstance(up, plugin_update.Updater) and s.confirm == up.confirm
    assert up.lock == tmp_path / "claude" / "ndf" / "plugin-update.lock"
    assert (up.cwd, up.timeout) == (tmp_path, 120) and "CLAUDECODE" not in up.env
    assert up.env["CLAUDE_CONFIG_DIR"] == str(tmp_path / "claude")
