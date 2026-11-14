from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from ndf import cli, identity
from ndf.contracts import causes
from ndf.domains.relay import accounts
from ndf.testing import isolation

PLUGIN_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def broken_root(tmp_path, monkeypatch):
    root = tmp_path / "broken"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text("{")
    monkeypatch.setattr(identity, "ROOT", root)
    return root


@pytest.fixture
def state_home(tmp_path_factory, monkeypatch):
    """状態の置き場を一時ディレクトリへ向け、本物の置き場への書き込みを落とす（`ndf.testing.isolation`）。新しい状態の置き場を返す。"""
    roots = isolation.protected_roots()
    base = tmp_path_factory.mktemp("ndf-state")
    for name, sub in isolation.REDIRECTED_ENV.items():
        monkeypatch.setenv(name, str(base / sub))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(base / "claude"))
    for name in isolation.REMOVED_ENV:
        monkeypatch.delenv(name, raising=False)
    with isolation.guarding(roots, (str(tmp_path_factory.getbasetemp()),)) as found:
        yield identity.load().state_dir
    assert not found, f"テストが本物の状態の置き場へ書こうとした: {found}"


def test_writer_through_the_entry_leaves_a_prepared_state_dir(state_home, last_result, capsys):
    # #3432 のレビュー: 状態の置き場へ書く副命令をエントリポイントから打った後も、置き場は用意済み（format.json がある）で、
    # 続けて用意しても新しい記録を旧い置き場と見なして退避しない
    assert cli.main(["relay", "account", "use", "work"]) == 0
    assert last_result(capsys.readouterr().out)["status"] == "ok"

    assert (state_home / identity.FORMAT_FILE).exists()
    assert identity.prepare_state_dir(identity.load()).retired == ()
    assert accounts.pinned(state_home) == "work"


def test_entry_writes_the_retired_places_to_stderr(state_home, monkeypatch, capsys):
    # 旧い置き場を退避したら、元と先を標準エラーへ 1 行ずつ書き、副命令はそのまま呼ぶ
    state_home.mkdir(parents=True)
    (state_home / "ledger.jsonl").write_text("old")
    calls = []
    _fake_subcommand(monkeypatch, lambda argv: calls.append(argv) or 0)

    assert cli.main(["fake"]) == 0

    assert calls == [[]]
    [moved] = [p for p in state_home.parent.iterdir() if p.name.startswith(state_home.name + identity.RETIRED_SUFFIX)]
    assert (moved / "ledger.jsonl").read_text() == "old"
    line = identity.RETIRED_LINE.format(retired_from=state_home, retired_to=moved)
    assert line in capsys.readouterr().err.splitlines()


def test_entry_stops_without_calling_the_subcommand_when_preparing_fails(state_home, monkeypatch, capsys, last_result):
    # 用意できなければ（ほかのプラグインの根から呼ばれ、旧い置き場を退避してよいと確かめられない）副命令を呼ばずに止まる
    state_home.mkdir(parents=True)
    (state_home / "ledger.jsonl").write_text("old")
    monkeypatch.setenv("NDF_CALLER_PLUGIN_ROOT", str(state_home.parent / "other-plugin"))
    calls = []
    _fake_subcommand(monkeypatch, lambda argv: calls.append(argv) or 0)

    assert cli.main(["fake"]) == causes.exit_code("config_broken")

    assert calls == []
    report = last_result(capsys.readouterr().out)
    assert (report["tool"], report["status"], report["cause"]["kind"]) == (cli.TOOL, "stopped", "config_broken")
    assert (state_home / "ledger.jsonl").read_text() == "old"


def test_entry_keeps_the_places_retired_before_preparing_failed(monkeypatch, capsys, last_result):
    # 退避の途中か後で失敗しても、それまでに退避した元と先を標準エラーと items に残す
    retired = (identity.Retired(Path("/s/p"), Path("/s/p.v10-20261114")),)

    def fail(ident):
        raise identity.IdentityError("read-only", retired)

    monkeypatch.setattr(identity, "prepare_state_dir", fail)
    _fake_subcommand(monkeypatch, lambda argv: pytest.fail("副命令を呼んだ"))

    assert cli.main(["fake"]) == causes.exit_code("config_broken")

    out = capsys.readouterr()
    assert last_result(out.out)["items"] == [{"retired_from": "/s/p", "retired_to": "/s/p.v10-20261114"}]
    assert identity.RETIRED_LINE.format(retired_from="/s/p", retired_to="/s/p.v10-20261114") in out.err.splitlines()


def test_usage_and_unknown_subcommand_do_not_touch_the_state_dir(state_home, capsys):
    assert cli.main([]) == 0
    assert cli.main(["--help"]) == 0
    assert cli.main(["no-such-command"]) == causes.exit_code("invalid")
    assert not state_home.parent.exists()


def test_no_args_prints_usage(capsys):
    assert cli.main([]) == 0
    assert "使い方" in capsys.readouterr().out


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
    """引数を argparse で読むだけの偽の副命令の main。"""
    argparse.ArgumentParser(prog="fake").parse_args(argv)
    return 0


def test_uncaught_exception_ends_with_an_unknown_result(monkeypatch, capsys, last_result):
    # 3394-3: 副命令が捕まえない例外も、結果 JSON（stopped/unknown）で終わる
    def boom(argv):
        raise RuntimeError("壊れた")

    _fake_subcommand(monkeypatch, boom)

    assert cli.main(["fake"]) == causes.exit_code("unknown")
    report = last_result(capsys.readouterr().out)
    assert (report["tool"], report["status"], report["cause"]["kind"]) == ("fake", "stopped", "unknown")
    assert "RuntimeError" in report["cause"]["detail"]


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


