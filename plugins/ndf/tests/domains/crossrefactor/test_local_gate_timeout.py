"""`local_gate.run_suites` の時間上限: 打つときに掛けた上限と、時間切れを失敗とみなすか上限停止とみなすかの判定。"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import pytest

from ndf.domains.crossrefactor import final_gate, local_gate
from ndf.testing import means

SUITES = (("unit", "sh check.sh"), ("lint", "sh other.sh"))


@dataclass
class TimedExec:
    """`timed_out` に入ったコマンドは掛けられた上限まで走って時間切れになる。打った (コマンド, 上限) を残す。"""

    timed_out: frozenset[str] = frozenset()
    step: float = 1.0
    now: float = 0.0
    seen: list[tuple[str, float]] = field(default_factory=list)

    def run(self, command: str, cwd: Path, timeout: float, *, suites: Any = None) -> subprocess.CompletedProcess[str]:
        self.seen.append((command, timeout))
        if command in self.timed_out:
            self.now += timeout
            raise subprocess.TimeoutExpired(command, timeout, output="partial")
        self.now += self.step
        return subprocess.CompletedProcess(command, 0, "", "")

    def ex(self) -> local_gate.Exec:
        return local_gate.Exec(self.run, lambda: self.now, lambda began: 0.0)


class _FixedHead:
    """`HEAD` を固定の値で返す。時間上限の判定に Git の起動は要らない。"""

    def __init__(self, tree: Path) -> None:
        self.tree = tree

    def rev_parse(self, rev: str) -> str:
        return "c" * 40


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # HEAD の取得と追跡ファイルの確認を差し替え、Git を起動しない（テストの前後で HEAD は動かず、変更も無い）
    monkeypatch.setattr(local_gate, "git", SimpleNamespace(Git=_FixedHead))
    monkeypatch.setattr(local_gate, "worktree", SimpleNamespace(dirty_tracked=lambda g: []))
    return tmp_path


def _found() -> means.Means:
    return means.Means(means.Kind.LOCAL_TEST, suites=SUITES, declarations=({"name": "unit"}, {"name": "lint"}))


def _run(tree: Path, fake: TimedExec, total: float, suite: Optional[float], used: float = 0.0) -> tuple[Any, local_gate.TestBudget]:
    budget = local_gate.TestBudget(total, suite, used)
    logs: list[tuple[int, str]] = []
    got = local_gate.run_suites(tree, _found(), budget, lambda i, text: logs.append((i, text)), fake.ex())
    return got, budget


def test_without_a_suite_limit_the_remaining_total_is_applied_and_a_timeout_stops_at_the_limit(tree):
    fake = TimedExec(frozenset({"sh check.sh"}))
    with pytest.raises(final_gate.GateStopped) as got:
        _run(tree, fake, total=100.0, suite=None, used=40.0)
    assert got.value.kind == local_gate.LIMIT_KIND
    assert fake.seen == [("sh check.sh", 60.0)]


def test_a_suite_limit_below_the_remaining_is_applied_and_its_timeout_is_a_failure(tree):
    fake = TimedExec(frozenset({"sh check.sh"}))
    failed, budget = _run(tree, fake, total=100.0, suite=10.0)
    assert fake.seen == [("sh check.sh", 10.0), ("sh other.sh", 10.0)]
    assert [(f.index, f.code, f.command, f.output) for f in failed] == [(1, None, "sh check.sh", "partial")]
    assert budget.used == 11.0


def test_a_suite_limit_equal_to_the_remaining_stops_at_the_limit(tree):
    fake = TimedExec(frozenset({"sh check.sh"}))
    with pytest.raises(final_gate.GateStopped) as got:
        _run(tree, fake, total=100.0, suite=60.0, used=40.0)
    assert got.value.kind == local_gate.LIMIT_KIND
    assert fake.seen == [("sh check.sh", 60.0)]


def test_a_suite_limit_above_the_remaining_applies_the_remaining_and_stops_at_the_limit(tree):
    fake = TimedExec(frozenset({"sh check.sh"}))
    with pytest.raises(final_gate.GateStopped) as got:
        _run(tree, fake, total=100.0, suite=300.0, used=40.0)
    assert got.value.kind == local_gate.LIMIT_KIND
    assert fake.seen == [("sh check.sh", 60.0)]


def test_the_remaining_shrinks_so_a_later_suite_gets_the_smaller_limit_and_stops(tree):
    # 1 本目は 25 秒で通り、残り 25 秒 < suite の上限 30 秒なので、2 本目には 25 秒が掛かり時間切れは上限停止
    fake = TimedExec(frozenset({"sh other.sh"}), step=25.0)
    with pytest.raises(final_gate.GateStopped) as got:
        _run(tree, fake, total=50.0, suite=30.0)
    assert got.value.kind == local_gate.LIMIT_KIND
    assert fake.seen == [("sh check.sh", 30.0), ("sh other.sh", 25.0)]


def test_no_remaining_stops_before_running(tree):
    fake = TimedExec()
    with pytest.raises(final_gate.GateStopped) as got:
        _run(tree, fake, total=100.0, suite=10.0, used=100.0)
    assert got.value.kind == local_gate.LIMIT_KIND
    assert fake.seen == []
