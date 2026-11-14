"""エントリポイントを別のプロセスで起動する: 起動の重さの退行（M3-H13）と、環境が無い状態からの用意（M3-H14・IH2）。

M3-H13 は所要時間で合否を決めない（マシンのスペックと混み具合で決まり、コードの良し悪しを表さない。#3436）。
エントリポイントが起動する子のプロセスの数・読み込むモジュールの数・ディレクトリの走査を数え、決めた値と比べる。
所要時間（p50・p95）は `ndf hooks stats` に記録するだけである。

登録の command は `hooks/claude.json` の形（決定 89。`registration.SCRIPT`）をそのまま打つ。数える対象を一時の置き場に
限るため、`hooks/entry.py` と `hooks/boot.py` と同じ働きの薄いスクリプト（本体の `run_entry`・`run_boot` を呼ぶだけ）を置く。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from ndf.domains.hooks import budget, env, registration
from ndf.testing.waits import wait_until

from ._hooks import AUTO, SRC, bash, make_repo, read_sample, records, sample, sample_paths, state_dir

REGISTRATION = registration.SCRIPT
# エントリポイント 1 回の重さの上限（M3-H13）。モジュールは 2026-11-14 の実測（PreToolUse 83・Stop 146・SessionStart 70・
# UserPromptSubmit 61・PostToolUse と SessionEnd 58）へ約 2 割を足した値。子のプロセスはどの事象も起動しない。
# ディレクトリの走査は SessionStart の掃除（状態ディレクトリの hooks の下）だけが行う
MAX_MODULES = {
    "PreToolUse": 100,
    "PermissionRequest": 100,
    "PostToolUse": 70,
    "UserPromptSubmit": 75,
    "SessionEnd": 70,
    "SessionStart": 85,
    "Stop": 175,
}
MAX_CHILDREN = 0
SCANNING_EVENTS = frozenset({"SessionStart"})

# 対象のスクリプトを同じプロセスで走らせ、子のプロセス・新しく読んだモジュール・ディレクトリの走査を数えて書き出す
PROBE = textwrap.dedent(
    """
    import json, os, runpy, subprocess, sys
    out, target = sys.argv[1], sys.argv[2]
    sys.argv = [target, *sys.argv[3:]]
    seen = {"children": 0, "scans": []}
    inside = [0]  # Popen の中の os.posix_spawn などを 2 度数えない
    _popen = subprocess.Popen.__init__
    def popen(self, *a, **k):
        seen["children"] += 1
        inside[0] += 1
        try:
            return _popen(self, *a, **k)
        finally:
            inside[0] -= 1
    subprocess.Popen.__init__ = popen
    for name in ("system", "fork", "posix_spawn", "posix_spawnp"):
        if hasattr(os, name):
            def child(*a, _o=getattr(os, name), **k):
                if not inside[0]:
                    seen["children"] += 1
                return _o(*a, **k)
            setattr(os, name, child)
    for name in ("scandir", "listdir", "walk"):
        def scan(*a, _o=getattr(os, name), _n=name, **k):
            seen["scans"].append(str(a[0]) if a else ".")
            return _o(*a, **k)
        setattr(os, name, scan)
    before = set(sys.modules)
    code = 0
    try:
        runpy.run_path(target, run_name="__main__")
    except SystemExit as exc:
        code = exc.code or 0
    finally:
        seen["modules"] = len(set(sys.modules) - before)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(seen, f)
    sys.exit(code)
    """
)


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
    return subprocess.run(
        command, input=json.dumps(payload), capture_output=True, text=True, timeout=timeout, env={**os.environ, **(extra_env or {})}
    )


def _footprint(tmp_path: Path, target: Path, args: list[str], payload: str = "", cwd: Path | None = None) -> tuple[int, dict]:
    probe = tmp_path / "probe.py"
    probe.write_text(PROBE, encoding="utf-8")
    out = tmp_path / "footprint.json"
    done = subprocess.run(
        [sys.executable, str(probe), str(out), str(target), *args], input=payload, capture_output=True, text=True, timeout=60, cwd=cwd
    )
    return done.returncode, json.loads(out.read_text(encoding="utf-8"))


def test_the_probe_counts_children_modules_and_scans(tmp_path):
    # 数え漏らすと上限の検査が素通りするため、数える側を先に確かめる
    target = tmp_path / "heavy.py"
    target.write_text(
        "import pathlib, subprocess, sys, xml.dom.minidom\n"
        "subprocess.run([sys.executable, '-c', '0'])\n"
        "list(pathlib.Path('.').glob('*'))\n"
        "list(pathlib.Path('.').iterdir())\n",
        encoding="utf-8",
    )
    code, seen = _footprint(tmp_path, target, [], cwd=tmp_path)
    assert code == 0
    assert seen["children"] == 1 and len(seen["scans"]) >= 2 and seen["modules"] >= 1


@pytest.mark.parametrize("path", sample_paths(), ids=lambda p: "/".join(p.parts[-3:]))
def test_entry_stays_within_the_footprint(path, scripts, tmp_path):
    runtime, event = path.parts[-3], path.stem
    repo = make_repo(tmp_path / "r", delivery=AUTO)
    payload = json.dumps(read_sample(path, repo))
    code, seen = _footprint(tmp_path, scripts / "entry.py", [runtime, event], payload, cwd=repo)
    assert code == 0
    assert records()[-1]["path"] == "entry" and not records()[-1].get("error"), records()[-1]
    assert seen["children"] <= MAX_CHILDREN, seen
    assert seen["modules"] <= MAX_MODULES[event], f"{event}: モジュール {seen['modules']} > {MAX_MODULES[event]}"
    if event in SCANNING_EVENTS:
        hooks_dir = str(state_dir() / "hooks")
        assert all(p.startswith(hooks_dir) for p in seen["scans"]), seen["scans"]
    else:
        assert seen["scans"] == [], seen["scans"]


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
        input="{}",
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "PATH": str(bare)},
    )
    assert (done.returncode, done.stdout) == (0, "")


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv が無い（CI は setup-uv で入れる）")
def test_session_start_without_the_environment_prepares_it_detached(scripts, tmp_path):
    # M3-H14: 環境を消した状態から SessionStart を打つと待たずに 0 で返り、切り離した用意が終わると entry で動く
    ident = env.Ident.of(SRC.parent)
    python = env.venv_python(ident)
    assert not python.exists()
    uv_env = {
        "UV_OFFLINE": "1",
        "UV_PYTHON_DOWNLOADS": "never",
        "UV_CACHE_DIR": os.environ.get("UV_CACHE_DIR") or str(Path.home() / ".cache" / "uv"),
    }
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
