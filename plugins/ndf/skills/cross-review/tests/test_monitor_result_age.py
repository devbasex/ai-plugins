"""monitor.py の result.json + age fallback (RESULT_AGE_GRACE) テスト。

gemini がレビュー完了後にプロセスがハングするケースで、result.json の
mtime が RESULT_AGE_GRACE 秒以上前なら OK 判定する fallback の検証。
"""
from __future__ import annotations

import json
import os
import pathlib
import time
from unittest import mock


def test_result_age_grace_constant(monitor_mod):
    assert hasattr(monitor_mod, "RESULT_AGE_GRACE")
    assert monitor_mod.RESULT_AGE_GRACE == 30


def test_gemini_result_age_fallback_triggers_ok(monitor_mod, tmp_path):
    """result.json が RESULT_AGE_GRACE 秒以上前に書かれていれば OK で返る。"""
    pr = 999
    agent = "gemini"
    base = tmp_path / f"{agent}-review-pr{pr}"

    pidfile = pathlib.Path(f"{base}.pid")
    err_log = pathlib.Path(f"{base}-err.log")
    stdout_log = pathlib.Path(f"{base}-stdout.log")
    result = pathlib.Path(f"{base}-result.json")

    pidfile.write_text("12345")
    err_log.write_text("YOLO mode is enabled.\n")
    stdout_log.write_text("")
    result.write_text(json.dumps({"event": "APPROVE", "comments_count": 0}))
    # time.time() を side_effect で進める:
    #   call 1 (started_wall): base_time
    #   call 2+ (result age check): base_time + 40
    # result_mtime = base_time + 5 → started_wall(base_time) 以降かつ age=35s >= 30s
    base_time = time.time()
    result_mtime = base_time + 5
    os.utime(result, (result_mtime, result_mtime))

    time_call_count = [0]
    def fake_time():
        time_call_count[0] += 1
        if time_call_count[0] == 1:
            return base_time
        return base_time + 40

    kill_called = []
    poll_count = [0]

    def fake_pid_alive(pid):
        return True

    def fake_kill_pid(pid, sigterm_grace=3.0):
        kill_called.append(pid)

    def fake_sleep(s):
        poll_count[0] += 1
        if poll_count[0] > 5:
            raise RuntimeError("too many polls — fallback should have triggered")

    with (
        mock.patch.object(monitor_mod, "_tmp_dir", return_value=tmp_path),
        mock.patch.object(monitor_mod, "_pid_alive", side_effect=fake_pid_alive),
        mock.patch.object(monitor_mod, "_kill_pid", side_effect=fake_kill_pid),
        mock.patch.object(monitor_mod, "_pid_cmdline_matches", return_value=True),
        mock.patch("time.sleep", side_effect=fake_sleep),
        mock.patch("time.time", side_effect=fake_time),
    ):
        st = monitor_mod.monitor_agent(
            agent=agent, pr=pr,
            timeout=420, stall_timeout=480, poll=15,
            require_result=True, no_early_error=True,
        )

    assert st.status == "OK"
    assert st.exit_code == 0
    assert st.result_exists is True
    assert "result.json exists for" in st.detail
    assert 12345 in kill_called


def test_gemini_result_age_too_young_continues(monitor_mod, tmp_path):
    """result.json が書かれたばかり (age < RESULT_AGE_GRACE) なら fallback しない。"""
    pr = 998
    agent = "gemini"
    base = tmp_path / f"{agent}-review-pr{pr}"

    pidfile = pathlib.Path(f"{base}.pid")
    err_log = pathlib.Path(f"{base}-err.log")
    stdout_log = pathlib.Path(f"{base}-stdout.log")
    result = pathlib.Path(f"{base}-result.json")

    pidfile.write_text("12346")
    err_log.write_text("YOLO mode is enabled.\n")
    stdout_log.write_text("")
    result.write_text(json.dumps({"event": "APPROVE", "comments_count": 0}))
    # mtime を 5 秒前に設定 (RESULT_AGE_GRACE=30 未満)
    recent_mtime = time.time() - 5
    os.utime(result, (recent_mtime, recent_mtime))

    poll_count = [0]

    def fake_pid_alive(pid):
        if poll_count[0] >= 2:
            return False
        return True

    def fake_sleep(s):
        poll_count[0] += 1

    with (
        mock.patch.object(monitor_mod, "_tmp_dir", return_value=tmp_path),
        mock.patch.object(monitor_mod, "_pid_alive", side_effect=fake_pid_alive),
        mock.patch.object(monitor_mod, "_kill_pid"),
        mock.patch.object(monitor_mod, "_pid_cmdline_matches", return_value=None),
        mock.patch("time.sleep", side_effect=fake_sleep),
    ):
        st = monitor_mod.monitor_agent(
            agent=agent, pr=pr,
            timeout=420, stall_timeout=480, poll=15,
            require_result=True, no_early_error=True,
        )

    # result.json は young なので fallback せず、プロセス終了後の通常 OK になる
    assert st.status == "OK"
    assert st.exit_code == 0
    assert "process exited" in st.detail


