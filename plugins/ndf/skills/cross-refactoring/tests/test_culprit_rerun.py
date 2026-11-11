"""原因の判定の走らせ直しの守り（#1649 PR 1673 の指摘）。

| 振る舞い | 確かめること |
| --- | --- |
| 未コミットの変更 | 項目を外した走らせ直しをせず、`reset --hard`・`clean -fd` に届かない |
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
