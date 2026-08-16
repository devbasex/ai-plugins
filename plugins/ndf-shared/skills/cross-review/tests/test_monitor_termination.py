"""monitor_agent の 5 つの終了経路を固定する現状固定テスト。

リファクタリング R2-001 で monitor_agent を分割する前に、
各終了経路の入出力と副作用を振る舞いとして記録する。

終了経路:
  1. codex sentinel + result.json → OK (kill + detail に "codex sentinel")
  2. result.json age fallback → OK (kill + detail に "result.json exists for")
  3. hard timeout → TIMEOUT (kill + exit_code=2)
  4. early error (FATAL) → EARLY_ERROR (kill + exit_code=4)
  5. stall detection → STALLED (kill + exit_code=5)

追加で固定する経路:
  6. プロセス正常終了 + result.json あり → OK (exit_code=0)
  7. プロセス正常終了 + result.json なし → NO_RESULT (exit_code=3)
  8. pidfile 不在 → PIDFILE_BAD (exit_code=6)
  9. cmdline 不一致 → PIDFILE_BAD (kill + exit_code=6)
"""
from __future__ import annotations

import json
import os
import pathlib
import time
from unittest import mock


# ---------- 1. codex sentinel + result.json ----------

def test_codex_sentinel_with_result_returns_ok_and_kills(monitor_mod, tmp_path):
    """codex sentinel + result.json が揃ったら即 OK で pid を kill する。"""
    pr = 800
    base = tmp_path / f"codex-review-pr{pr}"

    (pathlib.Path(f"{base}.pid")).write_text("11111")
    (pathlib.Path(f"{base}-err.log")).write_text("processing...\ntokens used\n")
    (pathlib.Path(f"{base}-stdout.log")).write_text("")
    (pathlib.Path(f"{base}-result.json")).write_text(
        json.dumps({"event": "APPROVE"})
    )

    killed = []

    with (
        mock.patch.object(monitor_mod, "_tmp_dir", return_value=tmp_path),
        mock.patch.object(monitor_mod, "_pid_alive", return_value=True),
        mock.patch.object(monitor_mod, "_kill_pid", side_effect=lambda p, **kw: killed.append(p)),
        mock.patch.object(monitor_mod, "_pid_cmdline_matches", return_value=None),
        mock.patch("time.sleep"),
    ):
        st = monitor_mod.monitor_agent(
            agent="codex", pr=pr, timeout=420, stall_timeout=180, poll=15,
            require_result=True, no_early_error=True,
        )

    assert st.status == "OK"
    assert st.exit_code == 0
    assert st.result_exists is True
    assert st.sentinel_seen is True
    assert "codex sentinel" in st.detail
    assert 11111 in killed


# ---------- 2. result.json age fallback ----------

def test_result_age_fallback_returns_ok_and_kills(monitor_mod, tmp_path):
    """result.json の age >= RESULT_AGE_GRACE で OK、pid を kill する。"""
    pr = 801
    base = tmp_path / f"gemini-review-pr{pr}"

    (pathlib.Path(f"{base}.pid")).write_text("22222")
    (pathlib.Path(f"{base}-err.log")).write_text("working...\n")
    (pathlib.Path(f"{base}-stdout.log")).write_text("")
    result = pathlib.Path(f"{base}-result.json")
    result.write_text(json.dumps({"event": "APPROVE"}))

    base_time = time.time()
    result_mtime = base_time + 5
    os.utime(result, (result_mtime, result_mtime))

    time_calls = [0]
    def fake_time():
        time_calls[0] += 1
        if time_calls[0] == 1:
            return base_time
        return base_time + 40  # age = 40 - 5 = 35s >= 30s

    killed = []

    with (
        mock.patch.object(monitor_mod, "_tmp_dir", return_value=tmp_path),
        mock.patch.object(monitor_mod, "_pid_alive", return_value=True),
        mock.patch.object(monitor_mod, "_kill_pid", side_effect=lambda p, **kw: killed.append(p)),
        mock.patch.object(monitor_mod, "_pid_cmdline_matches", return_value=True),
        mock.patch("time.sleep"),
        mock.patch("time.time", side_effect=fake_time),
    ):
        st = monitor_mod.monitor_agent(
            agent="gemini", pr=pr, timeout=420, stall_timeout=480, poll=15,
            require_result=True, no_early_error=True,
        )

    assert st.status == "OK"
    assert st.exit_code == 0
    assert st.result_exists is True
    assert "result.json exists for" in st.detail
    assert 22222 in killed