def test_entrypoint_stops_when_plugin_json_is_unreadable(tmp_path):
    root = tmp_path / "plugin"
    (root / "bin").mkdir(parents=True)
    shutil.copy(PLUGIN_ROOT / "bin" / "ndf", root / "bin" / "ndf")

    done = subprocess.run([str(root / "bin" / "ndf")], capture_output=True, text=True, check=False)

    assert done.returncode == 2
    assert "plugin.json" in done.stderr


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv が要る")
def test_entrypoint_is_found_through_a_link_on_path(tmp_path):
    # 3366-4: PATH の上のリンクから呼んでも、リンクをたどって実体の根を求める
    link_dir = tmp_path / "bin"
    link_dir.mkdir()
    (link_dir / "ndf").symlink_to(PLUGIN_ROOT / "bin" / "ndf")
    env = {**os.environ, "PATH": f"{link_dir}{os.pathsep}{os.environ['PATH']}"}

    done = subprocess.run(["ndf"], cwd=tmp_path, env=env, capture_output=True, text=True, check=False)

    assert done.returncode == 0
    assert "使い方" in done.stdout


def _path_of(tmp_path, commands):
    """`commands` だけへのリンクを置いた PATH のディレクトリ。"""
    path_dir = tmp_path / "path"
    path_dir.mkdir()
    for cmd in commands:
        found = shutil.which(cmd)
        assert found is not None
        (path_dir / cmd).symlink_to(found)
    return path_dir


def test_entrypoint_without_uv_or_curl_stops_with_deps_missing(tmp_path, last_result):
    # 要求の 316 行: uv が無く入れられないときは、理由を結果 JSON で出して止まる
    path_dir = _path_of(tmp_path, ("bash", "python3", "dirname", "readlink", "env", "tail", "mkdir"))
    env = {"HOME": str(tmp_path / "home"), "XDG_CACHE_HOME": str(tmp_path / "cache"), "PATH": str(path_dir)}

    done = subprocess.run([str(PLUGIN_ROOT / "bin" / "ndf")], env=env, capture_output=True, text=True, check=False)

    assert done.returncode == causes.exit_code("deps_missing")
    report = last_result(done.stdout)
    assert (report["status"], report["cause"]["kind"]) == ("stopped", "deps_missing")
    assert "uv" in report["cause"]["detail"]


def test_entrypoint_does_not_run_an_installer_whose_hash_differs(tmp_path, last_result):
    # 取ったインストーラは、埋めたハッシュと照合してから実行する。違えば実行せず、一時の置き場も残さない
    path_dir = _path_of(tmp_path, ("bash", "python3", "dirname", "readlink", "env", "tail", "mkdir", "mktemp", "rm", "mv", "sh"))
    mark = tmp_path / "ran"
    curl = path_dir / "curl"
    curl.write_text(
        '#!/bin/sh\nwhile [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift ;; esac; shift; done\nprintf \': > "%s"\\n\' "$MARK" > "$out"\n'
    )
    curl.chmod(0o755)
    cache = tmp_path / "cache"
    env = {"HOME": str(tmp_path / "home"), "XDG_CACHE_HOME": str(cache), "PATH": str(path_dir), "MARK": str(mark)}

    done = subprocess.run([str(PLUGIN_ROOT / "bin" / "ndf")], env=env, capture_output=True, text=True, check=False)

    assert done.returncode == causes.exit_code("deps_missing")
    report = last_result(done.stdout)
    assert report["cause"]["kind"] == "deps_missing"
    assert "ハッシュ" in report["cause"]["detail"]
    assert not mark.exists()
    name = identity.load().name
    assert list((cache / name / "uv").iterdir()) == []


def test_entrypoint_passes_the_callers_plugin_root_on(tmp_path):
    # 子へ自分の根を渡す前に、呼び出し元の CLAUDE_PLUGIN_ROOT を NDF_CALLER_PLUGIN_ROOT へ写す
    fake_dir = tmp_path / "fake"
    fake_dir.mkdir()
    uv = fake_dir / "uv"
    uv.write_text('#!/bin/sh\nprintf \'%s\\n%s\\n\' "$NDF_CALLER_PLUGIN_ROOT" "$CLAUDE_PLUGIN_ROOT"\n')
    uv.chmod(0o755)
    other = tmp_path / "other-plugin"
    env = {**os.environ, "PATH": f"{fake_dir}{os.pathsep}{os.environ['PATH']}", "CLAUDE_PLUGIN_ROOT": str(other)}

    done = subprocess.run([str(PLUGIN_ROOT / "bin" / "ndf")], env=env, capture_output=True, text=True, check=False)

    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines() == [str(other), str(PLUGIN_ROOT)]


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv が要る")
def test_entrypoint_does_not_import_from_callers_directory(tmp_path):
    # 呼び出し元のディレクトリに置かれた同じ名前のパッケージを読まない
    fake = tmp_path / "ndf"
    fake.mkdir()
    (fake / "__init__.py").write_text("")
    (fake / "__main__.py").write_text("print('HIJACKED')\n")

    done = subprocess.run([str(PLUGIN_ROOT / "bin" / "ndf")], cwd=tmp_path, capture_output=True, text=True, check=False)

    assert "HIJACKED" not in done.stdout
    assert done.returncode == 0


def test_every_subcommand_reports_its_table_name_as_tool():
    # 3394-11: 結果 JSON の tool は副命令の表の名前を 1 つの求め方で引く
    import importlib

    for name, module in cli.COMMANDS.items():
        assert cli.tool_of(module) == name
        assert importlib.import_module(module).TOOL == name


def test_tool_of_rejects_a_module_outside_the_table():
    with pytest.raises(LookupError):
        cli.tool_of("ndf.no_such_command")
