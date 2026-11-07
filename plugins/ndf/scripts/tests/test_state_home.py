"""状態の置き場の根（#2766）: 解決の順・旧からの移行・各置き場とシェルの同じ順。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))

import durable_keys  # noqa: E402
import run_metrics  # noqa: E402
import state_home as sh  # noqa: E402
import usage_ledger  # noqa: E402

WF = SCRIPTS.parent / "skills" / "development-workflow" / "scripts" / "lib" / "workflow-common.sh"


def _layout(home: Path, case: str) -> None:
    if case in ("old", "both"):
        (home / ".local" / "state" / "ndf").mkdir(parents=True)
    if case in ("new", "both"):
        (home / ".claude" / "ndf" / "state").mkdir(parents=True)


CASES = [
    ("none", {}, ".claude/ndf/state"),
    ("old", {}, ".local/state/ndf"),
    ("both", {}, ".claude/ndf/state"),
    ("new", {}, ".claude/ndf/state"),
    ("old", {"XDG_STATE_HOME": "/x"}, "/x/ndf"),
    ("old", {"XDG_STATE_HOME": "/x", "NDF_STATE_HOME": "/n"}, "/n"),
]


@pytest.mark.parametrize("case, extra, want", CASES)
def test_state_home_order(tmp_path, case, extra, want):
    _layout(tmp_path, case)
    env = {"HOME": str(tmp_path), **extra}
    expect = Path(want) if want.startswith("/") else tmp_path / want
    assert sh.state_home(env) == expect
    # 個別の置き場も同じ根の下に解決される
    assert durable_keys.records_dir(env) == expect / "dbos"
    assert run_metrics.metrics_dir(env) == expect / "metrics"
    assert usage_ledger.ledger_dir(env) == expect / "usage"


@pytest.mark.parametrize("case, extra, want", CASES)
def test_shell_order_matches(tmp_path, case, extra, want):
    """シェルの ndf_state_home は Python と同じ根を返す。"""
    _layout(tmp_path, case)
    env = {"HOME": str(tmp_path), "PATH": os.environ["PATH"], **extra}
    out = subprocess.run(["bash", "-c", f". '{WF}' && ndf_state_home"], env=env, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == str(sh.state_home(env))


def test_migrate_moves_and_then_resolves_new(tmp_path):
    _layout(tmp_path, "old")
    (tmp_path / ".local/state/ndf/dbos").mkdir()
    (tmp_path / ".local/state/ndf/dbos/a.sqlite").write_text("x")
    env = {"HOME": str(tmp_path)}
    r = sh.migrate(env, proc=tmp_path / "noproc")
    assert r["status"] == "migrated"
    assert (tmp_path / ".claude/ndf/state/dbos/a.sqlite").read_text() == "x"
    assert not (tmp_path / ".local/state/ndf").exists()
    assert sh.state_home(env) == tmp_path / ".claude/ndf/state"
    assert sh.migrate(env, proc=tmp_path / "noproc")["status"] == "none"


def test_migrate_refuses_when_held(tmp_path):
    """旧の下のファイルを開いているプロセスがあれば何も変えない。"""
    _layout(tmp_path, "old")
    f = tmp_path / ".local/state/ndf/a.sqlite"
    f.write_text("x")
    env = {"HOME": str(tmp_path)}
    with subprocess.Popen(["sleep", "30"], stdin=open(f)) as p:
        try:
            r = sh.migrate(env)
        finally:
            p.kill()
    assert r["status"] == "held" and any(h["pid"] == p.pid for h in r["holders"])
    assert f.exists() and not (tmp_path / ".claude/ndf/state").exists()


@pytest.mark.parametrize("case, extra, status", [("both", {}, "conflict"), ("old", {"XDG_STATE_HOME": "/x"}, "none")])
def test_migrate_leaves_conflict_and_overrides(tmp_path, case, extra, status):
    _layout(tmp_path, case)
    r = sh.migrate({"HOME": str(tmp_path), **extra}, proc=tmp_path / "noproc")
    assert r["status"] == status
    assert (tmp_path / ".local/state/ndf").is_dir()


def test_cli_path_reports_root(tmp_path):
    env = {"HOME": str(tmp_path), "PATH": os.environ["PATH"]}
    out = subprocess.run([sys.executable, str(SCRIPTS / "state-home.py"), "path"], env=env, capture_output=True, text=True)
    assert out.returncode == 0
    assert json.loads(out.stdout)["metrics"]["root"] == str(tmp_path / ".claude/ndf/state")
