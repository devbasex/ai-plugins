"""原因の判定の走らせ直しの守り（#1649 PR 1673 の指摘）。

| 振る舞い | 確かめること |
| --- | --- |
| 未コミットの変更 | 項目を外した走らせ直しをせず、`reset --hard`・`clean -fd` に届かない |
| 見分けの記録 | 単数しか持たない旧い記録は単数を使う |
| 検証の走らせ直し | 修正の後の確かめは変更起因の suite のすべてを走らせ、今回落ちた ID を修正担当へ渡す |
| 同じ秒の書き換え | 走らせ直しが直前の書き換えの秒の内に終わったら、項目の内容へ戻す前に次の秒まで待つ（#1806 決定 6） |
"""

from __future__ import annotations

import datetime as dt
import sys

NOW = dt.datetime(2026, 1, 1, 0, 0, 0)


def _culprit(refactor):
    return sys.modules["refactor_lib.culprit"]


def _state(tmp_path):
    return {"worktrees": {"work": str(tmp_path)}, "tmp_dir": str(tmp_path), "limits": {"test_timeout": 60}}


def test_isolation_stops_before_discarding_uncommitted_changes(refactor, tmp_path, monkeypatch):
    culprit = _culprit(refactor)
    git_calls: list = []
    monkeypatch.setattr(culprit.timeline, "strategy_of", lambda state: object())
    monkeypatch.setattr(culprit.test_triage, "rerun_groups", lambda strategy, files: [["pytest"]])
    monkeypatch.setattr(culprit.worktree, "_dirty_paths", lambda state, work: ["src/calc.py"])
    monkeypatch.setattr(culprit, "git_ok", lambda work, args: git_calls.append(args) or True)
    monkeypatch.setattr(culprit.worktree, "_discard_worktree_changes", lambda work: git_calls.append(["discard"]))
    item = {"id": "I1", "commits": {"test": None, "implement": "abc", "fix": []}}

    left = culprit._isolate(_state(tmp_path), [item], ["tests/test_a.py::t"], {})

    assert left == ["tests/test_a.py::t"]
    assert git_calls == []


def test_rerun_of_prefers_every_suite_and_falls_back_to_the_single_command(refactor):
    culprit = _culprit(refactor)

    assert culprit.rerun_of({"rerun_commands": ["a", "b"], "rerun_command": "a"}) == ["a", "b"]
    assert culprit.rerun_of({"rerun_command": "a"}) == ["a"]
    assert culprit.rerun_of({}) == []


def test_whole_recheck_runs_every_caused_suite(refactor, tmp_path, monkeypatch):
    converge = sys.modules["refactor_lib.commands.converge"]
    seen: list = []
    monkeypatch.setattr(converge, "_whole_items", lambda state, record: [{"id": "I1"}])
    monkeypatch.setattr(converge.triage, "clear_junit", lambda state: None)
    monkeypatch.setattr(converge.targets, "run_or_stop", lambda path, state, rerun, log, **kw: seen.append((rerun, kw["whole"])) or True)
    record = {"rerun_command": "a", "rerun_commands": ["a", "b"]}

    assert converge._recheck_whole(tmp_path / "state.json", _state(tmp_path), record) is False
    assert seen == [(["a", "b"], False)]
    assert record["resolution"] == "fixed"


def test_whole_recheck_hands_the_fixer_the_tests_that_failed_this_time(refactor, tmp_path, monkeypatch):
    """直した後の走らせ直しで落ちた ID を読み直す。初回の集合のままだと、失敗が減っても停滞と判定される。"""
    converge = sys.modules["refactor_lib.commands.converge"]
    item = {"id": "I1", "status": "failing"}
    cleared: list = []
    monkeypatch.setattr(converge, "_whole_items", lambda state, record: [item])
    monkeypatch.setattr(converge.triage, "clear_junit", lambda state: cleared.append(True))
    monkeypatch.setattr(converge.targets, "run_or_stop", lambda *a, **kw: False)
    monkeypatch.setattr(converge.triage, "failed_ids_of", lambda state: (["t::b", "t::pre"], None))
    monkeypatch.setattr(converge.culprit, "fixable", lambda record: True)
    record = {"rerun_commands": ["a"], "rerun_command": "a", "caused": ["t::a", "t::b", "t::c"], "preexisting": ["t::pre"]}

    assert converge._recheck_whole(tmp_path / "state.json", _state(tmp_path), record) is True
    assert cleared == [True]
    assert record["caused"] == ["t::b"]
    assert item["failed_check"]["tests"] == ["t::b"]


