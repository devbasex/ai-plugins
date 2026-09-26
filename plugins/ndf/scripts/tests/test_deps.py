"""外部パッケージの require()（lib/deps.py・#1142 の決定 17・不足 h）。

uv は一時の PATH に置いた偽物で差し替える。偽物は受けた引数と環境を記録して終わる（`os.execve` の後は偽物の終了コード）。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1] / "lib"
PLUGIN = LIB.parents[1]
sys.path.insert(0, str(LIB))
import deps  # noqa: E402

FAKE_UV = """#!/bin/sh
{ printf '%s\\n' "$@"; echo "REEXEC=$NDF_DEPS_REEXEC"; echo "VENV=$UV_PROJECT_ENVIRONMENT"; } > "$FAKE_UV_LOG"
exit 7
"""


def entry(tmp_path: Path, modules: list[str]) -> Path:
    script = tmp_path / "entry.py"
    script.write_text(
        f"import sys\nsys.path.insert(0, {str(LIB)!r})\nimport deps\n"
        f"deps.GROUPS['t'] = {modules!r}\ndeps.require('t')\nprint('after', sys.argv[1:])\n")
    return script


def run_entry(tmp_path: Path, modules: list[str], *, uv: bool, **env: str) -> subprocess.CompletedProcess:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    if uv:
        (bindir / "uv").write_text(FAKE_UV)
        (bindir / "uv").chmod(0o755)
    base = {k: v for k, v in os.environ.items() if k not in ("NDF_DEPS_REEXEC", "UV_PROJECT_ENVIRONMENT")}
    base.update(PATH=str(bindir), HOME=str(tmp_path / "home"), FAKE_UV_LOG=str(tmp_path / "uv.log"),
                NDF_DEPS_VENV=str(tmp_path / "venv"), **env)
    return subprocess.run([sys.executable, str(entry(tmp_path, modules)), "a1", "--x"],
                          capture_output=True, text=True, env=base)


def test_an_importable_group_returns_without_reexec(tmp_path):
    p = run_entry(tmp_path, ["json"], uv=True)
    assert p.returncode == 0 and "after ['a1', '--x']" in p.stdout
    assert not (tmp_path / "uv.log").exists()


def test_reexec_through_uv_with_the_lock_and_the_venv_outside_the_plugin(tmp_path):
    p = run_entry(tmp_path, ["ndf_no_such_module"], uv=True)
    assert p.returncode == 7, p.stderr
    log = (tmp_path / "uv.log").read_text().splitlines()
    assert log[:9] == ["run", "--quiet", "--frozen", "--project", str(PLUGIN), "--extra", "t", "python",
                       str((tmp_path / "entry.py").resolve())]
    assert log[9:11] == ["a1", "--x"]
    assert "REEXEC=1" in log and f"VENV={tmp_path / 'venv'}" in log


def test_after_reexec_a_missing_package_stops_with_code_3(tmp_path):
    p = run_entry(tmp_path, ["ndf_no_such_module"], uv=True, NDF_DEPS_REEXEC="1")
    assert p.returncode == 3 and "after" not in p.stdout
    assert "import できない" in p.stderr
    assert not (tmp_path / "uv.log").exists()


def test_uv_is_installed_when_missing_then_reexec(monkeypatch, tmp_path):
    monkeypatch.setattr(deps, "find_uv", lambda: None)
    monkeypatch.setattr(deps, "install_uv", lambda: "/opt/uv")
    monkeypatch.delenv("NDF_DEPS_REEXEC", raising=False)
    monkeypatch.setitem(deps.GROUPS, "t", ["ndf_no_such_module"])
    calls = []

    def fake_execve(path, argv, env):
        calls.append((path, argv, env))
        raise SystemExit(0)
    monkeypatch.setattr(deps.os, "execve", fake_execve)
    with pytest.raises(SystemExit):
        deps.require("t")
    path, argv, env = calls[0]
    assert path == "/opt/uv" and argv[:3] == ["/opt/uv", "run", "--quiet"] and env["NDF_DEPS_REEXEC"] == "1"


def test_uv_that_cannot_be_installed_stops_with_the_manual_command(monkeypatch, capsys):
    monkeypatch.setattr(deps, "find_uv", lambda: None)
    monkeypatch.setattr(deps, "install_uv", lambda: None)
    monkeypatch.delenv("NDF_DEPS_REEXEC", raising=False)
    monkeypatch.setitem(deps.GROUPS, "t", ["ndf_no_such_module"])
    with pytest.raises(SystemExit) as e:
        deps.require("t")
    assert e.value.code == 3
    assert deps.INSTALL_HINT in capsys.readouterr().err


def test_install_uv_uses_pip_without_curl(monkeypatch):
    calls = []
    monkeypatch.setattr(deps.shutil, "which", lambda name: None)
    monkeypatch.setattr(deps.subprocess, "run", lambda cmd, **kw: calls.append((cmd, kw["env"])))
    monkeypatch.setattr(deps, "find_uv", lambda: None)
    assert deps.install_uv() is None
    cmd, env = calls[0]
    assert cmd[1:] == ["-m", "pip", "install", "--user", "--quiet", f"uv=={deps.UV_VERSION}"]
    assert env["UV_NO_MODIFY_PATH"] == "1"


def test_install_uv_uses_the_pinned_installer_with_curl(monkeypatch):
    calls = []
    monkeypatch.setattr(deps.shutil, "which", lambda name: "/usr/bin/curl" if name == "curl" else None)
    monkeypatch.setattr(deps.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    monkeypatch.setattr(deps, "find_uv", lambda: "/home/u/.local/bin/uv")
    assert deps.install_uv() == "/home/u/.local/bin/uv"
    assert calls[0] == ["sh", "-c", f"curl -LsSf https://astral.sh/uv/{deps.UV_VERSION}/install.sh | sh"]


def test_find_uv_looks_in_local_bin(monkeypatch, tmp_path):
    monkeypatch.setattr(deps.shutil, "which", lambda name: None)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert deps.find_uv() is None
    uv = tmp_path / ".cargo" / "bin" / "uv"
    uv.parent.mkdir(parents=True)
    uv.write_text("#!/bin/sh\n")
    uv.chmod(0o755)
    assert deps.find_uv() == str(uv)


def test_an_unknown_group_is_a_programming_error():
    with pytest.raises(ValueError):
        deps.require("aws-not-declared")


def test_the_venv_is_named_by_the_plugin_version(monkeypatch, tmp_path):
    monkeypatch.delenv("NDF_DEPS_VENV", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    version = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())["version"]
    assert deps.venv_dir() == str(tmp_path / ".cache" / "ndf" / "venv" / version)
    assert deps.venv_version(tmp_path) == "dev"


def test_groups_match_the_declaration_and_the_lock():
    """require() のグループは pyproject.toml の extra と同じで、版は uv.lock が固定する。"""
    text = (PLUGIN / "pyproject.toml").read_text()
    section = text.split("[project.optional-dependencies]", 1)[1].split("\n[", 1)[0]
    declared = dict(re.findall(r'^(\w[\w-]*)\s*=\s*\[(.*?)\]', section, re.M))
    assert set(declared) == set(deps.GROUPS)
    lock = (PLUGIN / "uv.lock").read_text()
    for pkgs in declared.values():
        for name, ver in re.findall(r'"([\w-]+)==([\w.]+)"', pkgs):
            assert f'name = "{name}"\nversion = "{ver}"' in lock
