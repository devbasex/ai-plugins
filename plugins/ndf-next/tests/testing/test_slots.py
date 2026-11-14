from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from ndf import identity, slot_pool
from ndf.testing import records, slots, waits
from ndf.testing.limits import Limits

PLENTY = 10**9
POLL = 0.01


def _limits(max_runs: int, wait: int = 60, floor: int = 1) -> Limits:
    return Limits(max_runs=max_runs, memory_floor_mib=floor, wait_limit_seconds=wait)


def _gate(directory: Path, limits: Limits, read_memory=lambda: PLENTY, environ=None):
    return slots.gate(
        "pytest .",
        directory,
        options=slots.GateOptions(
            kind="whole", limits=limits, environ=environ or {}, directory=directory, poll=POLL, read_memory=read_memory
        ),
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
    assert len(records.read_events(tmp_path)) == 4


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
            options=slots.GateOptions(
                suites=[{"runner": "pytest", "command": "pytest ."}], limits=_limits(1, wait=1), environ={}, directory=tmp_path
            ),
        ) as held:
            assert held.index is None


def test_gives_up_after_the_wait_limit_and_records_it(tmp_path):
    with _gate(tmp_path, _limits(1)):
        with pytest.raises(slots.Stopped) as err:
            with _gate(tmp_path, _limits(1, wait=1)):
                pass
    assert err.value.reason == slots.GAVE_UP
    assert err.value.waited_seconds >= 1
    results = [row["result"] for row in records.read_events(tmp_path)]
    assert results == [slots.GAVE_UP, slots.FINISHED]


def test_llm_cli_launches_count_against_the_test_slot(tmp_path):
    # 枠 1 本分の容量に LLM の CLI が 1 本でもいれば、テストは待つ（#3368）
    limits = _limits(1, wait=1)
    cli = slot_pool.take(tmp_path, limits.capacity_mib, slot_pool.weight_mib("llm-cli"), {"pid": os.getpid()})
    assert cli is not None
    try:
        with pytest.raises(slots.Stopped) as err:
            with _gate(tmp_path, limits):
                pass
        assert err.value.reason == slots.GAVE_UP
    finally:
        cli.release()
    with _gate(tmp_path, limits) as held:
        assert held.index is not None


def test_waits_for_memory_and_does_not_count_the_wait(tmp_path):
    readings = iter([10, 10, 10])
    started = time.monotonic()
    with _gate(tmp_path, _limits(1, floor=100), read_memory=lambda: next(readings, PLENTY)) as held:
        assert held.waited_seconds > 0
    assert slots.waited_since(started) >= held.waited_seconds
    row = records.read_events(tmp_path)[-1]
    assert (
        row["result"] == slots.FINISHED
        and row["measured"] is True
        and set(row) >= {"at", "worktree", "kind", "waited_seconds", "available_mib", "oom_kill"}
    )


def test_unmeasurable_memory_starts_at_once(tmp_path):
    with _gate(tmp_path, _limits(1, floor=PLENTY), read_memory=lambda: None) as held:
        assert held.index == 0
    assert records.read_events(tmp_path)[-1]["measured"] is False


