from __future__ import annotations

import argparse
import json
import sys
import types
from pathlib import Path

import pytest

from ndf import cli, identity
from ndf.contracts import causes, result
from ndf.contracts.internal import InternalError
from ndf.core.plan import records
from ndf.domains.relay import accounts
from ndf.testing import isolation


@pytest.fixture
def broken_root(tmp_path, monkeypatch):
    root = tmp_path / "broken"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text("{")
    monkeypatch.setattr(identity, "ROOT", root)
    return root


@pytest.fixture
def state_home(tmp_path_factory, monkeypatch):
    """状態の保存先を一時ディレクトリへ向け、本物の保存先への書き込みを落とす（`ndf.testing.isolation`）。新しい状態の保存先を返す。"""
    roots = isolation.protected_roots()
    base = tmp_path_factory.mktemp("ndf-state")
    for name, sub in isolation.REDIRECTED_ENV.items():
        monkeypatch.setenv(name, str(base / sub))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(base / "claude"))
    for name in isolation.REMOVED_ENV:
        monkeypatch.delenv(name, raising=False)
    with isolation.guarding(roots, (str(tmp_path_factory.getbasetemp()),)) as found:
        yield identity.load().state_dir
    assert not found, f"テストが本物の状態の保存先へ書こうとした: {found}"


def test_writer_through_the_entry_leaves_a_prepared_state_dir(state_home, last_result, capsys):
    # #3432 のレビュー: 状態の保存先へ書くサブコマンドをエントリポイントから打った後も、保存先は用意済み（format.json がある）で、
    # 続けて用意しても新しい記録を旧い保存先と見なして退避しない
    assert cli.main(["relay", "account", "use", "work"]) == 0
    assert last_result(capsys.readouterr().out)["status"] == "ok"

    assert (state_home / identity.FORMAT_FILE).exists()
    assert identity.prepare_state_dir(identity.load()).retired == ()
    assert accounts.pinned(state_home) == "work"


def test_entry_writes_the_retired_locations_to_stderr(state_home, monkeypatch, capsys):
    # 旧い保存先を退避したら、元と先を標準エラーへ 1 行ずつ書き、サブコマンドはそのまま呼ぶ
    state_home.mkdir(parents=True)
    (state_home / "journal.jsonl").write_text("old")
    calls = []
    _fake_subcommand(monkeypatch, lambda argv: calls.append(argv) or 0)

    assert cli.main(["fake"]) == 0

    assert calls == [[]]
    [moved] = [p for p in state_home.parent.iterdir() if p.name.startswith(state_home.name + identity.RETIRED_SUFFIX)]
    assert (moved / "journal.jsonl").read_text() == "old"
    line = identity.RETIRED_LINE.format(retired_from=state_home, retired_to=moved)
    assert line in capsys.readouterr().err.splitlines()


def test_entry_stops_without_calling_the_subcommand_when_preparing_fails(state_home, monkeypatch, capsys, last_result):
    # 用意できなければ（ほかのプラグインの根から呼ばれ、旧い保存先を退避してよいと確かめられない）サブコマンドを呼ばずに止まる
    state_home.mkdir(parents=True)
    (state_home / "journal.jsonl").write_text("old")
    monkeypatch.setenv("NDF_CALLER_PLUGIN_ROOT", str(state_home.parent / "other-plugin"))
    calls = []
    _fake_subcommand(monkeypatch, lambda argv: calls.append(argv) or 0)

    assert cli.main(["fake"]) == causes.exit_code("config_broken")

    assert calls == []
    report = last_result(capsys.readouterr().out)
    assert (report["tool"], report["status"], report["cause"]["kind"]) == (cli.TOOL, "stopped", "config_broken")
    assert (state_home / "journal.jsonl").read_text() == "old"