# ---------- 3. hard timeout ----------

def test_timeout_returns_timeout_and_kills(monitor_mod, tmp_path):
    """hard timeout に達したら TIMEOUT で pid を kill する。"""
    pr = 802
    base = tmp_path / f"gemini-review-pr{pr}"

    (pathlib.Path(f"{base}.pid")).write_text("33333")
    (pathlib.Path(f"{base}-err.log")).write_text("working...\n")
    (pathlib.Path(f"{base}-stdout.log")).write_text("")

    killed = []
    poll_count = [0]
    # monotonic を進めて timeout に到達させる
    mono_base = time.monotonic()

    def fake_pid_alive(pid):
        return True

    def fake_sleep(s):
        poll_count[0] += 1
        if poll_count[0] > 50:
            raise RuntimeError("too many polls")

    def fake_monotonic():
        # 1 回目: start, 以降: timeout 超え
        if poll_count[0] >= 1:
            return mono_base + 500  # > timeout=420
        return mono_base

    with (
        mock.patch.object(monitor_mod, "_tmp_dir", return_value=tmp_path),
        mock.patch.object(monitor_mod, "_pid_alive", side_effect=fake_pid_alive),
        mock.patch.object(monitor_mod, "_kill_pid", side_effect=lambda p, **kw: killed.append(p)),
        mock.patch.object(monitor_mod, "_pid_cmdline_matches", return_value=None),
        mock.patch("time.sleep", side_effect=fake_sleep),
        mock.patch("time.monotonic", side_effect=fake_monotonic),
    ):
        st = monitor_mod.monitor_agent(
            agent="gemini", pr=pr, timeout=420, stall_timeout=480, poll=15,
            require_result=True, no_early_error=True,
        )

    assert st.status == "TIMEOUT"
    assert st.exit_code == 2
    assert "hard timeout" in st.detail
    assert 33333 in killed


# ---------- 4. early error (FATAL) ----------

def test_early_error_fatal_returns_early_error_and_kills(monitor_mod, tmp_path):
    """致命エラーパターン検出で EARLY_ERROR、pid を kill する。"""
    pr = 803
    base = tmp_path / f"codex-review-pr{pr}"

    (pathlib.Path(f"{base}.pid")).write_text("44444")
    (pathlib.Path(f"{base}-err.log")).write_text("Authentication failed: token expired\n")
    (pathlib.Path(f"{base}-stdout.log")).write_text("")

    killed = []

    with (
        mock.patch.object(monitor_mod, "_tmp_dir", return_value=tmp_path),
        mock.patch.object(monitor_mod, "_pid_alive", return_value=True),
        mock.patch.object(monitor_mod, "_kill_pid", side_effect=lambda p, **kw: killed.append(p)),
        mock.patch.object(monitor_mod, "_pid_cmdline_matches", return_value=None),
        mock.patch("time.sleep"),
    ):
        st = monitor_mod.monitor_agent(
            agent="codex", pr=pr, timeout=420, stall_timeout=180, poll=15,
            require_result=True, no_early_error=False,
        )

    assert st.status == "EARLY_ERROR"
    assert st.exit_code == 4
    assert "early error (fatal)" in st.detail
    assert 44444 in killed


# ---------- 5. stall detection ----------

def test_stall_returns_stalled_and_kills(monitor_mod, tmp_path):
    """ログ無進捗が stall_timeout に達したら STALLED、pid を kill する。"""
    pr = 804
    base = tmp_path / f"codex-review-pr{pr}"

    (pathlib.Path(f"{base}.pid")).write_text("55555")
    err_log = pathlib.Path(f"{base}-err.log")
    err_log.write_text("initial\n")
    (pathlib.Path(f"{base}-stdout.log")).write_text("")

    killed = []
    poll_count = [0]
    mono_base = time.monotonic()

    def fake_pid_alive(pid):
        return True

    def fake_sleep(s):
        poll_count[0] += 1
        if poll_count[0] > 50:
            raise RuntimeError("too many polls")

    def fake_monotonic():
        # elapsed < timeout (420), idle >= stall_timeout (60)
        # poll_count で進める: idle が stall_timeout を超えるようにする
        if poll_count[0] >= 2:
            return mono_base + 100  # idle_seconds will exceed stall_timeout=60
        return mono_base

    with (
        mock.patch.object(monitor_mod, "_tmp_dir", return_value=tmp_path),
        mock.patch.object(monitor_mod, "_pid_alive", side_effect=fake_pid_alive),
        mock.patch.object(monitor_mod, "_kill_pid", side_effect=lambda p, **kw: killed.append(p)),
        mock.patch.object(monitor_mod, "_pid_cmdline_matches", return_value=None),
        mock.patch("time.sleep", side_effect=fake_sleep),
        mock.patch("time.monotonic", side_effect=fake_monotonic),
    ):
        st = monitor_mod.monitor_agent(
            agent="codex", pr=pr, timeout=420, stall_timeout=60, poll=15,
            require_result=True, no_early_error=True,
        )

    assert st.status == "STALLED"
    assert st.exit_code == 5
    assert "no log progress" in st.detail
    assert 55555 in killed


