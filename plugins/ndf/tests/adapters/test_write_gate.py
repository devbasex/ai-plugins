"""書き込みの順番をマシン全体で取る（#4016 の u1。WriteGate の本体）。

時計と眠りは差し替え、記録は `tmp_path` に置く。本物のホームの下（`~/.local/state/ndf-github/`）を書かない。
"""

from __future__ import annotations

import asyncio
import functools
import json
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import httpx
import pytest
from filelock import Timeout

from ndf import slot_pool
from ndf.adapters import write_gate
from ndf.adapters.write_gate import CONTENT, WRITE, Writes
from ndf.contracts import limits

NOW = 1_000_000.0
EPS = 1e-6


def spacing(name: str) -> float:
    return limits.value(name, "interval")


W = spacing(write_gate.WRITE_SPACING)
# 内容を作る書き込みを数えるウィンドウの (長さの秒, 上限の件数)
MINUTE = (limits.value(write_gate.CONTENT_MINUTE, "seconds"), limits.value(write_gate.CONTENT_MINUTE, "count"))
HOUR = (limits.value(write_gate.CONTENT_HOUR, "seconds"), limits.value(write_gate.CONTENT_HOUR, "count"))


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
    return tmp_path / "gate" / write_gate.GATE_FILE  # tmp_path/state は共通の fixture が XDG_STATE_HOME に使う


@pytest.fixture
def gate(path, clock) -> write_gate.WriteGate:
    return write_gate.WriteGate(path, clock, clock.sleep)


class _TimedOut:
    """取ろうとするとすぐ Timeout を返すロック。"""

    def __init__(self, path: Path) -> None:
        self.lock_file = str(path)

    def acquire(self, timeout: Optional[float] = None) -> None:
        raise Timeout(self.lock_file)


@pytest.fixture
def failing_lock(monkeypatch):
    """`fail(name)` の後は、ファイル名が `name` のロックの取得がすぐ Timeout になる。"""
    failing: set[str] = set()
    real = slot_pool.lock
    monkeypatch.setattr(slot_pool, "lock", lambda path: _TimedOut(path) if Path(path).name in failing else real(path))
    return failing.add


RECORD_LOCK = write_gate.GATE_FILE + ".lock"