def test_entry_keeps_the_locations_retired_before_preparing_failed(monkeypatch, capsys, last_result):
    # 退避の途中か後で失敗しても、それまでに退避した元と先を標準エラーと items に残す
    retired = (identity.Retired(Path("/s/p"), Path("/s/p.v10-20261114")),)

    def fail(ident):
        raise identity.IdentityError("read-only", retired)

    monkeypatch.setattr(identity, "prepare_state_dir", fail)
    _fake_subcommand(monkeypatch, lambda argv: pytest.fail("サブコマンドを呼んだ"))

    assert cli.main(["fake"]) == causes.exit_code("config_broken")

    out = capsys.readouterr()
    assert last_result(out.out)["items"] == [{"retired_from": "/s/p", "retired_to": "/s/p.v10-20261114"}]
    assert identity.RETIRED_LINE.format(retired_from="/s/p", retired_to="/s/p.v10-20261114") in out.err.splitlines()


@pytest.mark.parametrize(("kind", "reason"), [("store_in_use", "legacy_running"), ("precondition", "retire_target_exists")])
def test_entry_stops_with_the_kind_and_reason_the_preparation_raised(monkeypatch, capsys, last_result, kind, reason):
    # 旧いラッパーが動いている・退避先がある、で止まったときは例外の停止理由とパスを結果へ写す
    def fail(ident):
        raise identity.IdentityError("止まった", kind=kind, reason=reason, paths=(Path("/s/p/relay/w/relay.lock"),))

    monkeypatch.setattr(identity, "prepare_state_dir", fail)
    _fake_subcommand(monkeypatch, lambda argv: pytest.fail("サブコマンドを呼んだ"))

    assert cli.main(["fake"]) == causes.exit_code(kind)

    out = capsys.readouterr()
    report = last_result(out.out)
    assert (report["status"], report["cause"]["kind"]) == ("stopped", kind)
    assert report["items"] == [{"reason": reason, "path": "/s/p/relay/w/relay.lock"}]
    assert "退避した" not in out.err


def test_usage_and_unknown_subcommand_do_not_touch_the_state_dir(state_home, capsys):
    assert cli.main([]) == 0
    assert cli.main(["--help"]) == 0
    assert cli.main(["no-such-command"]) == causes.exit_code("invalid")
    assert not state_home.parent.exists()


def test_no_args_prints_usage(capsys):
    assert cli.main([]) == 0
    assert "使い方" in capsys.readouterr().out


def test_usage_lists_user_commands_but_not_developer_commands(capsys, monkeypatch):
    """使い方の表示に NDF 自身の開発のサブコマンド（check・manifests）を出さず、名前で呼べば動く（#3617 の I11・I11a）。"""
    assert cli.main(["--help"]) == 0
    listed = {line.strip() for line in capsys.readouterr().out.splitlines() if line.startswith("  ")}
    assert listed == set(cli.COMMANDS) - cli.DEVELOPER_COMMANDS
    assert {"hooks", "worktree"} <= listed
    calls = []
    fake = types.ModuleType("fake_check")
    fake.main = lambda argv: calls.append(argv) or 0
    monkeypatch.setitem(sys.modules, "fake_check", fake)
    monkeypatch.setitem(cli.COMMANDS, "check", "fake_check")
    assert cli.main(["check", "--x"]) == 0
    assert calls == [["--x"]]


def test_unknown_subcommand_is_a_usage_error(capsys, last_result):
    assert cli.main(["no-such-command"]) == causes.exit_code("invalid")
    out = capsys.readouterr()
    assert "no-such-command" in out.err
    assert last_result(out.out)["cause"]["kind"] == "invalid"


def _fake_subcommand(monkeypatch, main):
    fake = types.ModuleType("fake_sub")
    fake.main = main
    monkeypatch.setitem(sys.modules, "fake_sub", fake)
    monkeypatch.setitem(cli.COMMANDS, "fake", "fake_sub")