# ---------- 6. プロセス正常終了 + result.json あり ----------

def test_process_exit_with_result_returns_ok(monitor_mod, tmp_path):
    """プロセスが正常に終了し result.json があれば OK。"""
    pr = 805
    base = tmp_path / f"gemini-review-pr{pr}"

    (pathlib.Path(f"{base}.pid")).write_text("66666")
    (pathlib.Path(f"{base}-err.log")).write_text("done\n")
    (pathlib.Path(f"{base}-stdout.log")).write_text("")
    (pathlib.Path(f"{base}-result.json")).write_text(
        json.dumps({"event": "APPROVE"})
    )

    with (
        mock.patch.object(monitor_mod, "_tmp_dir", return_value=tmp_path),
        mock.patch.object(monitor_mod, "_pid_alive", return_value=False),
        mock.patch.object(monitor_mod, "_pid_cmdline_matches", return_value=None),
        mock.patch("time.sleep"),
    ):
        st = monitor_mod.monitor_agent(
            agent="gemini", pr=pr, timeout=420, stall_timeout=480, poll=15,
            require_result=True, no_early_error=True,
        )

    assert st.status == "OK"
    assert st.exit_code == 0
    assert st.result_exists is True
    assert "process exited" in st.detail


# ---------- 7. プロセス正常終了 + result.json なし ----------

def test_process_exit_without_result_returns_no_result(monitor_mod, tmp_path):
    """プロセスが終了したが result.json がなければ NO_RESULT。"""
    pr = 806
    base = tmp_path / f"gemini-review-pr{pr}"

    (pathlib.Path(f"{base}.pid")).write_text("77777")
    (pathlib.Path(f"{base}-err.log")).write_text("crashed\n")
    (pathlib.Path(f"{base}-stdout.log")).write_text("")

    with (
        mock.patch.object(monitor_mod, "_tmp_dir", return_value=tmp_path),
        mock.patch.object(monitor_mod, "_pid_alive", return_value=False),
        mock.patch.object(monitor_mod, "_pid_cmdline_matches", return_value=None),
        mock.patch("time.sleep"),
    ):
        st = monitor_mod.monitor_agent(
            agent="gemini", pr=pr, timeout=420, stall_timeout=480, poll=15,
            require_result=True, no_early_error=True,
        )

    assert st.status == "NO_RESULT"
    assert st.exit_code == 3
    assert "result.json missing" in st.detail


# ---------- 8. pidfile 不在 ----------

def test_no_pidfile_returns_pidfile_bad(monitor_mod, tmp_path):
    """pidfile が見つからなければ PIDFILE_BAD。"""
    pr = 807

    with (
        mock.patch.object(monitor_mod, "_tmp_dir", return_value=tmp_path),
        mock.patch("time.sleep"),
    ):
        st = monitor_mod.monitor_agent(
            agent="codex", pr=pr, timeout=420, stall_timeout=180, poll=15,
            require_result=True, no_early_error=True,
        )

    assert st.status == "PIDFILE_BAD"
    assert st.exit_code == 6
    assert "pidfile not found" in st.detail


# ---------- 9. cmdline 不一致 ----------

def test_cmdline_mismatch_returns_pidfile_bad_and_kills(monitor_mod, tmp_path):
    """cmdline に agent 名がなければ PIDFILE_BAD で kill する。"""
    pr = 808
    base = tmp_path / f"codex-review-pr{pr}"

    (pathlib.Path(f"{base}.pid")).write_text("88888")
    (pathlib.Path(f"{base}-err.log")).write_text("")
    (pathlib.Path(f"{base}-stdout.log")).write_text("")

    killed = []

    with (
        mock.patch.object(monitor_mod, "_tmp_dir", return_value=tmp_path),
        mock.patch.object(monitor_mod, "_pid_alive", return_value=True),
        mock.patch.object(monitor_mod, "_kill_pid", side_effect=lambda p, **kw: killed.append(p)),
        mock.patch.object(monitor_mod, "_pid_cmdline_matches", return_value=False),
        mock.patch("time.sleep"),
    ):
        st = monitor_mod.monitor_agent(
            agent="codex", pr=pr, timeout=420, stall_timeout=180, poll=15,
            require_result=True, no_early_error=True,
        )

    assert st.status == "PIDFILE_BAD"
    assert st.exit_code == 6
    assert "cmdline does not contain" in st.detail
    assert 88888 in killed


