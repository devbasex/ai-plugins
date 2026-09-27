"""`test_triage` の打ち切りと上限の共有（#1334 の round 3）。

- 打ち切るときはシェルだけでなくプロセスグループごと止める（孫の pytest が作業ツリーを書き換え続けないため）
- 失敗の分類は、走らせ直しと着手前の HEAD の再実行の全体で 1 つの上限を使う
"""

from __future__ import annotations

import os
import pathlib
import sys
import time

LIB = pathlib.Path(__file__).resolve().parents[1] / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import test_strategy as ts  # noqa: E402
import test_triage  # noqa: E402


def test_a_timed_out_shell_command_takes_its_children_down(tmp_path):
    pid_file = tmp_path / "child.pid"
    command = f"sleep 60 & echo $! > {pid_file}; wait"
    code, timed_out = test_triage.run_command(command, str(tmp_path), 1)
    assert (code, timed_out) == (None, True)
    child = int(pid_file.read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    raise AssertionError("打ち切った後も子の sleep が残っている")


def test_a_command_that_finishes_returns_its_exit_code(tmp_path):
    assert test_triage.run_command("exit 3", str(tmp_path), 10) == (3, False)


def _suites():
    return ts.Strategy(
        "local-full",
        "test",
        [
            ts.Suite("a", "run a", "run-a {paths}", paths=["a"]),
            ts.Suite("b", "run b", "run-b {paths}", paths=["b"]),
        ],
    )


def test_classify_shares_one_limit_across_the_reruns_and_the_baseline(monkeypatch, tmp_path):
    now = [0.0]
    monkeypatch.setattr(test_triage.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(test_triage, "tracked_files", lambda work: [])
    monkeypatch.setattr(test_triage, "clear_junit", lambda work, strategy: None)
    monkeypatch.setattr(test_triage, "read_junit", lambda work, strategy, tracked=None: (["a/t.py::x", "b/t.py::y"], None))
    monkeypatch.setattr(test_triage, "_git", lambda work, args: True)
    given: list[int] = []

    def run(words, cwd, timeout, log=None):
        given.append(timeout)
        now[0] += 30
        return 1, False

    out = test_triage.classify(
        work=str(tmp_path),
        strategy=_suites(),
        failed=["a/t.py::x", "b/t.py::y"],
        fallback_reason=None,
        base_sha="abc",
        timeout=100,
        log_dir=tmp_path / "logs",
        run=run,
    )
    assert given == [100, 70, 40, 10], "4 回の再実行で 100 秒を分け合う（再実行ごとに 100 秒を渡さない）"
    assert out["preexisting"] == ["a/t.py::x", "b/t.py::y"]
