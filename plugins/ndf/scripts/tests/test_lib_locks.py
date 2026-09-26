"""ファイルロックの包み（lib/locks.py・#1142 の決定 19）。外部パッケージは全体テストの環境（根の pyproject.toml）が入れる。"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))
import filelock  # noqa: E402,F401  包みの外部パッケージ。無ければ集めるところで落とす
import locks  # noqa: E402


def hold(target: Path, seconds: float) -> subprocess.Popen:
    """別のプロセスで排他を `seconds` 秒持つ。取れたら標準出力へ 1 行を出す。"""
    code = (f"import sys, time; sys.path.insert(0, {str(LIB)!r}); import locks\n"
            f"with locks.exclusive({str(target)!r}):\n    print('held', flush=True); time.sleep({seconds})\n")
    p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "held"
    return p


def test_lock_lives_beside_the_target_and_keeps_its_content(tmp_path: Path):
    target = tmp_path / "state.json"
    target.write_text("{}")
    with locks.exclusive(target):
        pass
    assert target.read_text() == "{}" and locks.lock_path(target) == tmp_path / "state.json.lock"


def test_other_process_blocks_until_timeout(tmp_path: Path):
    target = tmp_path / "q" / "state.json"
    p = hold(target, 3)
    try:
        assert locks.try_exclusive(target) is None
        t0 = time.monotonic()
        with pytest.raises(locks.LockTimeout, match="0.3 秒"):
            with locks.exclusive(target, timeout=0.3):
                pass
        assert time.monotonic() - t0 >= 0.3
    finally:
        p.kill()
        p.wait()
    held = locks.try_exclusive(target)
    assert held is not None
    with held:
        pass


def test_append_locked_adds_lines(tmp_path: Path):
    j = tmp_path / "log.jsonl"
    locks.append_locked(j, '{"a": 1}')
    locks.append_locked(j, '{"b": 2}\n')
    assert j.read_text() == '{"a": 1}\n{"b": 2}\n'
