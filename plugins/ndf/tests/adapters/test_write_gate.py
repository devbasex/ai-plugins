"""書き込みの順番をマシン全体で取る（#4016 の u1。WriteGate の本体）。

時計と眠りは差し替え、記録は `tmp_path` に置く。本物のホームの下（`~/.local/state/ndf-github/`）を書かない。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Optional

import httpx
import pytest

from ndf import slot_pool
from ndf.adapters import write_gate
from ndf.adapters.write_gate import CONTENT, WRITE, Writes
from ndf.contracts import limits

NOW = 1_000_000.0
EPS = 1e-6
NDF_SRC = Path(write_gate.__file__).resolve().parents[2]


def spacing(name: str) -> float:
    return limits.value(name, "interval")


W = spacing(write_gate.WRITE_SPACING)
C = spacing(write_gate.CONTENT_SPACING)


class Clock:
    """偽の時計。眠りは時計を進めて秒を残す。`on_sleep` は眠りのたびに呼ぶ。"""

    def __init__(self, now: float = NOW) -> None:
        self.now = now
        self.slept: list[float] = []
        self.on_sleep: Optional[Callable[[], None]] = None

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds
        if self.on_sleep is not None:
            self.on_sleep()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def path(tmp_path) -> Path:
    return tmp_path / "state" / write_gate.GATE_FILE


@pytest.fixture
def gate(path, clock) -> write_gate.WriteGate:
    return write_gate.WriteGate(path, clock, clock.sleep)


@pytest.fixture
def short_locks(monkeypatch):
    """記録のロックと送信のロックの待ちの上限を短くする。"""
    monkeypatch.setattr(write_gate, "LOCK_SECONDS", 0.05)
    real = limits.value
    monkeypatch.setattr(limits, "value", lambda name, key: 0.05 if name == write_gate.SEND_LOCK else real(name, key))


def record(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def seed(path: Path, **times: float) -> None:
    """記録を書いた形で置く（書いた時刻は今）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    base = dict.fromkeys(write_gate.TIMES, 0.0)
    path.write_text(json.dumps({"version": 1, **base, "written_at": NOW, **times}), encoding="utf-8")


def held(path: Path):
    lock = slot_pool.lock(path)
    lock.acquire()
    return lock


def graphql(query: str) -> httpx.Request:
    return httpx.Request("POST", "https://api.github.com/graphql", json={"query": query})


# write_of


@pytest.mark.parametrize(
    ("request_", "expected"),
    [
        (httpx.Request("GET", "https://api.github.com/repos/o/r/issues"), None),
        (httpx.Request("HEAD", "https://api.github.com/repos/o/r"), None),
        (httpx.Request("POST", "https://api.github.com/repos/o/r/issues", json={"title": "t"}), Writes(CONTENT, 1)),
        (httpx.Request("PATCH", "https://api.github.com/repos/o/r/issues/1", json={}), Writes(WRITE, 1)),
        (httpx.Request("PUT", "https://api.github.com/repos/o/r/pulls/1/merge"), Writes(WRITE, 1)),
        (httpx.Request("DELETE", "https://api.github.com/repos/o/r/labels/x"), Writes(WRITE, 1)),
        (graphql("query { viewer { login } }"), None),
        (graphql("{ viewer { login } }"), None),
        (graphql('mutation { resolveReviewThread(input: {threadId: "T"}) { thread { id } } }'), Writes(WRITE, 1)),
        (graphql('mutation { addComment(input: {subjectId: "I", body: "b"}) { clientMutationId } }'), Writes(CONTENT, 1)),
        (graphql("mutation($i: CreateIssueInput!) { createIssue(input: $i) { issue { id } } }"), Writes(CONTENT, 1)),
    ],
)
def test_write_of_splits_reads_writes_and_content(request_, expected):
    assert write_gate.write_of(request_) == expected


def test_write_of_skips_leading_whitespace_and_comments():
    query = (
        '\n   \t# 前置きのコメント { "x"\n  # もう 1 行\n mutation Add($id: ID!) { addComment(input: {subjectId: $id, body: "#"}) { x } }'
    )

    assert write_gate.write_of(graphql(query)) == Writes(CONTENT, 1)


