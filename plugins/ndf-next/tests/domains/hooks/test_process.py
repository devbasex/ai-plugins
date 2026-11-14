"""エントリポイントを別のプロセスで起動する: 所要の退行（M3-H13）と、環境が無い状態からの用意（M3-H14・IH2）。

登録の command は S1 が `hooks/claude.json` に置く形（決定 89）をそのまま打つ。`hooks/entry.py` と `hooks/boot.py` は
S1 が置くため、ここでは同じ働きの薄いスクリプト（本体の `run_entry`・`run_boot` を呼ぶだけ）を一時の置き場に置く。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ndf.domains.hooks import budget, env, metrics
from ndf.testing.waits import wait_until

from ._hooks import AUTO, SRC, bash, make_repo, read_sample, records, sample, sample_paths

REGISTRATION = '[ -x "$0" ] && exec "$0" "$@"; command -v python3 >/dev/null 2>&1 || exit 0; b="${1%/*}/boot.py"; shift; exec python3 "$b" "$@"'
RUNS = 20


@pytest.fixture
def scripts(tmp_path) -> Path:
    """`hooks/entry.py` と `hooks/boot.py` と同じ働きの薄いスクリプトを置いた置き場。"""
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    for name, func, module in (("entry.py", "run_entry", "main"), ("boot.py", "run_boot", "boot")):
        (hooks / name).write_text(
            f"import sys\nsys.path.insert(0, {str(SRC)!r})\nfrom ndf.domains.hooks.{module} import {func}\nsys.exit({func}(sys.argv))\n",
            encoding="utf-8",
        )
    return hooks


def _register(python: Path, scripts: Path, runtime: str, event: str, payload: dict, extra_env: dict | None = None, timeout: float = 30):
    """登録の command を打つ（環境の python があれば entry.py、無ければシステムの python3 で boot.py）。"""
    command = ["sh", "-c", REGISTRATION, str(python), str(scripts / "entry.py"), runtime, event]
    return subprocess.run(command, input=json.dumps(payload), capture_output=True, text=True, timeout=timeout, env={**os.environ, **(extra_env or {})})


@pytest.mark.parametrize("path", sample_paths(), ids=lambda p: "/".join(p.parts[-3:]))
def test_entry_p95_stays_within_three_times_the_budget(path, scripts, tmp_path):
    runtime, event = path.parts[-3], path.stem
    payload = json.dumps(read_sample(path, tmp_path))
    spent = []
    for _ in range(RUNS):
        started = time.monotonic()
        done = subprocess.run([sys.executable, str(scripts / "entry.py"), runtime, event], input=payload, capture_output=True, text=True, timeout=60)
        spent.append((time.monotonic() - started) * 1000)
        assert done.returncode == 0
    limit = 3 * budget.budget_for(runtime, event).p95_ms
    assert metrics.percentile(spent, 95) <= limit, f"p95 {metrics.percentile(spent, 95):.0f} ms > {limit:.0f} ms"


def test_without_the_environment_every_event_passes_except_strict(scripts, tmp_path):
    missing = tmp_path / "no-venv" / "bin" / "python"
    repo = make_repo(tmp_path / "r", delivery=AUTO)
    for path in sample_paths():
        runtime, event = path.parts[-3], path.stem
        if event == "SessionStart":
            continue
        done = _register(missing, scripts, runtime, event, read_sample(path, tmp_path))
        assert (done.returncode, done.stdout) == (0, ""), path
    denied = _register(missing, scripts, "claude", "PreToolUse", bash(repo, "gh pr merge 1"))
    assert denied.returncode == 0 and "deny" in denied.stdout
    assert {r["path"] for r in records()} == {"boot"}


def test_without_python3_the_registration_does_nothing(scripts, tmp_path):
    bare = tmp_path / "bare-bin"
    bare.mkdir()
    sh = shutil.which("sh")
    done = subprocess.run(
        [sh, "-c", REGISTRATION, str(tmp_path / "none"), str(scripts / "entry.py"), "claude", "PreToolUse"],
        input="{}", capture_output=True, text=True, timeout=30, env={**os.environ, "PATH": str(bare)},
    )
    assert (done.returncode, done.stdout) == (0, "")


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv が無い（CI は setup-uv で入れる）")
def test_session_start_without_the_environment_prepares_it_detached(scripts, tmp_path):
    # M3-H14: 環境を消した状態から SessionStart を打つと待たずに 0 で返り、切り離した用意が終わると entry で動く
    ident = env.Ident.of(SRC.parent)
    python = env.venv_python(ident)
    assert not python.exists()
    uv_env = {"UV_OFFLINE": "1", "UV_PYTHON_DOWNLOADS": "never", "UV_CACHE_DIR": os.environ.get("UV_CACHE_DIR") or str(Path.home() / ".cache" / "uv")}
    found = budget.budget_for("claude", "SessionStart")
    started = time.monotonic()
    done = _register(python, scripts, "claude", "SessionStart", sample("claude", "SessionStart", tmp_path), uv_env, timeout=found.timeout)
    assert done.returncode == 0 and time.monotonic() - started < found.timeout
    assert list(ident.roots_dir.glob("*.json")), "SessionStart のエントリポイントが根の目印を書く"
    wait_until(lambda: python.exists() or env.failed_path(ident).exists(), timeout=600, interval=0.5, what="切り離した用意の終わり")
    assert not env.failed_path(ident).exists(), env.failed_path(ident).read_text(encoding="utf-8")
    used = env.venv_dir(ident) / env.LAST_USED
    assert used.exists()
    os.utime(used, (1, 1))
    marks = {p: p.stat().st_mtime for p in ident.roots_dir.glob("*.json")}
    after = _register(python, scripts, "claude", "PreToolUse", bash(tmp_path, "ls"), uv_env)
    assert after.returncode == 0
    assert records()[-1]["path"] == "entry" and records()[-1]["event"] == "PreToolUse"
    # 予算の経路（PreToolUse）は目印と最後に使った時刻を書かない
    assert used.stat().st_mtime == 1 and {p: p.stat().st_mtime for p in ident.roots_dir.glob("*.json")} == marks