def record(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def seed(path: Path, **times: object) -> None:
    """記録を書いた形で置く（書いた時刻は今。内容を作る書き込みの時刻の一覧は `content`）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    base = dict.fromkeys(write_gate.TIMES, 0.0)
    record = {"version": write_gate.RECORD_VERSION, **base, "written_at": NOW, "content": [], **times}
    path.write_text(json.dumps(record), encoding="utf-8")


def graphql(query: str) -> httpx.Request:
    return httpx.Request("POST", "https://api.github.com/graphql", json={"query": query})


# write_of


@pytest.mark.parametrize(
    ("request_", "expected"),
    [
        (httpx.Request("GET", "https://api.github.com/repos/o/r/issues"), None),
        (httpx.Request("HEAD", "https://api.github.com/repos/o/r"), None),
        (httpx.Request("POST", "https://api.github.com/repos/o/r/issues", json={"title": "t"}), Writes(CONTENT, 1)),
        # cross-review の返信（受け入れ条件 3）とまとめたレビューも、内容を作る書き込みとして間隔が当たる
        (httpx.Request("POST", "https://api.github.com/repos/o/r/pulls/7/comments/11/replies", json={"body": "b"}), Writes(CONTENT, 1)),
        (httpx.Request("POST", "https://api.github.com/repos/o/r/pulls/7/reviews", json={"comments": []}), Writes(CONTENT, 1)),
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


def test_content_after_content_waits_only_the_write_spacing_under_the_limits(gate, path):
    gate.reserve(CONTENT)

    assert gate.reserve(CONTENT).wait == pytest.approx(W)
    assert record(path)["content"] == [NOW, pytest.approx(NOW + W)]
    assert gate.reserve(WRITE).wait == pytest.approx(2 * W)


def test_a_reservation_at_exactly_now_does_not_wait(gate, path, clock):
    seed(path, next=NOW)

    got = gate.reserve(CONTENT)

    assert got.wait == 0 and got.at == NOW
    assert record(path)["content"] == [NOW]


def test_a_long_block_is_kept_and_waited(gate):
    gate.block(3600)

    assert gate.reserve(WRITE).wait == pytest.approx(3600)


def test_a_reservation_behind_six_contents_is_kept(gate):
    for _ in range(6):
        gate.reserve(CONTENT)

    assert gate.reserve(CONTENT).wait == pytest.approx(6 * W)


def test_hold_spaces_the_following_reservations_by_its_count(gate, path):
    with gate.hold(CONTENT, 3):
        pass

    assert record(path)["content"] == [NOW] * 3
    assert gate.reserve(CONTENT).wait == pytest.approx(3 * W)
    assert gate.reserve(WRITE).wait == pytest.approx(4 * W)


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


def test_without_the_record_lock_it_spaces_within_the_process(gate, path, capsys, failing_lock):
    failing_lock(RECORD_LOCK)
    first = gate.reserve(WRITE)
    second = gate.reserve(WRITE)

    assert first.wait == 0
    assert second.wait == pytest.approx(W)
    assert "書き込みの順番の記録のロックを取れない" in capsys.readouterr().err
    assert not path.exists()


def test_a_reservation_that_waited_on_the_record_lock_reads_the_ones_taken_meanwhile(path, clock, monkeypatch):
    # 記録のロックを待つ間に 7 つの予約が先に入った順を、ロックの取得の前に割り込ませて作る（スレッドも本物の待ちも使わない）
    others = [write_gate.WriteGate(path, clock, clock.sleep) for _ in range(7)]
    last = write_gate.WriteGate(path, clock, clock.sleep)
    got: list[float] = []
    real = slot_pool.lock
    waiting = [True]

    def lock(lock_path):
        if waiting and Path(lock_path).name == RECORD_LOCK:
            waiting.clear()
            got.extend(o.reserve(WRITE).at for o in others)
        return real(lock_path)

    monkeypatch.setattr(slot_pool, "lock", lock)
    got.append(last.reserve(WRITE).at)

    assert got == sorted(got) and len(set(got)) == 8
    assert all(b - a >= W - EPS for a, b in zip(got, got[1:]))


def test_two_gates_on_one_record_are_spaced(path, clock):
    # 別のプロセスの WriteGate と同じく、プロセスの中の記録を共有しない 2 つが同じ記録を引き継ぐ。
    # 予約の間の排他は test_the_record_lock_is_held_while_reserving が確かめる
    first, second = (write_gate.WriteGate(path, clock, clock.sleep) for _ in range(2))

    got = [first.reserve(WRITE).at, second.reserve(WRITE).at]

    assert got[1] - got[0] >= W - EPS


def test_the_record_lock_is_held_while_reserving(gate, path):
    path.parent.mkdir(parents=True)

    def change(record: dict, now: float) -> None:
        assert slot_pool.try_lock(path.with_name(RECORD_LOCK)) is None

    gate._apply(change)


# gate_path


class _Entry:
    def __init__(self, pw_dir: str) -> None:
        self.pw_dir = pw_dir


def test_gate_path_ignores_the_state_environment(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(write_gate.pwd, "getpwuid", lambda uid: _Entry(str(home)))
    for name in ("NDF_STATE_HOME", "XDG_STATE_HOME", "HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))

    assert write_gate.gate_path() == home / ".local" / "state" / "ndf-github" / write_gate.GATE_FILE
    assert write_gate.machine_gate().path == write_gate.gate_path()


def _no_entry(uid: int):
    raise KeyError(uid)


def test_a_uid_outside_passwd_shares_one_place_whatever_the_environment(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(write_gate.pwd, "getpwuid", _no_entry)
    monkeypatch.setattr(write_gate, "SHARED_TMP", tmp_path)
    got = []
    for home in (str(tmp_path / "a"), str(tmp_path / "b"), None):
        if home is None:
            monkeypatch.delenv("HOME", raising=False)
        else:
            monkeypatch.setenv("HOME", home)
        monkeypatch.setenv("TMPDIR", str(tmp_path / "t"))
        got.append(write_gate.gate_path())

    place = tmp_path / f"ndf-github-{write_gate.os.getuid()}"
    assert got == [place / ".local" / "state" / "ndf-github" / write_gate.GATE_FILE] * 3
    assert place.stat().st_mode & 0o777 == 0o700
    assert write_gate.pr_lock("o/r", 1).lock_file == str(place / ".local" / "state" / "ndf-github" / "pr" / "o__r__1.lock")
    assert "passwd に無い" in capsys.readouterr().err


def test_a_uid_place_open_to_others_falls_back_to_a_private_dir(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(write_gate.pwd, "getpwuid", _no_entry)
    monkeypatch.setattr(write_gate, "SHARED_TMP", tmp_path / "shared")
    monkeypatch.setattr(write_gate.tempfile, "tempdir", str(tmp_path / "private"))
    (tmp_path / "private").mkdir()
    (tmp_path / "shared" / f"ndf-github-{write_gate.os.getuid()}").mkdir(parents=True, mode=0o777)
    (tmp_path / "shared" / f"ndf-github-{write_gate.os.getuid()}").chmod(0o777)
    write_gate._private_home.cache_clear()

    try:
        got = write_gate.gate_path()
        assert got.is_relative_to(tmp_path / "private") and got == write_gate.gate_path()
        assert got.parents[3].stat().st_mode & 0o777 == 0o700
        assert "オーナーか権限が違う" in capsys.readouterr().err
    finally:
        write_gate._private_home.cache_clear()


def test_pr_lock_sits_beside_the_record_and_waits_the_publish_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(write_gate, "gate_path", lambda: tmp_path / "ndf-github" / write_gate.GATE_FILE)

    lock = write_gate.pr_lock("o/r", 12)

    assert Path(lock.lock_file) == tmp_path / "ndf-github" / "pr" / "o__r__12.lock"
    assert lock.timeout == limits.value(write_gate.PR_LOCK, "lock")
    assert lock is not write_gate.pr_lock("o/r", 12)


def test_pr_lock_is_the_same_for_any_case_of_the_repository(tmp_path, monkeypatch):
    monkeypatch.setattr(write_gate, "gate_path", lambda: tmp_path / "ndf-github" / write_gate.GATE_FILE)

    assert write_gate.pr_lock("Owner/Repo", 1).lock_file == write_gate.pr_lock("owner/repo", 1).lock_file


# block


def test_block_pushes_the_next_reservation_past_its_end(gate):
    gate.block(30)

    assert gate.reserve(WRITE).at >= NOW + 30


def test_a_short_block_does_not_shorten_a_longer_one(gate, path):
    gate.block(30)
    gate.block(5)

    assert record(path)["blocked_until"] == pytest.approx(NOW + 30)


def test_block_on_an_unreadable_record_keeps_going_within_the_process(tmp_path, clock, capsys):
    unreadable = tmp_path / "unreadable"
    unreadable.mkdir()
    gate = write_gate.WriteGate(unreadable, clock, clock.sleep)

    gate.block(30)

    assert "書き込みの順番の記録を読めない" in capsys.readouterr().err
    assert gate.reserve(WRITE).at >= NOW + 30


@pytest.fixture
def unwritable(monkeypatch):
    """記録を読めるが置き換えられない（ディレクトリの権限の変更など）。"""

    def fail(path, data):
        raise OSError("書けない")

    monkeypatch.setattr(slot_pool, "write_json", fail)


def test_an_unwritable_record_keeps_the_block_and_the_spacing(gate, path, unwritable, capsys):
    seed(path)

    gate.block(30)
    first = gate.reserve(WRITE)
    second = gate.reserve(WRITE)

    assert first.at >= NOW + 30
    assert second.at - first.at >= W - EPS
    assert "書き込みの順番の記録を書けない" in capsys.readouterr().err


def test_an_unwritable_record_keeps_the_send_spacing(gate, path, clock, unwritable):
    seed(path)
    with gate.hold(WRITE):
        clock.now += 5  # 応答に 5 秒かかる
        finished = clock.now

    with gate.hold(WRITE):
        sent_at = clock.now

    assert sent_at >= finished + W - EPS


def test_a_fallback_change_while_saving_is_kept_and_written_later(gate, path):
    # 記録のロックを持つ保存の途中で、ロックを取れなかった別のスレッドがプロセスの中の記録だけを変える
    seed(path)

    def change(record: dict, now: float) -> None:
        gate._in_process(lambda memory, now: memory.__setitem__("blocked_until", now + 30))

    gate._apply(change)

    assert gate._unsaved
    assert gate.reserve(WRITE).at >= NOW + 30
    assert record(path)["blocked_until"] == pytest.approx(NOW + 30)


def test_a_fallback_change_later_in_the_save_is_not_read_as_a_rewound_clock(gate, path, clock):
    # 保存を始めてから CLOCK_SLACK を越えて後に、プロセスの中だけの変更が入る（壁時計は正しく進む）
    seed(path)

    def change(record: dict, now: float) -> None:
        clock.now += 5
        gate._in_process(lambda memory, now: memory.__setitem__("blocked_until", now + 30))

    gate._apply(change)

    assert gate._memory["blocked_until"] == pytest.approx(NOW + 5 + 30)
    assert gate._memory["written_at"] == pytest.approx(NOW + 5)
    assert gate.reserve(WRITE).at == pytest.approx(NOW + 5 + 30)


def test_a_fallback_change_while_loading_is_not_read_as_a_rewound_clock(gate, path, clock, monkeypatch):
    # 記録を読む途中で CLOCK_SLACK を越えて時計が進み、プロセスの中だけの変更が入る（壁時計は正しく進む）
    seed(path)
    real = gate._load

    def load() -> Optional[dict]:
        clock.now += 5
        gate._in_process(lambda memory, now: memory.__setitem__("blocked_until", now + 30))
        return real()

    monkeypatch.setattr(gate, "_load", load)
    gate.block(1)

    assert record(path)["blocked_until"] == pytest.approx(NOW + 5 + 30)


def test_the_record_written_again_carries_the_unsaved_changes(gate, path, monkeypatch):
    seed(path)
    real = slot_pool.write_json
    monkeypatch.setattr(slot_pool, "write_json", lambda p, d: (_ for _ in ()).throw(OSError("書けない")))
    gate.block(30)
    monkeypatch.setattr(slot_pool, "write_json", real)

    gate.reserve(WRITE)

    assert record(path)["blocked_until"] == pytest.approx(NOW + 30)


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

    def spy(kind, count=1, remaining=None):
        calls.append((kind, count))
        return real(kind, count, remaining)

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


@pytest.mark.parametrize("kind", [WRITE, CONTENT])
def test_a_slow_response_pushes_the_next_send(path, clock, kind):
    first = write_gate.WriteGate(path, clock, clock.sleep)
    second = write_gate.WriteGate(path, clock, clock.sleep)
    with first.hold(kind):
        clock.now += 5  # 応答に 5 秒かかる
        finished = clock.now

    with second.hold(kind):
        sent_at = clock.now

    assert sent_at >= finished + W - EPS


def test_without_the_record_lock_the_send_still_waits_the_spacing(gate, path, clock, failing_lock):
    # 先の書き込みの予約は過ぎているが、先の書き込みは今から 5 秒の応答の後に送り終えた（I6）
    seed(path, next=NOW - 10, sent_next=NOW + 5 + W)
    gate.reserve(WRITE)  # プロセスの中の記録へ写す
    failing_lock(RECORD_LOCK)

    with gate.hold(WRITE):
        sent_at = clock.now

    assert sent_at == pytest.approx(NOW + 5 + W)


def test_no_other_send_starts_during_a_slow_response(path, clock):
    # 送信のロックは応答の終わりまで持つ。持つ間は後の書き込みが取れない（取れるかを待たずに確かめる）
    first = write_gate.WriteGate(path, clock, clock.sleep)
    second = write_gate.WriteGate(path, clock, clock.sleep)
    with first.hold(WRITE):
        clock.now += 5  # 応答に 5 秒かかる
        assert not _send_lock_is_free(path)
        finished = clock.now
    assert _send_lock_is_free(path)

    with second.hold(WRITE):
        sent_at = clock.now

    assert sent_at >= finished + W - EPS


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

    assert got.at == NOW and got.before == 0.0
    assert record(path)["next"] == pytest.approx(NOW + W)
    assert record(path)["content"] == [NOW]


def test_replacing_keeps_the_old_reservation_when_another_is_behind_it(gate):
    old = gate.reserve(WRITE)
    gate.reserve(WRITE)

    assert gate.reserve(WRITE, replacing=old).at == pytest.approx(NOW + 2 * W)


def test_without_the_send_lock_it_sends_and_says_so(gate, path, clock, capsys, failing_lock):
    failing_lock(write_gate.SEND_LOCK_FILE)
    with gate.acquire(httpx.Request("PATCH", "https://api.github.com/repos/o/r/issues/1")):
        sent = True

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


# async_acquire


def test_async_acquire_takes_the_locks_off_the_event_loop(gate, path, monkeypatch):
    threads: list[int] = []
    real = slot_pool.lock

    def spy(lock_path):
        threads.append(threading.get_ident())
        return real(lock_path)

    monkeypatch.setattr(slot_pool, "lock", spy)

    async def write() -> int:
        async with gate.async_acquire(httpx.Request("PATCH", "https://api.github.com/repos/o/r/issues/1")):
            pass
        return threading.get_ident()

    loop_thread = asyncio.run(write())

    assert threads and loop_thread not in threads
    assert record(path)["sent_next"] == pytest.approx(NOW + W)
    probe = slot_pool.try_lock(path.with_name(write_gate.SEND_LOCK_FILE))
    assert probe is not None
    probe.release()


class _Executor(ThreadPoolExecutor):
    """スレッドを使わない実行器。`frozen` の間は受けた処理を進めず `pending` に積む（埋まったプールの代わり）。"""

    def __init__(self) -> None:
        super().__init__(max_workers=1)
        self.frozen = False
        self.pending: list[tuple[Future, Callable]] = []
        self.ran: list[Callable] = []

    def submit(self, fn, /, *args, **kwargs) -> Future:
        future: Future = Future()
        self.pending.append((future, functools.partial(fn, *args, **kwargs)))
        if not self.frozen:
            self.drain()
        return future

    def drain(self) -> None:
        while self.pending:
            future, fn = self.pending.pop(0)
            if not future.set_running_or_notify_cancel():
                continue
            self.ran.append(fn)
            try:
                future.set_result(fn())
            except BaseException as exc:
                future.set_exception(exc)


async def _settled(task: asyncio.Future, turns: int = 50) -> bool:
    """本物の時間を待たずにイベントループを回し、`task` が終わったかを返す。"""
    for _ in range(turns):
        if task.done():
            return True
        await asyncio.sleep(0)
    return task.done()


def _send_lock_is_free(path: Path) -> bool:
    probe = slot_pool.try_lock(path.with_name(write_gate.SEND_LOCK_FILE))
    if probe is None:
        return False
    probe.release()
    return True


PATCH = httpx.Request("PATCH", "https://api.github.com/repos/o/r/issues/1")


def test_async_release_runs_while_the_default_pool_is_stuck(gate, path, monkeypatch):
    # 送信のロックを待つ手順が既定のスレッドプールを埋めた状態を、処理を進めない既定の実行器で作る
    default, finisher = _Executor(), _Executor()
    monkeypatch.setattr(write_gate, "_FINISHER", finisher)

    async def write() -> None:
        async with gate.async_acquire(PATCH):
            default.frozen = True

    async def main() -> bool:
        asyncio.get_running_loop().set_default_executor(default)
        return await _settled(asyncio.ensure_future(write()))

    assert asyncio.run(main())
    assert default.pending == [] and len(finisher.ran) == 1
    assert _send_lock_is_free(path)
    assert record(path)["sent_next"] == pytest.approx(NOW + W)


def test_a_cancelled_caller_still_releases_the_send_lock(gate, path, monkeypatch):
    # 専用の実行器が埋まっている間に呼び出し元が取り消されても、投入済みの放す処理は後で進む
    default, finisher = _Executor(), _Executor()
    finisher.frozen = True
    monkeypatch.setattr(write_gate, "_FINISHER", finisher)

    async def write() -> None:
        async with gate.async_acquire(PATCH):
            pass

    async def main() -> None:
        asyncio.get_running_loop().set_default_executor(default)
        task = asyncio.ensure_future(write())
        for _ in range(50):
            if finisher.pending:
                break
            await asyncio.sleep(0)
        assert len(finisher.pending) == 1 and not _send_lock_is_free(path)
        task.cancel()
        assert await _settled(task) and task.cancelled()
        finisher.drain()

    asyncio.run(main())

    assert len(finisher.ran) == 1
    assert _send_lock_is_free(path)
    assert record(path)["sent_next"] == pytest.approx(NOW + W)


COMMENT = graphql("mutation { addComment(input: {}) { x } }")


def test_a_caller_cancelled_while_waiting_withdraws_the_reservation(gate, path, monkeypatch):
    # PR 4273 の r1-3: 予約の時刻を待つ間に取り消されたら、送らなかった予約を一覧と共通の `next` から除く
    seed(path, next=NOW + 3)
    default, finisher = _Executor(), _Executor()
    monkeypatch.setattr(write_gate, "_FINISHER", finisher)
    real_sleep = asyncio.sleep
    sleeping = asyncio.Event()

    async def sleep(seconds: float) -> None:
        sleeping.set()
        # PR 4273 の r3-2: 眠る間に既定のスレッドプールが別の書き込みの送信のロックの待ちで埋まる
        default.frozen = True
        await asyncio.Event().wait()  # 取り消されるまで返らない

    monkeypatch.setattr(asyncio, "sleep", sleep)

    async def write() -> None:
        async with gate.async_acquire(COMMENT):
            pass

    async def main() -> bool:
        asyncio.get_running_loop().set_default_executor(default)
        task = asyncio.ensure_future(write())
        for _ in range(50):
            if sleeping.is_set():
                break
            await real_sleep(0)
        assert sleeping.is_set() and record(path)["content"] == [NOW + 3]
        task.cancel()
        for _ in range(50):
            if task.done():
                break
            await real_sleep(0)
        return task.cancelled()

    assert asyncio.run(main())
    assert (record(path)["next"], record(path)["content"]) == (pytest.approx(NOW + 3), [])
    assert _send_lock_is_free(path)
    # PR 4273 の r2-2・r3-2: 取り消しの後始末（手順の close）はイベントループの上でも既定のスレッドプールでもなく専用の実行器で進む
    assert [fn.func.__name__ for fn in finisher.ran] == ["close"]
    assert default.pending == []


def test_a_caller_cancelled_just_as_the_send_is_allowed_withdraws_the_reservation(gate, path, monkeypatch):
    # PR 4273 の r1-3: 送ってよくなった手順の結果を受け取る前に取り消されたら、送信のロックを放し、予約も取り消す
    default, finisher = _Executor(), _Executor()
    default.frozen = True
    monkeypatch.setattr(write_gate, "_FINISHER", finisher)

    async def write() -> None:
        async with gate.async_acquire(COMMENT):
            pass

    async def main() -> bool:
        asyncio.get_running_loop().set_default_executor(default)
        task = asyncio.ensure_future(write())
        for _ in range(50):
            if default.pending:
                break
            await asyncio.sleep(0)
        task.cancel()
        cancelled = await _settled(task) and task.cancelled()
        default.drain()  # 取り消しの後に、送ってよくなった手順が終わる
        for _ in range(50):
            if _send_lock_is_free(path):
                break
            await asyncio.sleep(0)
        return cancelled

    assert asyncio.run(main())
    assert _send_lock_is_free(path)
    assert (record(path)["next"], record(path)["sent_next"], record(path)["content"]) == (0.0, 0.0, [])
    # PR 4273 の r2-2: 送信のロックを持つ後始末はイベントループの上でなく専用の実行器で進む
    assert [fn.func.__name__ for fn in finisher.ran] == ["_give_back"]


def test_async_steps_holding_the_send_lock_resume_on_the_finisher(gate, path, clock, monkeypatch):
    # PR 4265 の r1-5: 最短の送信時刻を送信のロックを持ったまま待った後の読み直しは、既定のスレッドプールが
    # 送信のロックを待つ手順で埋まっていても進む
    seed(path, next=NOW - 10, sent_next=NOW + 5)
    default, finisher = _Executor(), _Executor()
    monkeypatch.setattr(write_gate, "_FINISHER", finisher)
    real_sleep = asyncio.sleep

    async def sleep(seconds: float) -> None:
        clock.now += seconds
        default.frozen = True  # 眠る間に既定のスレッドプールが埋まる
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", sleep)

    async def write() -> float:
        async with gate.async_acquire(PATCH):
            return clock.now

    async def main() -> Optional[float]:
        asyncio.get_running_loop().set_default_executor(default)
        task = asyncio.ensure_future(write())
        for _ in range(50):
            if task.done():
                return task.result()
            await real_sleep(0)
        return None

    sent_at = asyncio.run(main())

    assert sent_at == pytest.approx(NOW + 5)
    assert default.pending == [] and len(finisher.ran) == 2
    assert _send_lock_is_free(path)


# async_acquire（passing と同じ振る舞い）


def _patch_asyncio_sleep(monkeypatch, clock: Clock) -> list[float]:
    """非同期の眠りを偽の時計で進め、眠った秒を残す。"""
    slept: list[float] = []
    real_sleep = asyncio.sleep

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        clock.now += seconds
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", sleep)
    return slept


def test_async_acquire_lets_a_read_through_without_the_record_or_the_threads(gate, path, monkeypatch):
    default, finisher = _Executor(), _Executor()
    monkeypatch.setattr(write_gate, "_FINISHER", finisher)

    async def read() -> bool:
        asyncio.get_running_loop().set_default_executor(default)
        async with gate.async_acquire(httpx.Request("GET", "https://api.github.com/repos/o/r")):
            return True

    assert asyncio.run(read())
    assert default.ran == [] and finisher.ran == []
    assert not path.exists()


def test_async_acquire_sleeps_the_reserved_seconds_before_a_write(gate, path, clock, monkeypatch):
    seed(path, next=NOW + 3)
    slept = _patch_asyncio_sleep(monkeypatch, clock)

    async def write() -> float:
        async with gate.async_acquire(PATCH):
            return clock.now

    assert asyncio.run(write()) == pytest.approx(NOW + 3)
    assert slept == [pytest.approx(3)] and clock.slept == []


def test_async_acquire_holds_the_kind_of_the_request(gate, path):
    async def write() -> None:
        async with gate.async_acquire(graphql("mutation { addComment(input: {}) { x } }")):
            pass

    asyncio.run(write())

    assert record(path)["sent_next"] == pytest.approx(NOW + W)
    assert record(path)["content"] == [NOW]


def test_async_acquire_records_and_releases_when_the_send_fails(gate, path):
    async def write() -> None:
        async with gate.async_acquire(PATCH):
            raise RuntimeError("送れなかった")

    with pytest.raises(RuntimeError, match="送れなかった"):
        asyncio.run(write())

    assert _send_lock_is_free(path)
    assert record(path)["sent_next"] == pytest.approx(NOW + W)


def test_async_acquire_releases_once_on_the_finisher(gate, path, monkeypatch):
    finisher = _Executor()
    monkeypatch.setattr(write_gate, "_FINISHER", finisher)
    finished = []
    real = gate._finish

    def spy(reservation, send):
        finished.append((reservation.kind, send is not None))
        real(reservation, send)

    monkeypatch.setattr(gate, "_finish", spy)

    async def write() -> None:
        async with gate.async_acquire(PATCH):
            assert not _send_lock_is_free(path)

    asyncio.run(write())

    assert finished == [(WRITE, True)]
    assert [fn.func for fn in finisher.ran] == [spy]
    assert _send_lock_is_free(path)


def test_async_busy_neither_sends_nor_records_the_send(gate, path):
    seed(path, blocked_until=NOW + 60)
    entered = []

    async def write() -> None:
        async with gate.within(lambda: 3.0).async_acquire(PATCH):
            entered.append(True)

    with pytest.raises(write_gate.Busy):
        asyncio.run(write())

    assert entered == []
    assert record(path)["sent_next"] == 0.0
    assert _send_lock_is_free(path)