def test_write_of_reads_the_field_name_behind_an_alias():
    assert write_gate.write_of(graphql("mutation { createIssue: resolveReviewThread(input: {}) { x } }")) == Writes(WRITE, 1)


def test_write_of_counts_a_graphql_post_that_is_not_json_as_a_write():
    request = httpx.Request("POST", "https://api.github.com/graphql", content=b"mutation { addComment }")

    assert write_gate.write_of(request) == Writes(WRITE, 1)
    assert write_gate.write_of(httpx.Request("POST", "https://api.github.com/graphql", json={"query": 1})) == Writes(WRITE, 1)


# reserve


def test_two_writes_in_a_row_are_spaced(gate):
    first = gate.reserve(WRITE)
    second = gate.reserve(WRITE)

    assert first.wait == 0 and first.at == NOW
    assert second.wait == pytest.approx(W)


def test_content_after_content_waits_the_content_spacing(gate):
    gate.reserve(CONTENT)

    assert gate.reserve(CONTENT).wait == pytest.approx(C)
    assert gate.reserve(WRITE).wait == pytest.approx(C + W)


def test_a_reservation_at_exactly_now_does_not_wait(gate, path, clock):
    seed(path, next=NOW, next_content=NOW)

    got = gate.reserve(CONTENT)

    assert got.wait == 0 and got.at == NOW
    assert record(path)["next_content"] == pytest.approx(NOW + C)


def test_a_long_block_is_kept_and_waited(gate):
    gate.block(3600)

    assert gate.reserve(WRITE).wait == pytest.approx(3600)


def test_a_reservation_behind_six_contents_is_kept(gate):
    for _ in range(6):
        gate.reserve(CONTENT)

    assert gate.reserve(CONTENT).wait == pytest.approx(6 * C)


def test_hold_spaces_the_following_reservations_by_its_count(gate):
    with gate.hold(CONTENT, 3):
        pass

    assert gate.reserve(CONTENT).wait == pytest.approx(3 * C)
    assert gate.reserve(WRITE).wait == pytest.approx(3 * C + W)


def test_a_rewound_wall_clock_shifts_the_record_and_keeps_the_remaining_block(gate, path):
    hour = 3600.0
    seed(path, next=NOW + hour + W, blocked_until=NOW + hour + 30, written_at=NOW + hour)

    got = gate.reserve(WRITE)

    assert got.at == pytest.approx(NOW + 30)
    assert record(path)["next"] == pytest.approx(NOW + 30 + W)


def test_a_broken_record_is_read_as_empty_and_rewritten(gate, path, capsys):
    path.parent.mkdir(parents=True)
    path.write_text("{", encoding="utf-8")

    assert gate.reserve(WRITE).wait == 0
    assert "書き込みの順番の記録を読めない" in capsys.readouterr().err
    assert record(path)["next"] == pytest.approx(NOW + W)


def test_without_the_record_lock_it_spaces_within_the_process(gate, path, capsys, short_locks):
    path.parent.mkdir(parents=True)
    lock = held(path.with_name(path.name + ".lock"))
    try:
        first = gate.reserve(WRITE)
        second = gate.reserve(WRITE)
    finally:
        lock.release()

    assert first.wait == 0
    assert second.wait == pytest.approx(W)
    assert "書き込みの順番の記録のロックを取れない" in capsys.readouterr().err
    assert not path.exists()


