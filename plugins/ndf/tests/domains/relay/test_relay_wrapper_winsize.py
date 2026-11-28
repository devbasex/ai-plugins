"""ラッパーは外側の端末の大きさを子の擬似端末へ合わせる（子は偽物で、SIGWINCH は自分のプロセスへ上げる）。"""

from __future__ import annotations

import signal
import threading

import pytest
from ndf.domains.relay import wrapper_winsize
from relay_children import FakeChild, run_wrapper


class SizedChild(FakeChild):
    """合わせた大きさを `sizes` に残す子。`on_look` は見回りの回（1 始まり）→ その回に打つ手。"""

    def __init__(self, exit_code=0, on_look=None, outputs=()):
        super().__init__(list(outputs), exit_code)
        self.sizes: list[tuple[int, int]] = []
        self.on_look = dict(on_look or {})
        self.looks = 0

    def isalive(self):
        self.looks += 1
        action = self.on_look.pop(self.looks, None)
        if action is not None:
            action()
        return super().isalive()

    def setwinsize(self, rows, cols):
        self.sizes.append((rows, cols))


@pytest.fixture
def terminal(monkeypatch):
    """外側の端末の大きさ（`[(行, 列) か None]`）。書き換えると大きさが変わる。"""
    size = [(16, 166)]
    monkeypatch.setattr(wrapper_winsize, "dims", lambda: size[0])
    return size


def resize(terminal, to):
    terminal[0] = to
    signal.raise_signal(signal.SIGWINCH)


def test_a_resize_of_the_terminal_is_passed_to_the_running_child(tmp_path, terminal):
    child = SizedChild(on_look={1: lambda: resize(terminal, (39, 166))}, outputs=[b"a"])
    previous = signal.getsignal(signal.SIGWINCH)
    ended, _, _ = run_wrapper(tmp_path, [child])
    assert ended.cause == "" and child.sizes[-1] == (39, 166)
    assert signal.getsignal(signal.SIGWINCH) == previous  # ラッパーが終われば前のハンドラへ戻す


def test_a_resize_while_the_child_is_starting_is_applied_after_the_start(tmp_path, terminal):
    child = SizedChild()
    child.started = lambda *_: resize(terminal, (39, 166))  # 起動の途中は、ハンドラの相手がまだこの子でない
    run_wrapper(tmp_path, [child])
    assert child.sizes == [(39, 166)]


def test_a_restarted_child_is_the_one_that_follows_the_terminal(tmp_path, terminal):
    first = SizedChild(exit_code=1)
    second = SizedChild(on_look={1: lambda: resize(terminal, (39, 166))}, outputs=[b"a"])
    run_wrapper(tmp_path, [first, second])
    assert (39, 166) not in first.sizes and second.sizes[-1] == (39, 166)


def test_without_a_terminal_the_child_is_left_as_started(tmp_path, terminal):
    terminal[0] = None
    child = SizedChild(on_look={1: lambda: signal.raise_signal(signal.SIGWINCH)}, outputs=[b"a"])
    ended, _, _ = run_wrapper(tmp_path, [child])
    assert ended.cause == "" and child.sizes == []


@pytest.mark.parametrize("error", [OSError, ValueError])
def test_a_closed_child_pty_does_not_break_the_wrapper(terminal, error):
    class Closed:
        def setwinsize(self, rows, cols):
            raise error("closed")

    wrapper_winsize.fit(Closed())
    wrapper_winsize.fit(None)


def test_outside_the_main_thread_no_handler_is_registered():
    previous, seen = signal.getsignal(signal.SIGWINCH), []

    def body():
        with wrapper_winsize.following(lambda: None):
            seen.append(signal.getsignal(signal.SIGWINCH))

    thread = threading.Thread(target=body)
    thread.start()
    thread.join()
    assert seen == [previous]
