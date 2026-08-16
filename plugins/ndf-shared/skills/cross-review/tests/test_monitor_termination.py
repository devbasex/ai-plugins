"""monitor_agent の終了判定を固定するテスト。"""
from __future__ import annotations

from unittest import mock


def _prepare(tmp_path, agent: str, pr: int) -> None:
    base = tmp_path / f"{agent}-review-pr{pr}"
    (tmp_path / f"{base.name}.pid").write_text("12345")
    (tmp_path / f"{base.name}-err.log").write_text("")
    (tmp_path / f"{base.name}-stdout.log").write_text("")


def _monitor(monitor_mod, tmp_path, *, alive=True, timeout=420, stall_timeout=480):
    with (
        mock.patch.object(monitor_mod, "_tmp_dir", return_value=tmp_path),
        mock.patch.object(monitor_mod, "_pid_alive", return_value=alive),
        mock.patch.object(monitor_mod, "_pid_cmdline_matches", return_value=True),
        mock.patch.object(monitor_mod, "_kill_pid") as kill,
        mock.patch("time.sleep"),
    ):
        status = monitor_mod.monitor_agent(
            agent="gemini", pr=901, timeout=timeout, stall_timeout=stall_timeout,
            poll=1, require_result=True,
        )
    return status, kill


def test_hard_timeout_kills_live_process(monitor_mod, tmp_path):
    _prepare(tmp_path, "gemini", 901)
    status, kill = _monitor(monitor_mod, tmp_path, timeout=0)
    assert (status.status, status.exit_code) == ("TIMEOUT", 2)
    kill.assert_called_once_with(12345)


def test_fatal_log_kills_live_process(monitor_mod, tmp_path):
    _prepare(tmp_path, "gemini", 901)
    (tmp_path / "gemini-review-pr901-err.log").write_text("Error: quota exceeded\n")
    status, kill = _monitor(monitor_mod, tmp_path)
    assert (status.status, status.exit_code) == ("EARLY_ERROR", 4)
    assert "err.log" in status.detail
    kill.assert_called_once_with(12345)


def test_exited_process_without_result_is_reported(monitor_mod, tmp_path):
    _prepare(tmp_path, "gemini", 901)
    status, kill = _monitor(monitor_mod, tmp_path, alive=False)
    assert (status.status, status.exit_code) == ("NO_RESULT", 3)
    kill.assert_not_called()


def test_stall_kills_live_process(monitor_mod, tmp_path):
    _prepare(tmp_path, "gemini", 901)
    status, kill = _monitor(monitor_mod, tmp_path, stall_timeout=0)
    assert (status.status, status.exit_code) == ("STALLED", 5)
    assert status.err_log_size == 0
    assert status.stdout_log_size == 0
    kill.assert_called_once_with(12345)