def test_codex_sentinel_takes_priority(monitor_mod, tmp_path):
    """codex は sentinel + result.json が先に発火し、age fallback は通らない。"""
    pr = 997
    agent = "codex"
    base = tmp_path / f"{agent}-review-pr{pr}"

    pidfile = pathlib.Path(f"{base}.pid")
    err_log = pathlib.Path(f"{base}-err.log")
    stdout_log = pathlib.Path(f"{base}-stdout.log")
    result = pathlib.Path(f"{base}-result.json")

    pidfile.write_text("12347")
    err_log.write_text("some output\ntokens used\n")
    stdout_log.write_text("")
    result.write_text(json.dumps({"event": "APPROVE", "comments_count": 0}))
    old_mtime = time.time() - 60
    os.utime(result, (old_mtime, old_mtime))

    kill_called = []

    def fake_pid_alive(pid):
        return True

    def fake_kill_pid(pid, sigterm_grace=3.0):
        kill_called.append(pid)

    with (
        mock.patch.object(monitor_mod, "_tmp_dir", return_value=tmp_path),
        mock.patch.object(monitor_mod, "_pid_alive", side_effect=fake_pid_alive),
        mock.patch.object(monitor_mod, "_kill_pid", side_effect=fake_kill_pid),
        mock.patch.object(monitor_mod, "_pid_cmdline_matches", return_value=None),
        mock.patch("time.sleep"),
    ):
        st = monitor_mod.monitor_agent(
            agent=agent, pr=pr,
            timeout=420, stall_timeout=180, poll=15,
            require_result=True, no_early_error=True,
        )

    assert st.status == "OK"
    assert st.exit_code == 0
    # codex sentinel チェックによる OK
    assert "codex sentinel" in st.detail
    assert 12347 in kill_called


def test_stale_result_json_from_previous_round_ignored(monitor_mod, tmp_path):
    """前 round の古い result.json (mtime < started_wall) は fallback しない。"""
    pr = 996
    agent = "gemini"
    base = tmp_path / f"{agent}-review-pr{pr}"

    pidfile = pathlib.Path(f"{base}.pid")
    err_log = pathlib.Path(f"{base}-err.log")
    stdout_log = pathlib.Path(f"{base}-stdout.log")
    result = pathlib.Path(f"{base}-result.json")

    pidfile.write_text("12348")
    err_log.write_text("YOLO mode is enabled.\n")
    stdout_log.write_text("")
    result.write_text(json.dumps({"event": "APPROVE", "comments_count": 0}))
    # mtime を「現在の 120 秒前」に設定 → started_wall より前 → stale
    stale_mtime = time.time() - 120
    os.utime(result, (stale_mtime, stale_mtime))

    poll_count = [0]

    def fake_pid_alive(pid):
        if poll_count[0] >= 2:
            return False
        return True

    def fake_sleep(s):
        poll_count[0] += 1

    with (
        mock.patch.object(monitor_mod, "_tmp_dir", return_value=tmp_path),
        mock.patch.object(monitor_mod, "_pid_alive", side_effect=fake_pid_alive),
        mock.patch.object(monitor_mod, "_kill_pid"),
        mock.patch.object(monitor_mod, "_pid_cmdline_matches", return_value=True),
        mock.patch("time.sleep", side_effect=fake_sleep),
    ):
        st = monitor_mod.monitor_agent(
            agent=agent, pr=pr,
            timeout=420, stall_timeout=480, poll=15,
            require_result=True, no_early_error=True,
        )

    # stale result.json なので fallback せず、プロセス終了後の通常 OK
    assert st.status == "OK"
    assert "process exited" in st.detail


def test_cmdline_not_validated_skips_fallback(monitor_mod, tmp_path):
    """cmdline 未検証 (None) だと age fallback は発火しない。"""
    pr = 995
    agent = "gemini"
    base = tmp_path / f"{agent}-review-pr{pr}"

    pidfile = pathlib.Path(f"{base}.pid")
    err_log = pathlib.Path(f"{base}-err.log")
    stdout_log = pathlib.Path(f"{base}-stdout.log")
    result = pathlib.Path(f"{base}-result.json")

    pidfile.write_text("12349")
    err_log.write_text("YOLO mode is enabled.\n")
    stdout_log.write_text("")
    result.write_text(json.dumps({"event": "APPROVE", "comments_count": 0}))
    base_time = time.time()
    result_mtime = base_time + 5
    os.utime(result, (result_mtime, result_mtime))

    poll_count = [0]

    def fake_pid_alive(pid):
        if poll_count[0] >= 2:
            return False
        return True

    def fake_sleep(s):
        poll_count[0] += 1

    with (
        mock.patch.object(monitor_mod, "_tmp_dir", return_value=tmp_path),
        mock.patch.object(monitor_mod, "_pid_alive", side_effect=fake_pid_alive),
        mock.patch.object(monitor_mod, "_kill_pid"),
        # cmdline_matches が None → cmdline_validated は False のまま
        mock.patch.object(monitor_mod, "_pid_cmdline_matches", return_value=None),
        mock.patch("time.sleep", side_effect=fake_sleep),
    ):
        st = monitor_mod.monitor_agent(
            agent=agent, pr=pr,
            timeout=420, stall_timeout=480, poll=15,
            require_result=True, no_early_error=True,
        )

    # cmdline 未検証なので fallback せず、プロセス終了後の通常 OK
    assert st.status == "OK"
    assert "process exited" in st.detail
