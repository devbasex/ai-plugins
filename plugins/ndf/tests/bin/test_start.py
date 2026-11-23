"""エントリポイントが起動する `bin/start.py` が、依存の環境の欠けを確かめて止まる（#2447 の残り）。"""

from __future__ import annotations

import importlib.util
import json
import os
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


# --- 呼び出し元の環境へ戻す（#4029） ---

# bin/ndf と uv run が NDF の子孫へ足す・bin/ndf が消す変数（呼び出し元の値へ戻す）
ADDED = ("CLAUDE_PLUGIN_ROOT", "UV_PROJECT_ENVIRONMENT", "VIRTUAL_ENV", "UV_RUN_RECURSION_DEPTH", "PATH", "PYTHONPATH", "UV_ISOLATED")


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
        "NDF_CALLER_UV_ISOLATED": "1",
        "HOME": "/home/x",
    }

    start.restore_caller_env(env)

    assert env == {
        "UV_PROJECT_ENVIRONMENT": "/caller-venv",
        "UV_RUN_RECURSION_DEPTH": "",
        "PATH": "/usr/bin",
        "PYTHONPATH": "/caller-pp",
        "UV_ISOLATED": "1",
        "HOME": "/home/x",
    }


def test_another_plugins_root_comes_back_as_the_callers_value(start):
    env = {"CLAUDE_PLUGIN_ROOT": "/ndf", "NDF_CALLER_PLUGIN_ROOT": "/other-plugin", "PATH": "/b", "NDF_CALLER_PATH": "/a"}
    start.restore_caller_env(env)
    assert env == {"CLAUDE_PLUGIN_ROOT": "/other-plugin", "PATH": "/a"}


def test_uv_isolated_from_the_env_file_stays_without_a_callers_copy(start):
    # 呼び出し元に UV_ISOLATED が無く、UV_ENV_FILE から uv が入れた値は子へ残す
    env = {"PATH": "/ndf-venv/bin:/usr/bin", "NDF_CALLER_PATH": "/usr/bin", "UV_ISOLATED": "1"}
    start.restore_caller_env(env)
    assert env == {"PATH": "/usr/bin", "UV_ISOLATED": "1"}


def test_the_callers_uv_isolated_wins_over_the_env_file(start):
    env = {"NDF_CALLER_UV_ISOLATED": "0", "UV_ISOLATED": "1"}
    start.restore_caller_env(env)
    assert env == {"UV_ISOLATED": "0"}


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


@pytest.mark.parametrize("line", ["error: Failed to download `ruff==0.17.5`", ""])
def test_a_failed_sync_stops_with_one_result_line_and_deps_missing(start, capsys, line):
    # #3687: bin/ndf は exec の前の uv sync が落ちたら、uv の error の行（無ければ最終行。空もある）を添えてここを呼ぶ
    detail = f"依存を用意できない（ネットワークを確かめる）: {line}"
    code = start.emit_deps_missing(detail)
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 1
    report = json.loads(out[0])
    assert (report["status"], report["summary"], report["cause"]) == ("stopped", detail, {"kind": "deps_missing", "detail": detail})
    assert code == causes.exit_code("deps_missing") == 69