def _argparse_main(argv):
    """引数を argparse で読むだけの偽のサブコマンドの main。"""
    argparse.ArgumentParser(prog="fake").parse_args(argv)
    return 0


def test_uncaught_exception_ends_with_an_unknown_result(monkeypatch, capsys, last_result):
    # 3394-3: サブコマンドが捕まえない例外も、結果 JSON（stopped/unknown）で終わる
    def boom(argv):
        raise RuntimeError("壊れた")

    _fake_subcommand(monkeypatch, boom)

    assert cli.main(["fake"]) == causes.exit_code("unknown")
    report = last_result(capsys.readouterr().out)
    assert (report["tool"], report["status"], report["cause"]["kind"]) == ("fake", "stopped", "unknown")
    assert "RuntimeError" in report["cause"]["detail"]


@pytest.mark.parametrize("kind", ["invalid", "config_broken", "precondition"])
def test_internal_data_error_ends_with_a_stopped_result_of_its_kind(monkeypatch, capsys, last_result, kind):
    # #2339 の受け入れ条件 1: 内部データの形が違う・種類が無い・新しいバージョンは stopped で終わる
    def refuse(argv):
        raise InternalError(kind, "内部データの形が違う")

    _fake_subcommand(monkeypatch, refuse)

    assert cli.main(["fake"]) == causes.exit_code(kind)
    report = last_result(capsys.readouterr().out)
    assert (report["tool"], report["status"], report["cause"]) == ("fake", "stopped", {"kind": kind, "detail": "内部データの形が違う"})


def test_a_newer_stored_result_is_told_to_the_outer_plan(monkeypatch, capsys, last_result, tmp_path):
    # PR 4358 の r6-1: 新しいバージョンの保存済みの結果で止まった結果を、外のプランがふつうの precondition と見分ける
    path = tmp_path / "r.json"
    path.write_text('{"version": 2, "status": "ok"}', encoding="utf-8")
    _fake_subcommand(monkeypatch, lambda argv: records.read_result(path))

    assert cli.main(["fake"]) == causes.exit_code("precondition")
    assert result.stopped_by_newer(last_result(capsys.readouterr().out))


def test_usage_error_of_a_subcommand_ends_with_an_invalid_result(monkeypatch, capsys, last_result):
    # 3394-3: argparse の使い方の誤り（終了コード 2 の SystemExit）も、結果 JSON（stopped/invalid）で終わる
    _fake_subcommand(monkeypatch, _argparse_main)

    assert cli.main(["fake", "--bogus"]) == causes.exit_code("invalid")
    out = capsys.readouterr()
    assert last_result(out.out)["cause"]["kind"] == "invalid"
    assert "--bogus" in out.err


def test_help_of_a_subcommand_is_not_a_result(monkeypatch, capsys):
    _fake_subcommand(monkeypatch, _argparse_main)

    with pytest.raises(SystemExit) as exc:
        cli.main(["fake", "--help"])
    assert exc.value.code == 0


def test_dispatches_to_module_main(monkeypatch):
    calls = []
    _fake_subcommand(monkeypatch, lambda argv: calls.append(argv) or 7)

    assert cli.main(["fake", "--x", "1"]) == 7
    assert calls == [["--x", "1"]]


def test_broken_plugin_json_stops_with_config_broken(broken_root, capsys):
    assert cli.main([]) == causes.exit_code("config_broken")
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "stopped"
    assert report["cause"]["kind"] == "config_broken"
    assert "plugin.json" in report["cause"]["detail"]


def test_every_subcommand_reports_its_table_name_as_tool():
    # 3394-11: 結果 JSON の tool はサブコマンドの表の名前を 1 つの求め方で引く
    import importlib

    for name, module in cli.COMMANDS.items():
        assert cli.tool_of(module) == name
        assert importlib.import_module(module).TOOL == name


def test_tool_of_rejects_a_module_outside_the_table():
    with pytest.raises(LookupError):
        cli.tool_of("ndf.no_such_command")