def test_unusable_directory_still_runs(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    with slots.gate(
        "pytest .", tmp_path, options=slots.GateOptions(kind="whole", limits=_limits(1), environ={}, directory=blocker / "slots")
    ) as held:
        assert held.index is None and held.note


HOLDER = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path
    from ndf.testing import slots
    from ndf.testing.limits import Limits
    d = Path(sys.argv[1])
    with slots.gate("pytest .", d, options=slots.GateOptions(kind="whole", limits=Limits(1, 1, 60), environ={}, directory=d, poll=0.01, read_memory=lambda: 10**9)):
        (d / "holding").write_text("")
        time.sleep(600)
    """
)


def test_slot_frees_when_the_owner_is_killed(tmp_path, src_env):
    holder = subprocess.Popen([sys.executable, "-c", HOLDER, str(tmp_path)], env=src_env)
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
    assert [row["result"] for row in records.read_events(tmp_path)] == [slots.MEMORY_STOPPED, slots.FINISHED]


def test_unwritable_slot_files_release_the_slot_and_run_without_it(tmp_path, monkeypatch):
    real_write = slot_pool.write_json

    def refuse(path, data):
        raise PermissionError(13, "書けない", str(path))

    monkeypatch.setattr(slot_pool, "write_json", refuse)
    with _gate(tmp_path, _limits(1)) as held:
        assert held.index is None and held.env == {}
        assert held.note.startswith("枠の置き場へ書けないので枠を取らずに走らせた: ")
    monkeypatch.setattr(slot_pool, "write_json", real_write)
    with waits.returns_before(10):
        with _gate(tmp_path, _limits(1, wait=10)) as held:
            assert held.index == 0  # 取りかけた枠は返してある


@pytest.mark.skipif(not os.path.exists("/proc/self/oom_score_adj"), reason="OOM の優先度は Linux だけ")
def test_preexec_raises_the_oom_score_before_the_child_forks():
    # bash が fork した子孫（cd ... && の後ろのコマンド）も、exec の前に上げた優先度を受け継ぐ
    if int(Path("/proc/self/oom_score_adj").read_text()) > slots.machine.TEST_OOM_SCORE_ADJ:
        pytest.skip("既に優先度が高く、権限なしでは下げられない")
    out = subprocess.run(
        ["bash", "-c", "cd / && cat /proc/self/oom_score_adj"],
        preexec_fn=slots.Held(index=0).preexec,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert out.strip() == str(slots.machine.TEST_OOM_SCORE_ADJ)


def test_preexec_is_none_when_running_without_a_slot():
    assert slots.Held().preexec is None


@pytest.mark.parametrize("in_child", [False, True])
def test_interrupt_after_retiring_carries_the_retired_places(tmp_path, monkeypatch, in_child):
    # 旧い状態の置き場を退避した後の中断（待ちの間・子の実行中）は、退避の元と先を持つ Interrupted で抜ける
    retired = [{"retired_from": "/s/p", "retired_to": "/s/p.v10-20261114"}]
    monkeypatch.setattr(slots, "slots_place", lambda env: (tmp_path / "slots", retired))
    options = slots.GateOptions(
        kind="whole", limits=_limits(1), environ={}, poll=POLL, read_memory=lambda: PLENTY, interrupted=lambda: not in_child
    )

    with pytest.raises(slots.Interrupted) as caught:
        with slots.gate("pytest .", tmp_path, options=options):
            raise KeyboardInterrupt

    assert caught.value.retired == retired


def test_gate_keeps_the_places_retired_before_preparing_failed(tmp_path, monkeypatch):
    # 退避の途中で失敗して枠を取らずに走らせるときも、先に退避した元と先を Held に残す
    retired = (identity.Retired(Path("/s/p"), Path("/s/p.v10-20261114")),)

    def fail(env):
        raise identity.IdentityError("read-only", retired)

    monkeypatch.setattr(slots, "slots_place", fail)
    options = slots.GateOptions(kind="whole", limits=_limits(1), environ={}, poll=POLL, read_memory=lambda: PLENTY)

    with slots.gate("pytest .", tmp_path, options=options) as held:
        pass

    assert held.index is None
    assert held.retired == [{"retired_from": "/s/p", "retired_to": "/s/p.v10-20261114"}]


def _alive(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return False
    return state != "Z"


def test_interrupted_run_command_stops_its_process_group_before_releasing(tmp_path, monkeypatch):
    # 利用者が検証中に Ctrl+C で止めても、起動した組（孫まで）を止めてから抜ける
    pidfile = tmp_path / "pid"

    def interrupted(self, *a, **k):
        waits.wait_until(lambda: pidfile.exists() and pidfile.read_text().strip(), timeout=10, interval=POLL)
        raise KeyboardInterrupt

    monkeypatch.setattr(subprocess.Popen, "communicate", interrupted)
    with pytest.raises(KeyboardInterrupt):
        slots.run_command(f"sleep 30 & echo $! > {pidfile}; wait", tmp_path, 60, suites=[])
    monkeypatch.undo()
    pid = int(pidfile.read_text())
    waits.wait_until(lambda: not _alive(pid), timeout=10, interval=POLL, what="孫のプロセスが止まる")
