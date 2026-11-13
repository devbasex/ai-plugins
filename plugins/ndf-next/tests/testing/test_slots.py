from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ndf.testing import slots, waits
from ndf.testing.limits import Limits

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
PLENTY = 10**9
POLL = 0.01


def _at(seconds: float) -> str:
    """記録の行の時刻（UTC の ISO 8601、秒まで）。実装の整形に頼らず作る。"""
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec="seconds")


def _limits(max_runs: int, wait: int = 60, floor: int = 1) -> Limits:
    return Limits(max_runs=max_runs, memory_floor_mib=floor, wait_limit_seconds=wait)


def _gate(directory: Path, limits: Limits, read_memory=lambda: PLENTY, environ=None):
    return slots.gate(
        "pytest .", directory, kind="whole", limits=limits, environ=environ or {}, directory=directory, poll=POLL, read_memory=read_memory
    )


class Counter:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.now = 0
        self.peak: dict[str, int] = {}
        self.now_by: dict[str, int] = {}

    def enter(self, group: str) -> None:
        with self.lock:
            self.now += 1
            self.now_by[group] = self.now_by.get(group, 0) + 1
            self.peak["all"] = max(self.peak.get("all", 0), self.now)
            self.peak[group] = max(self.peak.get(group, 0), self.now_by[group])

    def leave(self, group: str) -> None:
        with self.lock:
            self.now -= 1
            self.now_by[group] -= 1


def _run_many(directory: Path, groups: list[tuple[str, Limits]]) -> Counter:
    counter, errors = Counter(), []

    def body(group: str, limits: Limits) -> None:
        try:
            with _gate(directory, limits):
                counter.enter(group)
                time.sleep(0.05)
                counter.leave(group)
        except BaseException as exc:  # noqa: BLE001  （スレッドの失敗を本体へ渡す）
            errors.append(exc)

    threads = [threading.Thread(target=body, args=g) for g in groups]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    return counter


def test_runs_stay_within_the_limit(tmp_path):
    counter = _run_many(tmp_path, [("a", _limits(2))] * 4)
    assert counter.peak["all"] <= 2
    assert len(slots.read_events(tmp_path)) == 4


def test_runs_with_different_limits_share_the_numbered_slots(tmp_path):
    counter = _run_many(tmp_path, [("one", _limits(1))] * 2 + [("two", _limits(2))] * 2)
    assert counter.peak["all"] <= 2
    assert counter.peak["one"] <= 1


@pytest.mark.parametrize("environ", [{slots.HELD_ENV: "x"}, {"GITHUB_ACTIONS": "true"}])
def test_nested_and_ci_runs_take_no_slot(tmp_path, environ):
    with _gate(tmp_path, _limits(1)) as outer:
        with _gate(tmp_path, _limits(1, wait=1), environ={**environ, **outer.env}) as inner:
            assert inner.index is None
    assert outer.env == {slots.HELD_ENV: str(tmp_path / "slot-0")}


def test_light_command_takes_no_slot(tmp_path):
    with _gate(tmp_path, _limits(1)):
        with slots.gate(
            "pytest a.py",
            tmp_path,
            suites=[{"runner": "pytest", "command": "pytest ."}],
            environ={},
            directory=tmp_path,
            limits=_limits(1, wait=1),
        ) as held:
            assert held.index is None


def test_gives_up_after_the_wait_limit_and_records_it(tmp_path):
    with _gate(tmp_path, _limits(1)):
        with pytest.raises(slots.Stopped) as err:
            with _gate(tmp_path, _limits(1, wait=1)):
                pass
    assert err.value.reason == slots.GAVE_UP
    assert err.value.waited_seconds >= 1
    results = [row["result"] for row in slots.read_events(tmp_path)]
    assert results == [slots.GAVE_UP, slots.FINISHED]


def test_waits_for_memory_and_does_not_count_the_wait(tmp_path):
    readings = iter([10, 10, 10])
    started = time.monotonic()
    with _gate(tmp_path, _limits(1, floor=100), read_memory=lambda: next(readings, PLENTY)) as held:
        assert held.waited_seconds > 0
    assert slots.waited_since(started) >= held.waited_seconds
    row = slots.read_events(tmp_path)[-1]
    assert (
        row["result"] == slots.FINISHED
        and row["measured"] is True
        and set(row) >= {"at", "worktree", "kind", "waited_seconds", "available_mib", "oom_kill"}
    )


