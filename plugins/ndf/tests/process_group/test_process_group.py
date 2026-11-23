from __future__ import annotations

import os
import subprocess

import pytest

from ndf import process_group
from ndf.contracts import ContractError


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


def test_stopping_a_finished_detached_group_does_nothing():
    proc = subprocess.Popen(["true"], start_new_session=True)
    proc.wait()
    process_group.stop_detached_group(proc.pid)
    assert not process_group.group_alive(proc.pid)
