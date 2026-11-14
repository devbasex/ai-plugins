from __future__ import annotations

import json
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from ndf import cli
from ndf.contracts import causes

PLUGIN_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def broken_root(tmp_path, monkeypatch):
    root = tmp_path / "broken"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text("{")
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))
    return root


def test_no_args_prints_usage(capsys):
    assert cli.main([]) == 0
    assert "使い方" in capsys.readouterr().out


def test_unknown_subcommand_is_a_usage_error(capsys):
    assert cli.main(["no-such-command"]) == causes.exit_code("invalid")
    assert "no-such-command" in capsys.readouterr().err


def test_dispatches_to_module_main(monkeypatch):
    calls = []
    fake = types.ModuleType("fake_sub")
    fake.main = lambda argv: calls.append(argv) or 7
    monkeypatch.setitem(sys.modules, "fake_sub", fake)
    monkeypatch.setitem(cli.COMMANDS, "fake", "fake_sub")

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
def test_entrypoint_does_not_import_from_callers_directory(tmp_path):
    # 呼び出し元のディレクトリに置かれた同じ名前のパッケージを読まない
    fake = tmp_path / "ndf"
    fake.mkdir()
    (fake / "__init__.py").write_text("")
    (fake / "__main__.py").write_text("print('HIJACKED')\n")

    done = subprocess.run([str(PLUGIN_ROOT / "bin" / "ndf")], cwd=tmp_path, capture_output=True, text=True, check=False)

    assert "HIJACKED" not in done.stdout
    assert done.returncode == 0