def test_whole_recheck_keeps_the_first_set_when_the_junit_is_unreadable(refactor, tmp_path, monkeypatch):
    converge = sys.modules["refactor_lib.commands.converge"]
    item = {"id": "I1", "status": "failing"}
    monkeypatch.setattr(converge, "_whole_items", lambda state, record: [item])
    monkeypatch.setattr(converge.triage, "clear_junit", lambda state: None)
    monkeypatch.setattr(converge.targets, "run_or_stop", lambda *a, **kw: False)
    monkeypatch.setattr(converge.triage, "failed_ids_of", lambda state: (None, "no junit"))
    monkeypatch.setattr(converge.culprit, "fixable", lambda record: True)
    record = {"rerun_commands": ["a"], "rerun_command": "a", "caused": ["t::a", "t::b"]}

    assert converge._recheck_whole(tmp_path / "state.json", _state(tmp_path), record) is True
    assert item["failed_check"]["tests"] == ["t::a", "t::b"]


class _Clock:
    """worktree の時刻と待ちの差し替え。待ちと書き換えの順を `events` に残す。"""

    def __init__(self, monkeypatch, start):
        self.now, self.events = start, []
        worktree = sys.modules["refactor_lib.worktree"]
        monkeypatch.setattr(worktree, "_last_rewrite", None)
        monkeypatch.setattr(worktree, "_clock", lambda: self.now)
        monkeypatch.setattr(worktree, "_sleep", self.sleep)

    def sleep(self, seconds):
        self.events.append(f"sleep {round(seconds, 3)}")
        self.now += seconds

    def run(self, seconds, event):
        self.events.append(event)
        self.now += seconds


def _isolate_once(refactor, tmp_path, monkeypatch, run_seconds):
    culprit = _culprit(refactor)
    clock = _Clock(monkeypatch, 100.2)
    monkeypatch.setattr(culprit.timeline, "strategy_of", lambda state: object())
    monkeypatch.setattr(culprit.test_triage, "rerun_groups", lambda strategy, files: [["pytest"]])
    monkeypatch.setattr(culprit.worktree, "_dirty_paths", lambda state, work: [])
    monkeypatch.setattr(culprit, "git_ok", lambda work, args: clock.run(0, " ".join(args)) or True)
    monkeypatch.setattr(culprit.worktree, "_discard_worktree_changes", lambda work: clock.run(0, "discard"))
    monkeypatch.setattr(culprit.test_triage, "failing_in", lambda *a: clock.run(run_seconds, "rerun") or ([], None, None))
    item = {"id": "I1", "commits": {"test": None, "implement": "abc", "fix": []}}

    assert culprit._isolate(_state(tmp_path), [item], ["tests/test_a.py::t"], {}) == []
    return clock.events


def test_isolation_waits_for_the_next_second_before_restoring_the_item(refactor, tmp_path, monkeypatch):
    events = _isolate_once(refactor, tmp_path, monkeypatch, 0.25)

    assert events == ["revert --no-commit abc", "rerun", "sleep 0.55", "revert --quit", "discard"]


def test_isolation_does_not_wait_when_the_rerun_crossed_the_second(refactor, tmp_path, monkeypatch):
    events = _isolate_once(refactor, tmp_path, monkeypatch, 0.9)

    assert events == ["revert --no-commit abc", "rerun", "revert --quit", "discard"]
