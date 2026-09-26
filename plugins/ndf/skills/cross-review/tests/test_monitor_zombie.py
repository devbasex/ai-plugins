"""monitor.py のゾンビプロセス検出テスト。

Docker without --init 環境では、nohup/disown で起動したプロセスが終了後に
ゾンビ化する。`_pid_alive()` がプロセスの状態（`lib/procs.py` の psutil）でゾンビを
検出して False を返すことを検証する。

差し替え先は `procs._process`（psutil のプロセスを引く 1 か所。#1142 の D3 で `/proc/<pid>/status` の
読み取りと `os.kill(pid, 0)` から移した）。
"""
from __future__ import annotations

from unittest import mock

import procs
import psutil


class _Proc:
    """`psutil.Process` の代わり。`status()` だけを返す（例外を渡せば投げる）。"""

    def __init__(self, status):
        self._status = status

    def status(self):
        if isinstance(self._status, BaseException):
            raise self._status
        return self._status


def _patch(status):
    return mock.patch.object(procs, "_process", return_value=None if status is None else _Proc(status))


def test_pid_alive_returns_false_for_zombie(monitor_mod):
    """State: Z のプロセスに対して _pid_alive() が False を返す。"""
    with _patch(psutil.STATUS_ZOMBIE):
        assert monitor_mod._pid_alive(12345) is False


def test_pid_alive_returns_true_for_running(monitor_mod):
    """State: S (sleeping) のプロセスに対して True を返す。"""
    with _patch(psutil.STATUS_SLEEPING):
        assert monitor_mod._pid_alive(12345) is True


def test_pid_alive_returns_true_for_running_state_r(monitor_mod):
    """State: R (running) のプロセスに対して True を返す。"""
    with _patch(psutil.STATUS_RUNNING):
        assert monitor_mod._pid_alive(12345) is True


def test_pid_alive_returns_false_for_dead_process(monitor_mod):
    """pid の無いプロセスに対して False。"""
    with _patch(None):
        assert monitor_mod._pid_alive(99999) is False


def test_pid_alive_is_false_when_the_state_is_unreadable(monitor_mod):
    """状態を読めない（psutil が AccessDenied を返す）ときは False（#1142 の D3 で変わった入力）。

    `/proc/<pid>/status` を読んでいた間は、読めなければ `kill -0` の結果だけで True を返した。
    """
    with _patch(psutil.AccessDenied(12345)):
        assert monitor_mod._pid_alive(12345) is False


def test_is_zombie_helper(monitor_mod):
    """_is_zombie() ヘルパーの動作確認。"""
    with _patch(psutil.STATUS_ZOMBIE):
        assert monitor_mod._is_zombie(1) is True

    with _patch(psutil.STATUS_SLEEPING):
        assert monitor_mod._is_zombie(1) is False

    with _patch(None):
        assert monitor_mod._is_zombie(1) is False


def test_kill_pid_skips_zombie(monitor_mod):
    """ゾンビプロセスに対して _kill_pid() はシグナルを送らない。"""
    with (
        mock.patch.object(monitor_mod.monitor_proc, "_is_zombie", return_value=True),
        mock.patch("os.kill") as mock_kill,
    ):
        monitor_mod._kill_pid(12345)
        mock_kill.assert_not_called()
