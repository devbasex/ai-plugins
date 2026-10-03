"""原因の判定と取り消しの走らせ直しの守り（#1649 PR 1673 の指摘）。

| 振る舞い | 確かめること |
| --- | --- |
| 未コミットの変更 | 項目を外した走らせ直しをせず、`reset --hard`・`clean -fd` に届かない |
| 締め切り | 走らせ直しの上限を締め切りまでの残りで切り詰め、過ぎていれば走らせない |
| 複数の suite | 変更起因の suite のすべてが通ったときだけ通ったとする |
| 見分けの記録 | 単数しか持たない旧い記録は単数を使う |
"""

from __future__ import annotations

import datetime as dt
import sys

NOW = dt.datetime(2026, 1, 1, 0, 0, 0)


def _culprit(refactor):
    return sys.modules["refactor_lib.culprit"]


def _state(tmp_path):
    return {"worktrees": {"work": str(tmp_path)}, "tmp_dir": str(tmp_path), "limits": {"test_timeout": 60}}


def _record_runs(monkeypatch, culprit, codes):
    runs: list = []

    def run(command, work, limit):
        runs.append((command, limit))
        return codes.get(command, 0), False

    monkeypatch.setattr(culprit, "run_with_timeout", run)
    monkeypatch.setattr(culprit.clock, "now", lambda: NOW)
    return runs


def test_isolation_stops_before_discarding_uncommitted_changes(refactor, tmp_path, monkeypatch):
    culprit = _culprit(refactor)
    git_calls: list = []
    monkeypatch.setattr(culprit.timeline, "strategy_of", lambda state: object())
    monkeypatch.setattr(culprit.test_triage, "rerun_groups", lambda strategy, files: [["pytest"]])
    monkeypatch.setattr(culprit.worktree, "_dirty_paths", lambda state, work: ["src/calc.py"])
    monkeypatch.setattr(culprit, "git_ok", lambda work, args: git_calls.append(args) or True)
    monkeypatch.setattr(culprit.worktree, "_discard_worktree_changes", lambda work: git_calls.append(["discard"]))
    item = {"id": "I1", "commits": {"test": None, "implement": "abc", "fix": []}}

    left = culprit._isolate(_state(tmp_path), [item], ["tests/test_a.py::t"], None, {})

    assert left == ["tests/test_a.py::t"]
    assert git_calls == []


def test_rerun_is_cut_to_the_time_left_before_the_deadline(refactor, tmp_path, monkeypatch):
    culprit = _culprit(refactor)
    runs = _record_runs(monkeypatch, culprit, {})

    assert culprit.rerun_passes(_state(tmp_path), "pytest -q a", NOW + dt.timedelta(seconds=2))()
    assert runs == [("pytest -q a", 2)]


def test_rerun_after_the_deadline_does_not_run(refactor, tmp_path, monkeypatch):
    culprit = _culprit(refactor)
    runs = _record_runs(monkeypatch, culprit, {})

    assert not culprit.rerun_passes(_state(tmp_path), "pytest -q a", NOW - dt.timedelta(seconds=1))()
    assert runs == []


def test_rerun_passes_only_when_every_suite_passes(refactor, tmp_path, monkeypatch):
    culprit = _culprit(refactor)
    runs = _record_runs(monkeypatch, culprit, {"pytest -q b": 1})

    assert not culprit.rerun_passes(_state(tmp_path), ["pytest -q a", "pytest -q b"])()
    assert [c for c, _ in runs] == ["pytest -q a", "pytest -q b"]
    assert culprit.rerun_passes(_state(tmp_path), ["pytest -q a", "pytest -q c"])()


def test_rerun_of_prefers_every_suite_and_falls_back_to_the_single_command(refactor):
    culprit = _culprit(refactor)

    assert culprit.rerun_of({"rerun_commands": ["a", "b"], "rerun_command": "a"}) == ["a", "b"]
    assert culprit.rerun_of({"rerun_command": "a"}) == ["a"]
    assert culprit.rerun_of({}) == []
