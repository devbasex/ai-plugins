from __future__ import annotations

import importlib.util
import pathlib
import sys


MONITOR_PATH = pathlib.Path(__file__).resolve().parents[1] / "lib" / "monitor.py"


def load_monitor():
    spec = importlib.util.spec_from_file_location("ndf_monitor", MONITOR_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.now = start

    def monotonic(self) -> float:
        return self.now

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def setup_monitor(monkeypatch, tmp_path, clock: FakeClock):
    monitor = load_monitor()
    monkeypatch.setattr(monitor, "_TMP_DIR_OVERRIDE", tmp_path)
    monkeypatch.setattr(monitor.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(monitor.time, "time", clock.time)
    monkeypatch.setattr(monitor.time, "sleep", clock.sleep)
    return monitor


def test_monitor_agent_reports_pidfile_bad_when_pidfile_is_missing(monkeypatch, tmp_path):
    monitor = setup_monitor(monkeypatch, tmp_path, FakeClock())

    status = monitor.monitor_agent(
        "codex", 435, timeout=60, stall_timeout=60, poll=2, require_result=True
    )

    assert status.status == "PIDFILE_BAD"
    assert status.exit_code == 6
    assert "pidfile not found" in status.detail


def test_monitor_agent_reports_timeout_and_kills_living_process(monkeypatch, tmp_path):
    clock = FakeClock()
    monitor = setup_monitor(monkeypatch, tmp_path, clock)
    paths = monitor.AgentPaths.for_("agy", 435)
    paths.pidfile.write_text("1234", encoding="utf-8")
    killed = []
    monkeypatch.setattr(monitor, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(monitor, "_pid_cmdline_matches", lambda pid, agent: True)
    monkeypatch.setattr(monitor, "_kill_pid", lambda pid: killed.append(pid))

    status = monitor.monitor_agent(
        "agy", 435, timeout=5, stall_timeout=60, poll=2, require_result=True
    )

    assert status.status == "TIMEOUT"
    assert status.exit_code == 2
    assert killed == [1234]


def test_monitor_agent_reports_ok_for_codex_sentinel_and_result(monkeypatch, tmp_path):
    monitor = setup_monitor(monkeypatch, tmp_path, FakeClock())
    paths = monitor.AgentPaths.for_("codex", 435)
    paths.pidfile.write_text("1234", encoding="utf-8")
    paths.err_log.write_text("tokens used\n", encoding="utf-8")
    paths.result.write_text("{}", encoding="utf-8")
    killed = []
    monkeypatch.setattr(monitor, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(monitor, "_pid_cmdline_matches", lambda pid, agent: True)
    monkeypatch.setattr(monitor, "_kill_pid", lambda pid: killed.append(pid))

    status = monitor.monitor_agent(
        "codex", 435, timeout=60, stall_timeout=60, poll=2, require_result=True
    )

    assert status.status == "OK"
    assert status.exit_code == 0
    assert status.result_exists is True
    assert status.sentinel_seen is True
    assert killed == [1234]


def test_monitor_agent_reports_no_result_after_process_exit(monkeypatch, tmp_path):
    monitor = setup_monitor(monkeypatch, tmp_path, FakeClock())
    paths = monitor.AgentPaths.for_("agy", 435)
    paths.pidfile.write_text("1234", encoding="utf-8")
    monkeypatch.setattr(monitor, "_pid_alive", lambda pid: False)

    status = monitor.monitor_agent(
        "agy", 435, timeout=60, stall_timeout=60, poll=2, require_result=True
    )

    assert status.status == "NO_RESULT"
    assert status.exit_code == 3
    assert "result.json missing" in status.detail


def test_monitor_agent_reports_stalled_when_log_sizes_do_not_change(monkeypatch, tmp_path):
    clock = FakeClock()
    monitor = setup_monitor(monkeypatch, tmp_path, clock)
    paths = monitor.AgentPaths.for_("agy", 435)
    paths.pidfile.write_text("1234", encoding="utf-8")
    paths.err_log.write_text("boot\n", encoding="utf-8")
    killed = []
    monkeypatch.setattr(monitor, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(monitor, "_pid_cmdline_matches", lambda pid, agent: True)
    monkeypatch.setattr(monitor, "_kill_pid", lambda pid: killed.append(pid))

    status = monitor.monitor_agent(
        "agy", 435, timeout=60, stall_timeout=5, poll=2, require_result=True
    )

    assert status.status == "STALLED"
    assert status.exit_code == 5
    assert status.idle_seconds >= 5
    assert killed == [1234]