def test_unmeasurable_memory_starts_at_once(tmp_path):
    with _gate(tmp_path, _limits(1, floor=PLENTY), read_memory=lambda: None) as held:
        assert held.index == 0
    assert slots.read_events(tmp_path)[-1]["measured"] is False


def test_unusable_directory_still_runs(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    with slots.gate("pytest .", tmp_path, kind="whole", limits=_limits(1), environ={}, directory=blocker / "slots") as held:
        assert held.index is None and held.note


HOLDER = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path
    from ndf.testing import slots
    from ndf.testing.limits import Limits
    d = Path(sys.argv[1])
    with slots.gate("pytest .", d, kind="whole", limits=Limits(1, 1, 60), environ={}, directory=d, poll=0.01, read_memory=lambda: 10**9):
        (d / "holding").write_text("")
        time.sleep(600)
    """
)


def test_slot_frees_when_the_owner_is_killed(tmp_path):
    env = {**os.environ, "PYTHONPATH": str(PLUGIN_ROOT / "src")}
    holder = subprocess.Popen([sys.executable, "-c", HOLDER, str(tmp_path)], env=env)
    try:
        waits.wait_for_file(tmp_path / "holding")
        holder.kill()
        holder.wait()
        with waits.returns_before(10):
            with _gate(tmp_path, _limits(1, wait=10)) as held:
                assert held.index == 0
    finally:
        holder.kill()


def test_memory_stop_ends_only_the_last_started_run(tmp_path):
    low = threading.Event()
    calls = {"n": 0}

    def read_memory():
        calls["n"] += 1
        return 10 if low.is_set() else PLENTY

    first_entered, release_first, outcome = threading.Event(), threading.Event(), {}
    first_proc: dict[str, subprocess.Popen] = {}

    def first() -> None:
        with _gate(tmp_path, _limits(2, floor=100), read_memory=read_memory) as held:
            proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"], start_new_session=True)
            first_proc["p"] = proc
            held.watch(proc)
            first_entered.set()
            release_first.wait()
            proc.terminate()
            proc.wait()
        outcome["first"] = "finished"

    thread = threading.Thread(target=first)
    thread.start()
    first_entered.wait()
    with pytest.raises(slots.Stopped) as err:
        with _gate(tmp_path, _limits(2, floor=100), read_memory=read_memory) as held:
            proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"], start_new_session=True)
            held.watch(proc)
            low.set()
            proc.wait()
    assert err.value.reason == slots.MEMORY_STOPPED
    assert proc.returncode == -signal.SIGTERM
    # 止めた後の 30 秒は、残った先の 1 本を止めない（監視が何度も読んでも動き続ける）
    seen = calls["n"]
    waits.wait_until(lambda: calls["n"] >= seen + 10, what="先の実行の監視")
    assert first_proc["p"].poll() is None
    release_first.set()
    thread.join()
    assert outcome == {"first": "finished"}
    assert json.loads((tmp_path / "stop.json").read_text())["slot"] == 1
    assert [row["result"] for row in slots.read_events(tmp_path)] == [slots.MEMORY_STOPPED, slots.FINISHED]


def test_records_drop_old_and_excess_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(slots, "KEEP_ROWS", 5)
    now = time.time()
    old = {"at": _at(now - 31 * 86400), "result": "finished"}
    recent = {"at": _at(now - 60), "result": "finished"}
    (tmp_path / slots.EVENTS_FILE).write_text("\n".join(json.dumps(r) for r in [old] + [recent] * 5) + "\n")
    slots.append_event(tmp_path, {"at": _at(now), "result": "gave_up"}, now=now)
    rows = slots.read_events(tmp_path)
    assert len(rows) == 5 and rows[-1]["result"] == "gave_up" and old not in rows


def test_report_counts_and_never_lets_the_oom_increase_go_negative(tmp_path):
    now = time.time()
    rows = [
        {"at": _at(now - 9 * 86400), "waited_seconds": 99, "result": "gave_up", "oom_kill": 0},  # 期間の外
        {"at": _at(now - 300), "waited_seconds": 4, "result": "finished", "oom_kill": 5},
        {"at": _at(now - 200), "waited_seconds": 0, "result": "memory_stopped", "oom_kill": 7},
        {"at": _at(now - 100), "waited_seconds": 6, "result": "gave_up", "oom_kill": 1},  # 再起動で減った
    ]
    (tmp_path / slots.EVENTS_FILE).write_text("".join(json.dumps(r) + "\n" for r in rows))
    got = slots.report(tmp_path, 7 * 86400, now=now, current_oom=3)
    assert got == {"runs": 3, "waited_runs": 2, "waited_seconds": 10.0, "memory_stopped": 1, "gave_up": 1, "oom_kill_increase": 4}


# --- 現状固定テスト（report の経路: 記録が無い・読めない行・期間の境・並べ替え・OOM の数でない値） ---


def test_report_without_records_counts_zero(tmp_path):
    expected = {"runs": 0, "waited_runs": 0, "waited_seconds": 0, "memory_stopped": 0, "gave_up": 0, "oom_kill_increase": 0}
    assert slots.report(tmp_path, 86400, now=1000.0) == expected
    # 記録が無ければ今の回数だけでは増分にならない
    assert slots.report(tmp_path, 86400, now=1000.0, current_oom=5) == expected


def _write_rows(directory: Path, rows: list[dict], tail: str = "") -> None:
    (directory / slots.EVENTS_FILE).write_text("".join(json.dumps(r) + "\n" for r in rows) + tail)


def test_report_skips_unreadable_rows_sorts_by_time_and_ignores_non_integer_oom(tmp_path):
    now = 1_800_000_000.0
    iso = _at
    rows = [
        {"at": iso(now - 100), "waited_seconds": 0.04, "result": "finished", "oom_kill": 4},
        {"at": iso(now - 300), "waited_seconds": 0.03, "result": "finished", "oom_kill": 2},  # 書いた順でなく時刻の順に並べる
        {"at": "broken", "waited_seconds": 50, "result": "gave_up"},  # 読めない時刻は数えない
        {"waited_seconds": 50, "result": "gave_up"},  # 時刻の無い行は数えない
        {"at": iso(now - 50), "waited_seconds": None, "result": "memory_stopped", "oom_kill": True},  # 真偽値は回数でない
        {"at": iso(now - 40), "result": "interrupted", "oom_kill": "9"},  # 文字列は回数でない
        {"at": iso(now - 3600), "waited_seconds": 1, "result": "gave_up", "oom_kill": 1},  # 期間の境はちょうどで含む
        {"at": iso(now + 10), "waited_seconds": 2, "result": "gave_up", "oom_kill": 10},  # 先の時刻も含む
    ]
    _write_rows(tmp_path, rows, tail="not json\n[1]\n")
    whole = {"runs": 6, "waited_runs": 4, "waited_seconds": 3.1, "memory_stopped": 1, "gave_up": 2, "oom_kill_increase": 9}
    assert slots.report(tmp_path, 3600, now=now) == whole
    assert slots.report(tmp_path, 3600, now=now, current_oom=True) == whole  # 今の回数も真偽値なら数えない
    # 境を 1 秒縮めると境の行が外れ、今の回数（7）が並びの最後に付く（10 → 7 は負なので足さない）
    assert slots.report(tmp_path, 3599, now=now, current_oom=7) == {
        "runs": 5,
        "waited_runs": 3,
        "waited_seconds": 2.1,
        "memory_stopped": 1,
        "gave_up": 1,
        "oom_kill_increase": 8,
    }


def test_unwritable_slot_files_release_the_slot_and_run_without_it(tmp_path, monkeypatch):
    real_write = slots._write_json

    def refuse(path, data):
        raise PermissionError(13, "書けない", str(path))

    monkeypatch.setattr(slots, "_write_json", refuse)
    with _gate(tmp_path, _limits(1)) as held:
        assert held.index is None and held.env == {}
        assert held.note.startswith("枠の置き場へ書けないので枠を取らずに走らせた: ")
    monkeypatch.setattr(slots, "_write_json", real_write)
    with waits.returns_before(10):
        with _gate(tmp_path, _limits(1, wait=10)) as held:
            assert held.index == 0  # 取りかけた枠は返してある


def test_memory_stop_kills_descendants_that_outlive_the_group_leader(monkeypatch):
    monkeypatch.setattr(slots, "KILL_GRACE_SECONDS", 0.5)
    # 先頭の bash は SIGTERM で終わるが、SIGTERM を無視する子孫が残る
    proc = subprocess.Popen(
        ["bash", "-c", "(trap '' TERM; sleep 600) & echo started; wait"], stdout=subprocess.PIPE, start_new_session=True
    )
    try:
        assert proc.stdout.readline() == b"started\n"
        slots._stop_group(proc)
        waits.wait_until(lambda: not slots._group_alive(proc.pid), what="グループ全体の終了")
    finally:
        if slots._group_alive(proc.pid):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
