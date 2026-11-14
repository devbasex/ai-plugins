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
