"""原因の判定と取り消しの走らせ直しの守り（#1649 PR 1673 の指摘）。

| 振る舞い | 確かめること |
| --- | --- |
| 未コミットの変更 | 項目を外した走らせ直しをせず、`reset --hard`・`clean -fd` に届かない |
| 締め切り | 走らせ直しの上限を締め切りまでの残りで切り詰め、過ぎていれば走らせない |
| 複数の suite | 変更起因の suite のすべてが通ったときだけ通ったとする |
| 見分けの記録 | 単数しか持たない旧い記録は単数を使う |
| 検証の走らせ直し | 修正の後の確かめは変更起因の suite のすべてを走らせる |
| 寄せた項目の取り消し | 取り消しの順と走らせ直しの両方が打ち切りの後の取り消しの締め切りで止まる |
| 取り消しの前の未コミットの変更 | 案 A・寄せた項目・絞り込みが共通に使う取り消しは、`drop` を呼ばずに終了コード 4 で止まる |
| 同じ秒の書き換え | 走らせ直しが直前の書き換えの秒の内に終わったら、項目の内容へ戻す前と次の取り消しの前に次の秒まで待つ（#1806 決定 6） |
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


def test_whole_recheck_runs_every_caused_suite(refactor, tmp_path, monkeypatch):
    converge = sys.modules["refactor_lib.commands.converge"]
    seen: list = []
    monkeypatch.setattr(converge, "_whole_items", lambda state, record: [{"id": "I1"}])
    monkeypatch.setattr(converge.targets, "run_or_stop", lambda path, state, rerun, log, **kw: seen.append((rerun, kw["whole"])) or True)
    record = {"rerun_command": "a", "rerun_commands": ["a", "b"]}

    assert converge._recheck_whole(tmp_path / "state.json", _state(tmp_path), record) is False
    assert seen == [(["a", "b"], False)]
    assert record["resolution"] == "fixed"


def test_deferred_revert_stops_at_the_stop_revert_deadline(refactor, tmp_path, monkeypatch):
    gate_ci = sys.modules["refactor_lib.gate_ci"]
    culprit = _culprit(refactor)
    end = NOW + dt.timedelta(seconds=5)
    seen: dict = {}
    monkeypatch.setattr(gate_ci, "live_items", lambda state: [{"id": "I1"}])
    monkeypatch.setattr(gate_ci.timeline, "stop_revert_end", lambda state: end)
    monkeypatch.setattr(culprit, "judge", lambda state, gate, verdict, deadline: type("F", (), {"order": ["I1"]})())
    monkeypatch.setattr(culprit, "rerun_passes", lambda state, rerun, deadline=None: seen.setdefault("passes", deadline))
    monkeypatch.setattr(
        culprit, "revert_in_order", lambda *a, deadline=None, **kw: seen.setdefault("order", deadline) and culprit.Narrowed([], False)
    )
    state = {"whole_test": {"deferred": {"items": ["I1"]}}}
    gate = {"triage": {"caused": ["t"], "rerun_commands": ["a"]}}

    assert gate_ci.revert_deferred(tmp_path / "state.json", state, gate) is False
    assert seen == {"passes": end, "order": end}


def test_revert_in_order_stops_before_discarding_uncommitted_changes(refactor, tmp_path, monkeypatch):
    culprit = _culprit(refactor)
    dropped: list = []
    monkeypatch.setattr(culprit.worktree, "_dirty_paths", lambda state, work: ["src/calc.py"])
    monkeypatch.setattr(culprit, "drop", lambda *a, **kw: dropped.append(a))
    state = {**_state(tmp_path), "items": [{"id": "I1", "status": "verified"}]}

    try:
        culprit.revert_in_order(tmp_path / "state.json", state, ["I1"], "r", lambda: True)
        code = 0
    except SystemExit as e:
        code = e.code

    assert code == 4 and dropped == []


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

    assert culprit._isolate(_state(tmp_path), [item], ["tests/test_a.py::t"], None, {}) == []
    return clock.events


def test_isolation_waits_for_the_next_second_before_restoring_the_item(refactor, tmp_path, monkeypatch):
    events = _isolate_once(refactor, tmp_path, monkeypatch, 0.25)

    assert events == ["revert --no-commit abc", "rerun", "sleep 0.55", "revert --quit", "discard"]


def test_isolation_does_not_wait_when_the_rerun_crossed_the_second(refactor, tmp_path, monkeypatch):
    events = _isolate_once(refactor, tmp_path, monkeypatch, 0.9)

    assert events == ["revert --no-commit abc", "rerun", "revert --quit", "discard"]


def test_revert_in_order_waits_for_the_next_second_before_the_next_drop(refactor, tmp_path, monkeypatch):
    culprit = _culprit(refactor)
    undo = sys.modules["refactor_lib.undo"]
    clock = _Clock(monkeypatch, 100.2)
    monkeypatch.setattr(culprit.worktree, "_dirty_paths", lambda state, work: [])
    monkeypatch.setattr(undo, "_rebuild", lambda path, state, targets, reason, on_conflict: clock.run(0, f"drop {targets[0]}") or {})
    answers = iter([False, True])
    commits = {"test": None, "implement": "abc", "fix": []}
    state = {**_state(tmp_path), "items": [{"id": i, "status": "verified", "commits": commits} for i in ("I1", "I2")]}

    narrowed = culprit.revert_in_order(tmp_path / "state.json", state, ["I1", "I2"], "r", lambda: clock.run(0.25, "rerun") or next(answers))

    assert narrowed.reverted == ["I1", "I2"] and narrowed.passed
    assert clock.events == ["drop I1", "rerun", "sleep 0.55", "drop I2", "rerun"]
