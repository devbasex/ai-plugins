from __future__ import annotations

import os
import signal
import subprocess
import sys

import pytest

from ndf import process_group
from ndf.contracts import ContractError, limits
from ndf.testing import waits

# 先頭の bash は SIGTERM で終わるが、SIGTERM を無視する子孫が残る組
STUBBORN = "(trap '' TERM; sleep 600) & echo started; wait"
# SIGTERM で 7 を返して終わる組
ENDS_ON_SIGTERM = "trap 'exit 7' TERM; echo started; while :; do sleep 0.05; done"
GRACE = 0.5


@pytest.fixture
def short_grace(monkeypatch):
    real = limits.seconds
    monkeypatch.setattr(limits, "seconds", lambda name: GRACE if name.startswith("procs.") else real(name))


def _start(script: str) -> subprocess.Popen:
    proc = subprocess.Popen(["bash", "-c", script], stdout=subprocess.PIPE, start_new_session=True)
    assert proc.stdout.readline() == b"started\n"
    return proc


def _cleanup(proc: subprocess.Popen) -> None:
    if process_group.alive(proc, proc.pid):
        os.killpg(proc.pid, signal.SIGKILL)
    proc.wait()


def test_group_that_ends_on_sigterm_gets_no_sigkill(short_grace):
    proc = _start(ENDS_ON_SIGTERM)
    try:
        process_group.stop_group(proc)
        assert proc.wait() == 7  # SIGKILL なら -9
    finally:
        _cleanup(proc)


@pytest.mark.parametrize("send_first", [True, False])
def test_descendants_ignoring_sigterm_are_killed_after_the_grace(short_grace, send_first):
    proc = _start(STUBBORN)
    try:
        if not send_first:
            # シグナルを転送した後の経路: 先頭は回収されたが子孫が残る
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait()
            assert process_group.alive(proc, proc.pid)
        process_group.stop_group(proc, send_first=send_first)
        waits.wait_until(lambda: not process_group.alive(proc, proc.pid), what="組全体の終了")
    finally:
        _cleanup(proc)


def test_without_send_first_no_sigterm_is_sent(short_grace):
    proc = _start(ENDS_ON_SIGTERM)
    try:
        process_group.stop_group(proc, send_first=False)
        assert proc.wait() == -signal.SIGKILL  # SIGTERM を受けていれば 7
    finally:
        _cleanup(proc)


def test_own_group_sends_only_to_the_process(short_grace):
    # start_new_session なしの子は自分と同じ組にいる。組へ送ると自分も止まる
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    process_group.stop_group(proc)
    assert proc.wait() == -signal.SIGTERM


def test_finished_group_is_a_no_op():
    proc = subprocess.Popen(["true"], start_new_session=True)
    proc.wait()
    process_group.stop_group(proc)
    process_group.stop_group(proc, send_first=False)
    assert not process_group.alive(proc, proc.pid)


def test_unknown_grace_key_is_a_contract_error():
    proc = subprocess.Popen(["true"], start_new_session=True)
    proc.wait()
    with pytest.raises(ContractError):
        process_group.stop_group(proc, grace_key="procs.no-such-key")


def test_alive_counts_members_without_permission(monkeypatch):
    proc = subprocess.Popen(["true"])
    proc.wait()

    def refuse(pgid, sig):
        raise PermissionError(1, "送れない")

    monkeypatch.setattr(os, "killpg", refuse)
    assert process_group.alive(proc, 12345)


def test_a_detached_group_is_stopped_by_pid_alone(short_grace):
    """自分の子でない組（前の回の親が残した組）も、組の番号だけで SIGTERM から SIGKILL まで止める（B10）。"""
    proc = _start(STUBBORN)
    try:
        process_group.stop_detached_group(proc.pid)
        waits.wait_until(lambda: not process_group.alive(proc, proc.pid), what="組全体の終了")
    finally:
        _cleanup(proc)


def test_stopping_a_finished_detached_group_does_nothing():
    proc = subprocess.Popen(["true"], start_new_session=True)
    proc.wait()
    process_group.stop_detached_group(proc.pid)
    assert not process_group.group_alive(proc.pid)
