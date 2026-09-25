"""区切った待ち `bg-wait.sh`（#598 / #537 の AC39 / AC40）。

Claude Code の Bash ツールは 1 回 600 秒で打ち切る。監視の上限は 1200 秒あるため、
監視を背景で起動し（`run`）、540 秒以内に区切った待ち（`wait`）を呼び直す。rc ファイルが
あるので、待ちの呼び出しをまたいでも終了コードを失わない（設計の決定 13）。
置き場所は共通層の `scripts/lib/` で、cross-review と cross-refactoring の駆動の待ちが使う（#731）。
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import time

import pytest

BG_WAIT = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "lib" / "bg-wait.sh"

pytestmark = pytest.mark.skipif(
    any(shutil.which(c) is None for c in ("bash", "sleep", "cat", "tail", "mv", "rm")),
    reason="bg-wait.sh が使う外部コマンドが無い",
)


def _bg(*args: str, timeout: float = 30) -> subprocess.CompletedProcess:
    # **出力を捕まえて実行する。** 背景のコマンドが標準出力を握ったままだと、
    # 呼び出し側（Bash ツール）は背景が終わるまで戻らない。
    return subprocess.run(["bash", str(BG_WAIT), *args], capture_output=True, text=True,
                          timeout=timeout)


def _run(rc: pathlib.Path, script: str) -> subprocess.CompletedProcess:
    return _bg("run", str(rc), "--", "bash", "-c", script)


# ---------- AC39 ----------

def test_run_returns_0_at_once_while_the_command_keeps_running(tmp_path) -> None:
    rc = tmp_path / "job.rc"
    started = time.monotonic()
    r = _run(rc, "sleep 3; exit 5")
    assert r.returncode == 0, r.stderr
    assert time.monotonic() - started < 2
    assert not rc.exists()


def test_the_command_exit_code_is_written_to_the_rc_file(tmp_path) -> None:
    rc = tmp_path / "job.rc"
    _run(rc, "exit 5")
    for _ in range(100):
        if rc.exists():
            break
        time.sleep(0.05)
    assert rc.read_text().strip() == "5"


def test_the_command_output_goes_to_the_log(tmp_path) -> None:
    rc = tmp_path / "job.rc"
    _run(rc, "echo out; echo err >&2")
    assert _bg("wait", str(rc), "--max-wait", "10").returncode == 0
    assert set(pathlib.Path(f"{rc}.log").read_text().split()) == {"out", "err"}


def test_run_removes_a_stale_rc_file_first(tmp_path) -> None:
    rc = tmp_path / "job.rc"
    rc.write_text("0\n")
    _run(rc, "sleep 2; exit 3")
    assert _bg("wait", str(rc), "--max-wait", "10").returncode == 3


def test_run_removes_a_stale_log_first(tmp_path) -> None:
    """前回のログが残っていると、起動に失敗した回の `wait` が前回の出力を出す。"""
    rc = tmp_path / "job.rc"
    _run(rc, "echo 前回の出力")
    assert _bg("wait", str(rc), "--max-wait", "10").returncode == 0
    _run(rc, "sleep 2; exit 0")
    log = pathlib.Path(f"{rc}.log")
    assert "前回の出力" not in (log.read_text() if log.exists() else "")


def test_run_rejects_a_missing_command(tmp_path) -> None:
    assert _bg("run", str(tmp_path / "job.rc"), "--").returncode == 1


# ---------- AC40 ----------

def test_wait_returns_the_exit_code_when_the_command_ends(tmp_path) -> None:
    rc = tmp_path / "job.rc"
    _run(rc, "sleep 1; exit 4")
    r = _bg("wait", str(rc), "--max-wait", "10")
    assert r.returncode == 4


def test_wait_returns_124_after_max_wait_and_the_code_later(tmp_path) -> None:
    """設計の実測と同じ形（時間だけ縮めた）: 5 を返すコマンドを、別々の呼び出しで待つ。"""
    rc = tmp_path / "job.rc"
    _run(rc, "sleep 4; exit 5")
    started = time.monotonic()
    first = _bg("wait", str(rc), "--max-wait", "1")
    assert first.returncode == 124
    assert time.monotonic() - started < 3.5
    second = _bg("wait", str(rc), "--max-wait", "20")
    assert second.returncode == 5


def test_wait_shows_the_log_when_the_command_ends(tmp_path) -> None:
    rc = tmp_path / "job.rc"
    _run(rc, 'echo \'{"agent": "agy", "status": "OK"}\'')
    r = _bg("wait", str(rc), "--max-wait", "10")
    assert '"status": "OK"' in r.stdout


def test_wait_shows_the_last_log_line_while_waiting(tmp_path) -> None:
    rc = tmp_path / "job.rc"
    _run(rc, "echo '⏳ agy elapsed=15s' >&2; sleep 5")
    time.sleep(0.5)
    r = _bg("wait", str(rc), "--max-wait", "1")
    assert r.returncode == 124
    assert "agy elapsed=15s" in r.stderr


@pytest.mark.parametrize(("given", "used"), [("541", "540"), ("600", "540"), ("540", "540"), ("30", "30")])
def test_max_wait_above_540_is_treated_as_540(tmp_path, given: str, used: str) -> None:
    rc = tmp_path / "job.rc"
    _run(rc, "exit 0")
    r = _bg("wait", str(rc), "--max-wait", given)
    assert r.returncode == 0
    assert f"最大 {used} 秒" in r.stderr


def test_max_wait_defaults_to_540(tmp_path) -> None:
    rc = tmp_path / "job.rc"
    _run(rc, "exit 0")
    assert "最大 540 秒" in _bg("wait", str(rc)).stderr


def test_max_wait_0_returns_124_at_once_then_the_code_later(tmp_path) -> None:
    """許容される最小値 0 秒は待たずに 124 を返す（deadline 比較の境界）。
    続けて十分な上限で待つと、背景コマンド本来の終了コードを取得できる。"""
    rc = tmp_path / "job.rc"
    _run(rc, "sleep 3; exit 7")
    started = time.monotonic()
    first = _bg("wait", str(rc), "--max-wait", "0")
    assert first.returncode == 124
    assert time.monotonic() - started < 2
    second = _bg("wait", str(rc), "--max-wait", "20")
    assert second.returncode == 7


@pytest.mark.parametrize("bad", ["", "abc", "-1"])
def test_a_bad_max_wait_is_a_usage_error(tmp_path, bad: str) -> None:
    rc = tmp_path / "job.rc"
    _run(rc, "exit 0")
    assert _bg("wait", str(rc), "--max-wait", bad).returncode == 1


def test_wait_without_a_run_exits_1_at_once(tmp_path) -> None:
    started = time.monotonic()
    r = _bg("wait", str(tmp_path / "never.rc"), "--max-wait", "10")
    assert r.returncode == 1
    assert time.monotonic() - started < 3


def test_wait_exits_1_when_the_background_died_without_a_code(tmp_path) -> None:
    """背景が強制終了されると rc ファイルが書かれない。124 を返し続けると待ちが終わらない。"""
    rc = tmp_path / "job.rc"
    _run(rc, "sleep 30")
    pid = int(pathlib.Path(f"{rc}.pid").read_text())
    os.kill(pid, 9)
    time.sleep(0.5)
    r = _bg("wait", str(rc), "--max-wait", "10")
    assert r.returncode == 1
    assert not rc.exists()
