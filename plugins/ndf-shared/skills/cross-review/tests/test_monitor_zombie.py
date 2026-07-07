"""monitor.py のゾンビプロセス検出テスト。

Docker without --init 環境では、nohup/disown で起動したプロセスが終了後に
ゾンビ化する。`_pid_alive()` が /proc/<pid>/status の State: Z を検出して
False を返すことを検証する。
"""
from __future__ import annotations

import pathlib
from unittest import mock


def test_pid_alive_returns_false_for_zombie(monitor_mod):
    """State: Z のプロセスに対して _pid_alive() が False を返す。"""
    proc_status = "Name:\tbash\nState:\tZ (zombie)\nTgid:\t12345\nPid:\t12345\n"

    with (
        mock.patch("os.kill", return_value=None),
        mock.patch.object(
            pathlib.Path, "read_text", return_value=proc_status
        ),
    ):
        assert monitor_mod._pid_alive(12345) is False


def test_pid_alive_returns_true_for_running(monitor_mod):
    """State: S (sleeping) のプロセスに対して True を返す。"""
    proc_status = "Name:\tgemini\nState:\tS (sleeping)\nTgid:\t12345\nPid:\t12345\n"

    with (
        mock.patch("os.kill", return_value=None),
        mock.patch.object(
            pathlib.Path, "read_text", return_value=proc_status
        ),
    ):
        assert monitor_mod._pid_alive(12345) is True


def test_pid_alive_returns_true_for_running_state_r(monitor_mod):
    """State: R (running) のプロセスに対して True を返す。"""
    proc_status = "Name:\tgemini\nState:\tR (running)\nTgid:\t12345\nPid:\t12345\n"

    with (
        mock.patch("os.kill", return_value=None),
        mock.patch.object(
            pathlib.Path, "read_text", return_value=proc_status
        ),
    ):
        assert monitor_mod._pid_alive(12345) is True


def test_pid_alive_returns_false_for_dead_process(monitor_mod):
    """kill -0 が ProcessLookupError を返すプロセスに対して False。"""
    with mock.patch("os.kill", side_effect=ProcessLookupError):
        assert monitor_mod._pid_alive(99999) is False


def test_pid_alive_proc_unreadable_falls_back_to_alive(monitor_mod):
    """/proc/<pid>/status が読めない場合 (non-Linux) は kill -0 の結果のみで True。"""
    with (
        mock.patch("os.kill", return_value=None),
        mock.patch.object(
            pathlib.Path, "read_text", side_effect=FileNotFoundError
        ),
    ):
        assert monitor_mod._pid_alive(12345) is True


def test_is_zombie_helper(monitor_mod):
    """_is_zombie() ヘルパーの動作確認。"""
    zombie_status = "Name:\tbash\nState:\tZ (zombie)\nTgid:\t1\nPid:\t1\n"
    alive_status = "Name:\tgemini\nState:\tS (sleeping)\nTgid:\t1\nPid:\t1\n"

    with mock.patch.object(pathlib.Path, "read_text", return_value=zombie_status):
        assert monitor_mod._is_zombie(1) is True

    with mock.patch.object(pathlib.Path, "read_text", return_value=alive_status):
        assert monitor_mod._is_zombie(1) is False

    with mock.patch.object(pathlib.Path, "read_text", side_effect=FileNotFoundError):
        assert monitor_mod._is_zombie(1) is False


def test_kill_pid_skips_zombie(monitor_mod):
    """ゾンビプロセスに対して _kill_pid() はシグナルを送らない。"""
    with (
        mock.patch.object(monitor_mod, "_is_zombie", return_value=True),
        mock.patch("os.kill") as mock_kill,
    ):
        monitor_mod._kill_pid(12345)
        mock_kill.assert_not_called()