# ---------- 10. early error WARN は kill しない ----------

def test_early_error_warn_does_not_kill(monitor_mod, tmp_path):
    """WARN パターンは kill せず、プロセス終了後に OK で返る。"""
    pr = 809
    base = tmp_path / f"codex-review-pr{pr}"

    (pathlib.Path(f"{base}.pid")).write_text("99999")
    (pathlib.Path(f"{base}-err.log")).write_text("Error: something non-fatal\n")
    (pathlib.Path(f"{base}-stdout.log")).write_text("")
    (pathlib.Path(f"{base}-result.json")).write_text(
        json.dumps({"event": "APPROVE"})
    )

    poll_count = [0]

    def fake_pid_alive(pid):
        if poll_count[0] >= 1:
            return False
        return True

    def fake_sleep(s):
        poll_count[0] += 1

    killed = []

    with (
        mock.patch.object(monitor_mod, "_tmp_dir", return_value=tmp_path),
        mock.patch.object(monitor_mod, "_pid_alive", side_effect=fake_pid_alive),
        mock.patch.object(monitor_mod, "_kill_pid", side_effect=lambda p, **kw: killed.append(p)),
        mock.patch.object(monitor_mod, "_pid_cmdline_matches", return_value=None),
        mock.patch("time.sleep", side_effect=fake_sleep),
    ):
        st = monitor_mod.monitor_agent(
            agent="codex", pr=pr, timeout=420, stall_timeout=180, poll=15,
            require_result=True, no_early_error=False,
        )

    assert st.status == "OK"
    assert st.exit_code == 0
    assert killed == []  # WARN does not kill


# ---------- 11. no_early_error フラグが FATAL を抑制する ----------

def test_no_early_error_suppresses_fatal_detection(monitor_mod, tmp_path):
    """no_early_error=True のとき、FATAL パターンがあっても検知しない。"""
    pr = 810
    base = tmp_path / f"codex-review-pr{pr}"

    (pathlib.Path(f"{base}.pid")).write_text("10101")
    (pathlib.Path(f"{base}-err.log")).write_text("Authentication failed: bad token\n")
    (pathlib.Path(f"{base}-stdout.log")).write_text("")
    (pathlib.Path(f"{base}-result.json")).write_text(
        json.dumps({"event": "APPROVE"})
    )

    poll_count = [0]

    def fake_pid_alive(pid):
        if poll_count[0] >= 1:
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
            agent="codex", pr=pr, timeout=420, stall_timeout=180, poll=15,
            require_result=True, no_early_error=True,
        )

    assert st.status == "OK"
    assert st.exit_code == 0


# ---------- 12. claude stdout fatal ----------

def test_claude_stdout_fatal_returns_early_error(monitor_mod, tmp_path):
    """claude の stdout.log に致命パターンがあれば EARLY_ERROR。"""
    pr = 811
    base = tmp_path / f"claude-review-pr{pr}"

    (pathlib.Path(f"{base}.pid")).write_text("12121")
    (pathlib.Path(f"{base}-err.log")).write_text("")
    stdout = pathlib.Path(f"{base}-stdout.log")
    stdout.write_text(json.dumps({
        "type": "result", "is_error": True, "permission_denials": [],
    }))

    killed = []

    with (
        mock.patch.object(monitor_mod, "_tmp_dir", return_value=tmp_path),
        mock.patch.object(monitor_mod, "_pid_alive", return_value=True),
        mock.patch.object(monitor_mod, "_kill_pid", side_effect=lambda p, **kw: killed.append(p)),
        mock.patch.object(monitor_mod, "_pid_cmdline_matches", return_value=None),
        mock.patch("time.sleep"),
    ):
        st = monitor_mod.monitor_agent(
            agent="claude", pr=pr, timeout=420, stall_timeout=900, poll=15,
            require_result=True, no_early_error=False,
        )

    assert st.status == "EARLY_ERROR"
    assert st.exit_code == 4
    assert "stdout.log" in st.detail
    assert 12121 in killed