def test_eight_threads_get_distinct_spaced_reservations(path):
    gate = write_gate.WriteGate(path)
    start = threading.Barrier(8)
    got: list[float] = []

    def take() -> None:
        start.wait()
        got.append(gate.reserve(WRITE).at)

    threads = [threading.Thread(target=take) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ats = sorted(got)
    assert len(set(ats)) == 8
    assert all(b - a >= W - EPS for a, b in zip(ats, ats[1:]))


CHILD = """
import json, os, pwd, sys, time
from pathlib import Path

mode, where, go = sys.argv[1:4]
if mode == "home":
    class _Entry:
        pw_dir = where
    pwd.getpwuid = lambda uid: _Entry()
from ndf.adapters import write_gate

gate = write_gate.WriteGate(Path(where)) if mode == "path" else write_gate.machine_gate()
while not os.path.exists(go):
    time.sleep(0.005)
print(json.dumps({"at": gate.reserve(write_gate.WRITE).at, "path": str(gate.path)}))
"""


def run_children(tmp_path: Path, argvs: list[list[str]], envs: list[dict[str, str]]) -> list[dict]:
    """子プロセスを並べて起動し、シグナルファイルで同時に予約させる。"""
    go = tmp_path / "go"
    children = [
        subprocess.Popen(
            [sys.executable, "-c", CHILD, *argv, str(go)],
            env={**os.environ, "PYTHONPATH": str(NDF_SRC), **env},
            stdout=subprocess.PIPE,
            text=True,
        )
        for argv, env in zip(argvs, envs)
    ]
    time.sleep(0.5)
    go.touch()
    outs = [child.communicate(timeout=60)[0] for child in children]
    assert [child.returncode for child in children] == [0, 0]
    return [json.loads(out) for out in outs]


def test_two_processes_on_one_record_are_spaced(tmp_path, path):
    got = run_children(tmp_path, [["path", str(path)], ["path", str(path)]], [{}, {}])

    assert abs(got[0]["at"] - got[1]["at"]) >= W - EPS


# gate_path


def test_gate_path_ignores_the_state_environment(tmp_path):
    home = tmp_path / "home"
    envs = [
        {"NDF_STATE_HOME": str(tmp_path / f"ndf{i}"), "XDG_STATE_HOME": str(tmp_path / f"xdg{i}"), "HOME": str(tmp_path / f"h{i}")}
        for i in (1, 2)
    ]

    got = run_children(tmp_path, [["home", str(home)], ["home", str(home)]], envs)

    expected = str(home / ".local" / "state" / "ndf-github" / write_gate.GATE_FILE)
    assert [g["path"] for g in got] == [expected, expected]
    assert abs(got[0]["at"] - got[1]["at"]) >= W - EPS


def test_pr_lock_sits_beside_the_record_and_waits_the_publish_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(write_gate, "gate_path", lambda: tmp_path / "ndf-github" / write_gate.GATE_FILE)

    lock = write_gate.pr_lock("o/r", 12)

    assert Path(lock.lock_file) == tmp_path / "ndf-github" / "pr" / "o__r__12.lock"
    assert lock.timeout == limits.value(write_gate.PR_LOCK, "lock")
    assert lock is not write_gate.pr_lock("o/r", 12)


# block


def test_block_pushes_the_next_reservation_past_its_end(gate):
    gate.block(30)

    assert gate.reserve(WRITE).at >= NOW + 30


def test_a_short_block_does_not_shorten_a_longer_one(gate, path):
    gate.block(30)
    gate.block(5)

    assert record(path)["blocked_until"] == pytest.approx(NOW + 30)


def test_block_on_an_unreadable_record_keeps_going_within_the_process(tmp_path, clock, capsys):
    unreadable = tmp_path / "state"
    unreadable.mkdir()
    gate = write_gate.WriteGate(unreadable, clock, clock.sleep)

    gate.block(30)

    assert "書き込みの順番の記録を読めない" in capsys.readouterr().err
    assert gate.reserve(WRITE).at >= NOW + 30


# acquire


def test_acquire_lets_a_read_through_without_sleeping(gate, path, clock):
    with gate.acquire(httpx.Request("GET", "https://api.github.com/repos/o/r")):
        pass

    assert clock.slept == []
    assert not path.exists()


def test_acquire_sleeps_the_reserved_seconds_before_a_write(gate, path, clock):
    seed(path, next=NOW + 3)

    with gate.acquire(httpx.Request("PATCH", "https://api.github.com/repos/o/r/issues/1")):
        sent_at = clock.now

    assert clock.slept == [pytest.approx(3)]
    assert sent_at == pytest.approx(NOW + 3)


def test_acquire_does_not_sleep_when_the_wait_is_zero(gate, clock):
    with gate.acquire(httpx.Request("POST", "https://api.github.com/repos/o/r/issues")):
        pass

    assert clock.slept == []


def test_acquire_holds_the_kind_of_the_request_with_count_one(gate, monkeypatch):
    calls = []
    real = gate.hold

    def spy(kind, count=1):
        calls.append((kind, count))
        return real(kind, count)

    monkeypatch.setattr(gate, "hold", spy)
    with gate.acquire(graphql("mutation { addComment(input: {}) { x } }")):
        pass

    assert calls == [(CONTENT, 1)]


# acquire（送る時刻）


def test_two_writes_start_sending_at_least_the_spacing_apart(gate, clock):
    starts = []
    for _ in range(2):
        with gate.hold(WRITE):
            starts.append(clock.now)

    assert starts[1] - starts[0] >= W - EPS


def test_a_late_send_counts_from_when_the_previous_send_finished(gate, path, clock):
    # 先の書き込みの予約は過ぎているが、先の書き込みは今から 5 秒の応答の後に送り終えた（I6）
    seed(path, next=NOW - 10, sent_next=NOW + 5 + W)

    with gate.hold(WRITE):
        sent_at = clock.now

    assert sent_at == pytest.approx(NOW + 5 + W)


@pytest.mark.parametrize(("kind", "space"), [(WRITE, W), (CONTENT, C)])
def test_a_slow_response_pushes_the_next_send(path, clock, kind, space):
    first = write_gate.WriteGate(path, clock, clock.sleep)
    second = write_gate.WriteGate(path, clock, clock.sleep)
    with first.hold(kind):
        clock.now += 5  # 応答に 5 秒かかる
        finished = clock.now

    with second.hold(kind):
        sent_at = clock.now

    assert sent_at >= finished + space - EPS


def test_no_other_send_starts_during_a_slow_response(path, clock):
    first = write_gate.WriteGate(path, clock, clock.sleep)
    second = write_gate.WriteGate(path, clock, clock.sleep)
    inside = threading.Event()
    events: list[tuple[str, float]] = []

    def slow() -> None:
        with first.hold(WRITE):
            inside.set()
            time.sleep(0.5)
            events.append(("first-end", time.monotonic()))

    def fast() -> None:
        inside.wait()
        with second.hold(WRITE):
            events.append(("second-start", time.monotonic()))

    threads = [threading.Thread(target=slow), threading.Thread(target=fast)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert [name for name, _ in events] == ["first-end", "second-start"]


def test_a_block_during_the_sleep_replaces_the_reservation(gate, path, clock):
    other = write_gate.WriteGate(path, clock, clock.sleep)
    seed(path, next=NOW + 2)
    blocked = []

    def block_once() -> None:
        if not blocked:
            blocked.append(True)
            other.block(30)

    clock.on_sleep = block_once
    with gate.hold(WRITE):
        sent_at = clock.now

    assert clock.slept == [pytest.approx(2), pytest.approx(30)]
    assert sent_at >= NOW + 2 + 30 - EPS
    assert record(path)["next"] == pytest.approx(sent_at + W)


def test_replacing_gives_back_the_old_reservation_first(gate, path):
    old = gate.reserve(CONTENT)

    got = gate.reserve(CONTENT, replacing=old)

    assert got.at == NOW and got.before == (0.0, 0.0)
    assert record(path)["next"] == pytest.approx(NOW + W)
    assert record(path)["next_content"] == pytest.approx(NOW + C)


def test_replacing_keeps_the_old_reservation_when_another_is_behind_it(gate):
    old = gate.reserve(WRITE)
    gate.reserve(WRITE)

    assert gate.reserve(WRITE, replacing=old).at == pytest.approx(NOW + 2 * W)


def test_without_the_send_lock_it_sends_and_says_so(gate, path, clock, capsys, short_locks):
    path.parent.mkdir(parents=True)
    lock = held(path.with_name(write_gate.SEND_LOCK_FILE))
    try:
        with gate.acquire(httpx.Request("PATCH", "https://api.github.com/repos/o/r/issues/1")):
            sent = True
    finally:
        lock.release()

    assert sent
    assert "書き込みの送信のロックを取れない" in capsys.readouterr().err
    assert record(path)["sent_next"] == pytest.approx(NOW + W)


def test_the_send_lock_is_released_when_the_send_fails(gate, path, clock):
    with pytest.raises(RuntimeError), gate.hold(WRITE):
        raise RuntimeError("送れなかった")

    probe = slot_pool.try_lock(path.with_name(write_gate.SEND_LOCK_FILE))
    assert probe is not None
    probe.release()
    assert record(path)["sent_next"] == pytest.approx(NOW + W)
