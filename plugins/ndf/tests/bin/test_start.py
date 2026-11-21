"""エントリポイントが起動する `bin/start.py` が、依存の環境の欠けを確かめて止まる（#2447 の残り）。"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ndf import identity
from ndf.contracts import causes

ROOT = identity.ROOT


@pytest.fixture
def start(tmp_path, monkeypatch):
    """`bin/start.py` を読み、確かめたマーカーの保存先を一時の保存先へ向けた module。"""
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


# --- 呼び出し元の環境へ戻す（#4029） ---

# bin/ndf と uv run が NDF の子孫へ足す変数（呼び出し元の値へ戻す）
ADDED = ("CLAUDE_PLUGIN_ROOT", "UV_PROJECT_ENVIRONMENT", "VIRTUAL_ENV", "UV_RUN_RECURSION_DEPTH", "PATH", "PYTHONPATH")


def test_the_callers_values_come_back_and_the_held_copies_go(start):
    # bin/ndf が写した形: あった値（空も）は NDF_CALLER_<名前>、無かった値は写し無し。CLAUDE_PLUGIN_ROOT だけは空で「無かった」
    env = {
        "CLAUDE_PLUGIN_ROOT": "/ndf",
        "NDF_CALLER_PLUGIN_ROOT": "",
        "UV_PROJECT_ENVIRONMENT": "/ndf-venv",
        "NDF_CALLER_UV_PROJECT_ENVIRONMENT": "/caller-venv",
        "VIRTUAL_ENV": "/ndf-venv",
        "UV_RUN_RECURSION_DEPTH": "1",
        "NDF_CALLER_UV_RUN_RECURSION_DEPTH": "",
        "PATH": "/ndf-venv/bin:/usr/bin",
        "NDF_CALLER_PATH": "/usr/bin",
        "PYTHONPATH": "/ndf/src",
        "NDF_CALLER_PYTHONPATH": "/caller-pp",
        "HOME": "/home/x",
    }

    start.restore_caller_env(env)

    assert env == {
        "UV_PROJECT_ENVIRONMENT": "/caller-venv",
        "UV_RUN_RECURSION_DEPTH": "",
        "PATH": "/usr/bin",
        "PYTHONPATH": "/caller-pp",
        "HOME": "/home/x",
    }


def test_another_plugins_root_comes_back_as_the_callers_value(start):
    env = {"CLAUDE_PLUGIN_ROOT": "/ndf", "NDF_CALLER_PLUGIN_ROOT": "/other-plugin", "PATH": "/b", "NDF_CALLER_PATH": "/a"}
    start.restore_caller_env(env)
    assert env == {"CLAUDE_PLUGIN_ROOT": "/other-plugin", "PATH": "/a"}


def test_a_start_not_through_the_entrypoint_keeps_the_environment(start):
    # 写しが 1 つも無い（bin/ndf を通らずに start.py を打った）ときは何も変えない
    env = {"UV_PROJECT_ENVIRONMENT": "/v", "VIRTUAL_ENV": "/v", "PATH": "/v/bin", "PYTHONPATH": "/src"}
    start.restore_caller_env(env)
    assert env == {"UV_PROJECT_ENVIRONMENT": "/v", "VIRTUAL_ENV": "/v", "PATH": "/v/bin", "PYTHONPATH": "/src"}


def test_the_package_runs_after_the_environment_is_restored(start, monkeypatch):
    monkeypatch.setattr(start.metadata, "distributions", lambda: [])
    for name in ADDED:  # 戻した後に monkeypatch がテストの前の値へ戻すよう、触る変数を記録させる
        if name in os.environ:
            monkeypatch.setenv(name, os.environ[name])
    monkeypatch.setenv("NDF_CALLER_PATH", "/caller")
    seen = []
    monkeypatch.setattr(start.runpy, "run_module", lambda *a, **k: seen.append(os.environ["PATH"]))
    assert start.main() == 0
    assert seen == ["/caller"] and "NDF_CALLER_PATH" not in os.environ


# uv run の代わり: uv run と同じく VIRTUAL_ENV・UV_RUN_RECURSION_DEPTH・PATH の先頭の venv の bin を足し、`python` の後を
# テストの python で打つ
_FAKE_UV_RUN = """#!/bin/sh
export VIRTUAL_ENV="$UV_PROJECT_ENVIRONMENT" UV_RUN_RECURSION_DEPTH=1 PATH="$UV_PROJECT_ENVIRONMENT/bin:$PATH"
while [ "$1" != python ]; do shift; done
shift
exec "$FAKE_UV_PYTHON" "$@"
"""
_DUMP = "import json, os, sys; open(sys.argv[1], 'w').write(json.dumps(dict(os.environ)))"


@pytest.mark.parametrize(
    "caller",
    [
        {"UV_PROJECT_ENVIRONMENT": "/caller/env", "VIRTUAL_ENV": "/caller/venv", "PYTHONPATH": "/caller/pp"},
        {},
    ],
    ids=["caller-has-values", "caller-has-none"],
)
def test_a_child_of_ndf_gets_the_callers_environment(tmp_path, caller):
    """#4029 の再現: NDF が起動した子（プランのステップ）の環境に、NDF が足した UV_PROJECT_ENVIRONMENT・VIRTUAL_ENV・
    UV_RUN_RECURSION_DEPTH・CLAUDE_PLUGIN_ROOT が無く、PATH の先頭が NDF の venv の bin でなく、PYTHONPATH に NDF の
    src が無い。呼び出し元が元から持っていた値は残る。"""
    import git_sandbox

    fake = tmp_path / "fake"
    fake.mkdir()
    (fake / "uv").write_text(_FAKE_UV_RUN)
    (fake / "uv").chmod(0o755)
    repo = tmp_path / "repo"
    repo.mkdir()
    git_sandbox.sh(repo, "init", "-q", identity=True)
    git_sandbox.sh(repo, "commit", "-q", "--allow-empty", "-m", "init", identity=True)
    dump = tmp_path / "child-env.json"
    plan = repo / "plan.json"
    plan.write_text(json.dumps({"name": "p", "repo": str(repo), "steps": [{"id": "s", "run": [sys.executable, "-c", _DUMP, str(dump)]}]}))
    env = {k: v for k, v in os.environ.items() if k not in ADDED and not k.startswith("NDF_CALLER_")}
    env |= {"PATH": f"{fake}{os.pathsep}{os.environ['PATH']}", "FAKE_UV_PYTHON": sys.executable, **caller}
    env.pop("NDF_PROGRESS_DB", None)

    done = subprocess.run(
        [str(ROOT / "bin" / "ndf"), "plan", "next", str(plan)], cwd=repo, env=env, capture_output=True, text=True, check=False, timeout=300
    )

    assert done.returncode == 0, done.stdout + done.stderr
    child = json.loads(dump.read_text())
    assert {k: child.get(k) for k in ADDED} == {k: env.get(k) for k in ADDED}
    assert not [k for k in child if k.startswith("NDF_CALLER_")]
