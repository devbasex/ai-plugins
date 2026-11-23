"""`claude -p` の起動の前置きへ渡す、スロットを待つ上限・プロセスの組・CLI の下限の確かめ（#4000 の決定 8・決定 3）。

実時間の経過と実プロセスの起動は使わない（偽の時計と偽の `Popen` で縛る）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf import slot_pool
from ndf.adapters import claude, launch_slot
from ndf.testing import limits as test_limits
from ndf.testing import slots

PLENTY_MIB = 10**9
# 偽の claude が返す 1 回の回答（`--output-format json` の形）
REPLY = json.dumps({"type": "result", "is_error": False, "result": "{}", "total_cost_usd": 0.0})


class FakeProc:
    """管を持たない偽のプロセス（実プロセスを起動しない）。"""

    def __init__(self, stdout: str = REPLY) -> None:
        self.returncode = 0
        self.pid = 0
        self.stdin = self.stdout = self.stderr = None
        self._out = stdout

    def communicate(self, prompt: str = "", timeout: float = 0) -> tuple[str, str]:
        return self._out, ""


class Launches:
    """偽の起動の記録。`popen` は `Popen` が受けた引数、`floor` は CLI の下限を確かめた実行ファイル。"""

    def __init__(self) -> None:
        self.popen: list[dict] = []
        self.floor: list[str] = []


@pytest.fixture
def launches(monkeypatch) -> Launches:
    """`subprocess.Popen` と CLI の下限の確かめを偽物にし、受けた引数を残す。"""
    seen = Launches()

    def popen(argv, **kwargs):
        seen.popen.append({"argv": list(argv), **kwargs})
        return FakeProc()

    def require(name, argv0, env=None, cwd=None):
        seen.floor.append(argv0)

    monkeypatch.setattr(claude.subprocess, "Popen", popen)
    monkeypatch.setattr(claude.cli_floor, "require", require)
    return seen


@pytest.fixture
def full_pool(tmp_path, monkeypatch) -> Path:
    """空きの無い重い起動のスロットの保存先（テスト 1 本分の容量を 1 本が持つ）。"""
    monkeypatch.delenv(slots.HELD_ENV, raising=False)
    directory = tmp_path / "slots"
    monkeypatch.setenv(slots.SLOTS_DIR_ENV, str(directory))
    (tmp_path / ".ndf").mkdir()
    (tmp_path / ".ndf" / test_limits.RESOURCES_FILE).write_text(
        json.dumps({test_limits.SECTION: {"max_runs": 1, "memory_floor_mib": 1024}}), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(launch_slot, "wait_policy", lambda: (0.3, 0.05))
    monkeypatch.setattr(launch_slot.machine, "available_mib", lambda: PLENTY_MIB)
    directory.mkdir(parents=True, exist_ok=True)
    taken = slot_pool.take(directory, PLENTY_MIB, test_limits.test_weight_mib(), {"pid": 0})
    assert taken is not None
    yield directory
    taken.release()


def test_issue_4000_popen_starts_a_new_group_by_default(launches, tmp_path):
    """#4000: 既定は新しいプロセスの組、`new_group=False` なら呼び手の組に入る。"""
    claude._popen(["x"], tmp_path, {})
    claude._popen(["x"], tmp_path, {}, new_group=False)

    assert [call["start_new_session"] for call in launches.popen] == [True, False]


@pytest.mark.parametrize(("passed", "expected"), [({}, True), ({"options": claude.LaunchOptions(new_group=False)}, False)])
def test_issue_4000_call_claude_passes_the_group_down(launches, passed, expected):
    """#4000: 選び方の `new_group` が `Popen` まで届く（省けば今までどおり新しい組）。"""
    got = claude.call_claude(["claude"], "q", 30, **passed)

    assert got.ok and [call["start_new_session"] for call in launches.popen] == [expected]


def test_issue_4000_call_claude_with_a_zero_slot_wait_does_not_launch(launches, full_pool):
    """#4000: `slot_wait=0` で空きが無ければ `Failure.NO_SLOT` を返し、起動しない。"""
    got = claude.call_claude(["claude"], "q", 30, options=claude.LaunchOptions(slot_wait=0))

    assert got.failure is claude.Failure.NO_SLOT and not got.ok and launches.popen == []


def test_issue_4000_call_claude_checks_the_cli_floor_by_default(launches):
    """#4000: 引数を省けば今までどおり CLI の下限を確かめてから起動する。"""
    got = claude.call_claude(["claude"], "q", 30)

    assert got.ok and launches.floor == ["claude"]


def test_issue_4000_call_claude_can_skip_the_cli_floor(launches):
    """#4000: `cli_floor=False` なら下限を確かめず（`claude --version` を打たず）に起動する。"""
    got = claude.call_claude(["claude"], "q", 30, options=claude.LaunchOptions(cli_floor=False))

    assert got.ok and launches.floor == [] and len(launches.popen) == 1


def test_issue_4000_call_claude_without_ensure_training_refuses_a_stale_record(launches, monkeypatch):
    """#4000: `ensure_training=False` なら学習の設定の記録が無いとき確かめ直さず `Failure.REFUSED` を返し、起動しない。"""
    from ndf.adapters import training

    monkeypatch.setattr(
        launch_slot.training_cache, "verdict", lambda agent, env=None: launch_slot.training_cache.Verdict("記録が無い", stale=True)
    )
    monkeypatch.setattr(training, "ensure", lambda agent, env=None: pytest.fail("確かめ直した"))
    got = claude.call_claude(["claude"], "q", 30, options=claude.LaunchOptions(ensure_training=False))

    assert got.failure is claude.Failure.REFUSED and not got.ok and launches.popen == []
