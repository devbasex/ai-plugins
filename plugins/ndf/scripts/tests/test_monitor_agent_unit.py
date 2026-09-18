"""monitor_agent の現状固定テスト。"""
from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys
import time

import pytest

LIB = pathlib.Path(__file__).resolve().parents[1] / "lib"
MONITOR_PATH = LIB / "monitor.py"


def _load_monitor():
    if str(LIB) not in sys.path:
        sys.path.insert(0, str(LIB))
    name = "ndf_lib_monitor_unit"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, MONITOR_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def monitor(tmp_path, monkeypatch):
    monkeypatch.setenv("CROSS_REVIEW_TMP_DIR", str(tmp_path))
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    mod = _load_monitor()
    monkeypatch.setattr(mod, "_pid_cmdline_matches", lambda _p, _a: True)
    return mod


def test_monitor_agent_pidfile_bad_when_empty(monitor, tmp_path):
    paths = monitor.AgentPaths.for_("agy", 1)
    paths.pidfile.write_text("", encoding="utf-8")

    config = monitor.MonitorConfig(timeout=10, stall_timeout=10, poll=0, require_result=True)
    status = monitor.monitor_agent("agy", 1, config)
    assert status.status == "PIDFILE_BAD"
    assert status.exit_code == 6
    assert "pidfile not found" in status.detail


def test_monitor_agent_ok_process_exited(monitor, tmp_path):
    p = subprocess.Popen(["true"])
    p.wait()

    paths = monitor.AgentPaths.for_("agy", 1)
    paths.pidfile.write_text(str(p.pid), encoding="utf-8")
    paths.result.write_text('{"status": "ok"}', encoding="utf-8")

    config = monitor.MonitorConfig(timeout=10, stall_timeout=10, poll=0, require_result=True)
    status = monitor.monitor_agent("agy", 1, config)

    assert status.status == "OK"
    assert status.exit_code == 0
    assert status.result_exists is True
    assert "process exited" in status.detail


def test_monitor_agent_no_result_when_process_exited(monitor, tmp_path):
    p = subprocess.Popen(["true"])
    p.wait()

    paths = monitor.AgentPaths.for_("agy", 1)
    paths.pidfile.write_text(str(p.pid), encoding="utf-8")

    config = monitor.MonitorConfig(timeout=10, stall_timeout=10, poll=0, require_result=True)
    status = monitor.monitor_agent("agy", 1, config)

    assert status.status == "NO_RESULT"
    assert status.exit_code == 3
    assert status.result_exists is False
    assert "result.json missing" in status.detail


def test_monitor_agent_timeout(monitor, tmp_path):
    p = subprocess.Popen(["sleep", "60"])
    try:
        paths = monitor.AgentPaths.for_("agy", 1)
        paths.pidfile.write_text(str(p.pid), encoding="utf-8")

        config = monitor.MonitorConfig(timeout=0, stall_timeout=10, poll=0, require_result=True)
        status = monitor.monitor_agent("agy", 1, config)

        assert status.status == "TIMEOUT"
        assert status.exit_code == 2
        assert "hard timeout" in status.detail
    finally:
        p.kill()
        p.wait()


def test_monitor_agent_stalled(monitor, tmp_path):
    p = subprocess.Popen(["sleep", "60"])
    try:
        paths = monitor.AgentPaths.for_("agy", 1)
        paths.pidfile.write_text(str(p.pid), encoding="utf-8")

        config = monitor.MonitorConfig(timeout=10, stall_timeout=0, poll=0, require_result=True)
        status = monitor.monitor_agent("agy", 1, config)

        assert status.status == "STALLED"
        assert status.exit_code == 5
        assert "no log progress" in status.detail
    finally:
        p.kill()
        p.wait()
