"""エントリポイントが起こす `bin/start.py` が、依存の環境の欠けを確かめて止まる（#2447 の残り）。"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from ndf import identity
from ndf.contracts import causes

ROOT = identity.ROOT


@pytest.fixture
def start(tmp_path, monkeypatch):
    """`bin/start.py` を読み、確かめた印の置き場を一時の置き場へ向けた module。"""
    spec = importlib.util.spec_from_file_location("_ndf_start", ROOT / "bin" / "start.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "STAMP", tmp_path / "venv" / ".deps-checked")
    (tmp_path / "venv").mkdir()
    return module


class _Dist:
    def __init__(self, base: Path, name: str, files: list[str]):
        self.base, self.metadata, self.files = base, {"Name": name}, files

    def read_text(self, name):
        return "".join(f"{f},sha256=x,1\n" for f in self.files) if name == "RECORD" else None

    def locate_file(self, f):
        return self.base / f


def test_a_distribution_with_missing_files_stops_with_the_reinstall_steps(start, tmp_path, monkeypatch, capsys):
    (tmp_path / "ok.py").write_text("", encoding="utf-8")
    dists = [_Dist(tmp_path, "whole", ["ok.py", "gone.pyc"]), _Dist(tmp_path, "broken", ["ok.py", "gone.py", "gone2.py"])]
    monkeypatch.setattr(start.metadata, "distributions", lambda: dists)
    ran = []
    monkeypatch.setattr(start.runpy, "run_module", lambda *a, **k: ran.append(a))

    assert start.main() == causes.exit_code("deps_missing")

    report = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert (report["status"], report["cause"]["kind"]) == ("stopped", "deps_missing")
    assert "broken（2 件）" in report["cause"]["detail"] and "whole" not in report["cause"]["detail"]
    assert "uv sync --frozen --no-dev --reinstall" in report["cause"]["detail"]
    assert ran == [] and not start.STAMP.exists()


def test_a_whole_environment_is_checked_once_until_the_lock_changes(start, monkeypatch):
    calls = []
    monkeypatch.setattr(start.metadata, "distributions", lambda: calls.append(1) or [])
    ran = []
    monkeypatch.setattr(start.runpy, "run_module", lambda *a, **k: ran.append(a))

    assert start.main() == 0
    assert start.main() == 0
    assert len(calls) == 1 and len(ran) == 2 and start.STAMP.exists()
    lock = (ROOT / "uv.lock").stat().st_mtime
    os.utime(start.STAMP, (lock - 10, lock - 10))
    assert start.main() == 0
    assert len(calls) == 2


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv が要る（CI は setup-uv で入れる）")
def test_the_entrypoint_stops_when_a_file_of_the_environment_is_gone(tmp_path):
    cache = tmp_path / "cache"
    env = {**os.environ, "XDG_CACHE_HOME": str(cache), "UV_CACHE_DIR": os.environ.get("UV_CACHE_DIR") or str(Path.home() / ".cache" / "uv")}
    first = subprocess.run([str(ROOT / "bin" / "ndf")], env=env, capture_output=True, text=True, check=False, timeout=300)
    assert first.returncode == 0, first.stderr
    manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    venv = cache / manifest["name"] / "venv" / manifest["version"]
    stamp = venv / ".deps-checked"
    assert stamp.exists()
    victim = next(venv.glob("lib/python*/site-packages/tabulate/__init__.py"))
    victim.unlink()
    stamp.unlink()

    done = subprocess.run([str(ROOT / "bin" / "ndf")], env=env, capture_output=True, text=True, check=False, timeout=300)

    assert done.returncode == causes.exit_code("deps_missing")
    report = json.loads(done.stdout.strip().splitlines()[-1])
    assert report["cause"]["kind"] == "deps_missing" and "tabulate" in report["cause"]["detail"]
