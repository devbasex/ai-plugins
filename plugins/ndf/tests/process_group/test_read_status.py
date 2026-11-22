"""関所の起動の知らせを読む関数の振る舞いを固定する。

今は `ndf.adapters.claude._read_status` と `ndf.domains.relay.launch_target._read_status` の 2 つが同じ役割を持つ。
どちらも同じ契約（何も読まずに EOF なら b""・読んだら EOF までの全部・待ち切れたら None）を守ることを確かめる。
"""

from __future__ import annotations

import os
import threading
import time

import pytest

from ndf.adapters import claude
from ndf.domains.relay import launch_target

READERS = [
    pytest.param(claude._read_status, id="claude"),
    pytest.param(launch_target._read_status, id="launch_target"),
]


@pytest.fixture
def pipe():
    r, w = os.pipe()
    fds = {"r": r, "w": w}
    yield fds
    for fd in fds.values():
        try:
            os.close(fd)
        except OSError:
            pass


def _close_writer(fds: dict[str, int]) -> None:
    os.close(fds["w"])
    fds["w"] = -1


@pytest.mark.parametrize("read_status", READERS)
def test_eof_without_data_is_empty_bytes(read_status, pipe):
    _close_writer(pipe)

    assert read_status(pipe["r"], 5.0) == b""


@pytest.mark.parametrize("read_status", READERS)
def test_data_then_eof_returns_the_data(read_status, pipe):
    os.write(pipe["w"], b"2")
    _close_writer(pipe)

    assert read_status(pipe["r"], 5.0) == b"2"


@pytest.mark.parametrize("read_status", READERS)
def test_reads_past_one_chunk_until_eof(read_status, pipe):
    payload = b"x" * 200  # 1 回の読み（64 バイト）を超える
    os.write(pipe["w"], payload)
    _close_writer(pipe)

    assert read_status(pipe["r"], 5.0) == payload


@pytest.mark.parametrize("read_status", READERS)
def test_joins_writes_that_arrive_apart(read_status, pipe):
    def later():
        os.write(pipe["w"], b"1")
        time.sleep(0.1)
        os.write(pipe["w"], b"3")
        _close_writer(pipe)

    t = threading.Thread(target=later)
    t.start()
    try:
        assert read_status(pipe["r"], 5.0) == b"13"
    finally:
        t.join()


@pytest.mark.parametrize("read_status", READERS)
def test_no_eof_within_the_time_is_none(read_status, pipe):
    started = time.monotonic()

    assert read_status(pipe["r"], 0.2) is None
    assert time.monotonic() - started < 3.0


@pytest.mark.parametrize("read_status", READERS)
def test_partial_data_without_eof_is_still_none(read_status, pipe):
    os.write(pipe["w"], b"2")

    assert read_status(pipe["r"], 0.2) is None


@pytest.mark.parametrize("read_status", READERS)
def test_zero_seconds_with_eof_ready_is_empty_bytes(read_status, pipe):
    _close_writer(pipe)

    assert read_status(pipe["r"], 0.0) == b""


@pytest.mark.parametrize("read_status", READERS)
def test_zero_seconds_with_nothing_ready_is_none(read_status, pipe):
    assert read_status(pipe["r"], 0.0) is None


@pytest.mark.parametrize("read_status", READERS)
def test_closed_fd_raises_os_error(read_status, pipe):
    os.close(pipe["r"])
    fd = pipe.pop("r")

    with pytest.raises((OSError, ValueError)):
        read_status(fd, 0.2)
