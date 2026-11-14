"""codex-hooks-list.py の `wait_for_hooks_list` の今の振る舞いを固定する（現状固定テスト）。

`main` の経路は assert-characterization.sh（test_hook_definitions_characterization.py）がスタブの app-server で固定する。
ここは応答を待つ関数だけを、行の待ち行列と読み終わりの印を直接渡して呼ぶ。
"""

from __future__ import annotations

import importlib.util
import threading
import time
from collections import deque
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "lib" / "codex-hooks-list.py"


@pytest.fixture
def mod():
    spec = importlib.util.spec_from_file_location("codex_hooks_list", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _event(done: bool) -> threading.Event:
    event = threading.Event()
    if done:
        event.set()
    return event


def _fails(mod, capsys, lines, done, tail=()):
    with pytest.raises(SystemExit) as exc:
        mod.wait_for_hooks_list(deque(lines), _event(done), list(tail))
    assert exc.value.code == 1
    return capsys.readouterr().err


def test_returns_result_skipping_other_lines(mod, capsys):
    lines = deque(["noise\n", "[1]\n", '{"id":1}\n', '{"id":2,"result":{"data":[1]}}\n', "after\n"])
    assert mod.wait_for_hooks_list(lines, _event(False), []) == {"data": [1]}
    assert list(lines) == ["after\n"]
    assert capsys.readouterr().err == ""


def test_waits_for_lines_appended_later(mod):
    lines: deque[str] = deque()
    done = threading.Event()

    def feed():
        time.sleep(0.1)
        lines.append('{"id":2,"result":{"data":[]}}\n')

    thread = threading.Thread(target=feed)
    thread.start()
    try:
        assert mod.wait_for_hooks_list(lines, done, []) == {"data": []}
    finally:
        thread.join()


def test_error_response_fails_with_body_and_stderr_tail(mod, capsys):
    err = _fails(mod, capsys, ['{"id":2,"error":{"x":1}}\n'], False, ["e1\n"])
    assert err == 'hooks/list returned an error:\n{"id":2,"error":{"x":1}}\n\napp-server stderr (tail):\ne1\n'


@pytest.mark.parametrize("line", ['{"id":2,"result":{}}\n', '{"id":2,"result":[1]}\n', '{"id":2}\n'])
def test_response_without_data_fails(mod, capsys, line):
    assert _fails(mod, capsys, [line], False) == f"hooks/list response has no result.data:\n{line}\n"


def test_reader_done_before_answer_fails(mod, capsys):
    err = _fails(mod, capsys, ['{"id":1}\n'], True, ["t\n"])
    assert err == 'codex app-server exited before answering hooks/list:\n{"id":1}\n\napp-server stderr (tail):\nt\n'
    assert _fails(mod, capsys, [], True) == "codex app-server exited before answering hooks/list:\n\n"


def test_no_answer_within_timeout_fails(mod, capsys, monkeypatch):
    monkeypatch.setattr(mod, "TIMEOUT_SECONDS", 0)
    err = _fails(mod, capsys, ["x\n"], False)
    assert err.startswith("codex app-server did not answer hooks/list within ")
    assert err.endswith("s:\nx\n\n")


class _Clock:
    """渡した時刻を順に返す時計と、待った秒を控える待ち。待つ間に `during_sleep` を 1 度呼ぶ。"""

    def __init__(self, times, during_sleep=None):
        self.times = list(times)
        self.slept: list[float] = []
        self.during_sleep = during_sleep

    def clock(self) -> float:
        return self.times.pop(0)

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        if self.during_sleep is not None:
            self.during_sleep()
            self.during_sleep = None


def test_empty_queue_fails_only_after_the_deadline_is_passed(mod, capsys):
    # 期限の比較は >（期限ちょうどではまだ待つ）。待つ間隔は 0.05 秒
    fake = _Clock([0, 10, mod.TIMEOUT_SECONDS, mod.TIMEOUT_SECONDS + 0.01])
    with pytest.raises(SystemExit) as exc:
        mod.wait_for_hooks_list(deque(), _event(False), [], clock=fake.clock, sleep=fake.sleep)
    assert exc.value.code == 1
    assert capsys.readouterr().err == f"codex app-server did not answer hooks/list within {mod.TIMEOUT_SECONDS}s:\n\n"
    assert fake.slept == [0.05, 0.05]


def test_finished_reader_is_reported_before_the_deadline(mod, capsys):
    # 読み終わった reader は、期限を過ぎていても応答前の終了として報告する（期限の判定より先）
    fake = _Clock([0, mod.TIMEOUT_SECONDS + 1])
    with pytest.raises(SystemExit) as exc:
        mod.wait_for_hooks_list(deque(["x\n"]), _event(True), [], clock=fake.clock, sleep=fake.sleep)
    assert exc.value.code == 1
    assert capsys.readouterr().err == "codex app-server exited before answering hooks/list:\nx\n\n"
    assert fake.slept == []


def test_answer_that_arrived_by_the_deadline_is_returned(mod, capsys):
    # 待つ間に届いた応答は、次の周回で期限の判定より先に読む
    lines: deque[str] = deque()
    fake = _Clock([0, 1, mod.TIMEOUT_SECONDS + 1], during_sleep=lambda: lines.append('{"id":2,"result":{"data":[3]}}\n'))
    assert mod.wait_for_hooks_list(lines, _event(False), [], clock=fake.clock, sleep=fake.sleep) == {"data": [3]}
    assert capsys.readouterr().err == ""
    assert (fake.slept, fake.times) == ([0.05], [mod.TIMEOUT_SECONDS + 1])
