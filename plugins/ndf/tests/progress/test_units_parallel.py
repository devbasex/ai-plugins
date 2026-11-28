"""単位を依存の順に並列で流す処理（#3702 の決定 9・受け入れ条件 3）のテスト。

並びを流す順（`units_flow.flow`）は、プールと待ちを偽にして、終わる順をテストが決める（スレッドも本物の待ちも使わない）。
`run_stage` を通すテストは、`test_units_run.py` の `World`（手元の git は本物、プランとコマンドは偽）を使い、プールと待ちは
同じ偽に差し替える。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from concurrent.futures import Future
from pathlib import Path

import pytest

from ndf.adapters import git, github, github_pulls
from ndf.contracts import named_cap, result
from ndf.core.plan.review_units_decide import Unit, unit_key
from ndf.progress import invoke, issue_values, units_flow, units_invoke, units_queue
from ndf.progress.plan_runs import StageResult, UnitsStopped


def _load_units_run():
    spec = importlib.util.spec_from_file_location("units_run", Path(__file__).with_name("test_units_run.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


units_run = _load_units_run()
World, _git = units_run.World, units_run._git


def _unit(name: str, *after: str, issue: int = 3, whole: bool = False) -> Unit:
    return Unit(name, issue, "", (), (), whole, tuple(unit_key(3, a) for a in after))


class FakePool:
    """偽のプールと待ち。投入した呼び出しは待たれるまで走らず、待ち 1 回ごとに `pick` が選んだものを終える
    （既定は後に投入したもの 1 つ）。"""

    def __init__(self) -> None:
        self.running: dict[Future, object] = {}
        self.widest = 0
        self.limit = 0
        self.now = 0.0

    def pool(self, max_workers: int):
        self.limit = max_workers
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None

    def submit(self, call):
        future: Future = Future()
        self.running[future] = call
        self.widest = max(self.widest, len(self.running))
        return future

    def finish(self, future: Future) -> None:
        """呼び出しを走らせて終わらせる（終わっていれば何もしない）。"""
        if future.done():
            return
        try:
            future.set_result(self.running[future]())
        except Exception as exc:  # noqa: BLE001
            future.set_exception(exc)

    def pick(self) -> list[Future]:
        return [list(self.running)[-1]]

    def wait(self, waiting, timeout=None, return_when=None):
        assert waiting, "待つ相手が無いのに待った"
        done = set(self.pick())
        if not done:
            # 何も終わらずに時間が切れた
            assert timeout is not None, "待ちの時刻が無いのに、何も終わらなかった"
            self.now += timeout
        for future in done:
            self.finish(future)
            del self.running[future]
        return done, set(waiting) - done

    def workers(self) -> units_flow.Workers:
        return units_flow.Workers(self.pool, self.wait)


class Fake(FakePool):
    """偽のプール・待ち・単位の操作。`finish` は待ち 1 回ごとに終える単位の名前の並び（尽きれば起動の順に 1 つ終える）。

    `log` は起動（`start`）・マージ（`merge`）・止まった単位（`fail`）の順。`stops` は単位の名前 → `impl` の停止理由、
    `merge_stops` はマージの停止理由。`during` は (`start` か `merge`, 単位の名前) → その操作の間に `impl` が終わる単位。
    """

    def __init__(self, units, *finish, stops=None, merge_stops=None, merge_only=(), during=None, outdated=()) -> None:
        super().__init__()
        self.during = during or {}
        self.units = tuple(units)
        self.script = [list(f) for f in finish]
        self.stops = stops or {}
        self.merge_stops = merge_stops or {}
        self.merge_only = set(merge_only)
        self.outdated = set(outdated)
        self.log: list[tuple[str, str]] = []

    # --- 待ち ---

    def pick(self) -> list[Future]:
        names = self.script.pop(0) if self.script else [next(iter(self.running.values())).name]
        done = [f for f, call in self.running.items() if call.name in names]
        assert len(done) == len(names), f"{names} は実行中でない: {[c.name for c in self.running.values()]}"
        return done

    def meanwhile(self, kind: str, name: str) -> None:
        if (kind, name) in self.during:
            self.finish(next(f for f, call in self.running.items() if call.name == self.during[kind, name]))

    # --- 単位の操作 ---

    def start(self, unit: Unit) -> units_flow.Started:
        self.log.append(("start", unit.name))
        self.meanwhile("start", unit.name)

        def call():
            if unit.name in self.stops:
                if isinstance(self.stops[unit.name], Exception):
                    raise self.stops[unit.name]
                return self.stops[unit.name]
            return "ok"

        call.name = unit.name

        def land(got):
            if got not in ("ok", None):
                raise UnitsStopped(got, f"{unit.name} が {got}")
            if unit.name in self.merge_stops:
                raise UnitsStopped(self.merge_stops[unit.name], f"{unit.name} をマージできない")
            if unit.name in self.outdated:
                self.outdated.discard(unit.name)
                raise units_flow.Outdated("not_ci", f"{unit.name} が古い")
            self.log.append(("merge", unit.name))
            self.meanwhile("merge", unit.name)
            return {"name": unit.name, "status": "merged"}, github_pulls.MergedPull(1, unit.name)

        return units_flow.Started(None if unit.name in self.merge_only else call, land)

    def flow(self, *, limit: int = 6, merged=(), chained: bool = False) -> units_flow.Flowed:
        def failed(unit, exc):
            self.log.append(("fail", unit.name))
            if isinstance(exc, UnitsStopped):
                return exc
            return UnitsStopped("precondition", str(exc))

        order = units_flow.Order(self.units, frozenset(unit_key(3, m) for m in merged), chained=chained)
        ops = units_flow.Ops(self.start, failed, lambda got: got == "usage_limit")
        return units_flow.flow(order, ops, limit, self.workers())

    def at(self, kind: str, name: str) -> int:
        return self.log.index((kind, name))

    def names(self, kind: str) -> list[str]:
        return [n for k, n in self.log if k == kind]


def test_a_unit_starts_as_soon_as_its_own_prerequisites_are_merged():
    fake = Fake([_unit("A"), _unit("B"), _unit("C", "A")], ["A"])

    got = fake.flow()

    assert fake.log[:2] == [("start", "A"), ("start", "B")]
    # B が終わる前に、A だけに依存する C が始まる
    assert fake.at("merge", "A") < fake.at("start", "C") < fake.at("merge", "B")
    assert not got.failures and not got.waiting and len(got.items) == 3
    assert [p.merge_sha for p in got.pulls] == fake.names("merge")


def test_units_over_the_limit_start_when_a_slot_is_free_in_the_merge_order():
    fake = Fake([_unit("A"), _unit("B"), _unit("C"), _unit("D")], ["B"])

    got = fake.flow(limit=2)

    assert fake.limit == 2 and fake.widest == 2
    assert fake.names("start") == ["A", "B", "C", "D"]
    assert fake.at("merge", "B") < fake.at("start", "C")
    assert len(got.items) == 4


def test_plans_that_finish_together_are_merged_one_by_one_in_the_merge_order():
    fake = Fake([_unit("A"), _unit("B")], ["B", "A"])

    fake.flow()

    assert fake.names("merge") == ["A", "B"]


def test_a_free_slot_is_filled_after_each_merge_and_the_merges_are_taken_in_together():
    fake = Fake([_unit("A"), _unit("B"), _unit("C"), _unit("D", "A")], ["A", "B"])

    fake.flow(limit=2)

    assert fake.at("merge", "A") < fake.at("start", "C") < fake.at("merge", "B") < fake.at("start", "D")


def test_an_outdated_unit_is_started_again_and_merged():
    fake = Fake([_unit("A"), _unit("B", "A")], outdated=["A"])

    got = fake.flow()

    assert fake.names("start") == ["A", "A", "B"] and fake.names("merge") == ["A", "B"]
    assert not got.failures and len(got.items) == 2


def test_a_failed_unit_holds_back_only_the_units_that_need_it():
    units = [_unit("A"), _unit("B"), _unit("C", "A"), _unit("D", "C"), _unit("E", "B")]
    fake = Fake(units, stops={"A": "test_failed"})

    got = fake.flow()

    assert fake.names("start") == ["A", "B", "E"] and fake.names("merge") == ["B", "E"]
    assert [(u.name, e.kind) for u, e in got.failures] == [("A", "test_failed")]
    assert [(u.name, why) for u, why in got.waiting] == [("C", "先に要る単位 A が止まった"), ("D", "先に要る単位 C が始まらない")]


def test_a_usage_limit_starts_nothing_more_but_merges_the_running_units():
    units = [_unit("A"), _unit("B"), _unit("C"), _unit("D", "B"), _unit("whole", issue=4, whole=True)]
    fake = Fake(units, ["A"], stops={"A": "usage_limit"})

    got = fake.flow(limit=2)

    assert fake.names("start") == ["A", "B"] and fake.names("merge") == ["B"]
    assert [u.name for u, _ in got.waiting] == ["C", "D", "whole"]
    assert all("利用上限" in why for _, why in got.waiting)


@pytest.mark.parametrize(
    ("during", "started", "waiting"),
    [
        # A のマージの間に B の impl が利用上限で終わる。C は用意もしない
        (("merge", "A"), ["A", "B"], ["C"]),
        # C の用意の間に終わる。用意した C は投入しない
        (("start", "C"), ["A", "B", "C"], ["C"]),
    ],
)
def test_a_usage_limit_that_is_not_received_yet_starts_nothing_more(during, started, waiting):
    fake = Fake([_unit("A"), _unit("B"), _unit("C")], ["A"], stops={"B": "usage_limit"}, during={during: "B"})

    got = fake.flow(limit=2)

    assert fake.names("start") == started and fake.names("merge") == ["A"]
    assert [(u.name, e.kind) for u, e in got.failures] == [("B", "usage_limit")]
    assert [u.name for u, _ in got.waiting] == waiting and "利用上限" in got.waiting[0][1]


def test_the_supplemented_units_run_alone_after_the_file_units_in_the_issue_order():
    units = [_unit("w5", issue=5, whole=True), _unit("A"), _unit("w4", issue=4, whole=True), _unit("B", "A")]
    fake = Fake(units, stops={"B": "test_failed"})

    got = fake.flow()

    # 単位のファイルの単位が失敗しても始め、1 つずつ流す
    assert fake.log == [
        ("start", "A"), ("merge", "A"), ("start", "B"), ("fail", "B"),
        ("start", "w4"), ("merge", "w4"), ("start", "w5"), ("merge", "w5"),
    ]  # fmt: skip
    assert fake.widest == 1 and not got.waiting


@pytest.mark.parametrize("kind", ["ci_pending", "infra_wait"])
def test_the_supplemented_units_are_held_while_a_pull_may_stay_in_the_queue(kind):
    fake = Fake([_unit("A"), _unit("w4", issue=4, whole=True)], merge_stops={"A": kind})

    got = fake.flow()

    assert fake.names("start") == ["A"]
    assert [u.name for u, _ in got.waiting] == ["w4"]


def test_merged_units_are_left_out_and_their_dependents_start_at_once():
    fake = Fake([_unit("A"), _unit("B", "A"), _unit("C", "B")])

    got = fake.flow(merged=["A"])

    assert fake.names("start") == ["B", "C"] and len(got.items) == 2


def test_units_without_dependencies_from_a_table_run_one_by_one_and_stop_at_the_first_failure():
    units = [_unit("A"), _unit("B"), _unit("C", whole=True, issue=4), _unit("D")]
    fake = Fake(units, stops={"C": "test_failed"})

    got = fake.flow(chained=True, merged=["B"])

    assert fake.names("start") == ["A", "C"] and fake.widest == 1
    assert [u.name for u, _ in got.waiting] == ["D"]


def test_a_merge_only_unit_is_merged_without_a_plan():
    fake = Fake([_unit("A"), _unit("B", "A")], merge_only=["A"])

    fake.flow()

    assert fake.log[:3] == [("start", "A"), ("merge", "A"), ("start", "B")]


def test_an_exception_of_the_plan_is_the_failure_of_that_unit_only():
    fake = Fake([_unit("A"), _unit("B")], stops={"A": OSError("書けない")})

    got = fake.flow()

    assert fake.names("merge") == ["B"]
    assert [(u.name, e.detail) for u, e in got.failures] == [("A", "書けない")]


# --- 止まった結果の種類 -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kinds", "expected"),
    [
        (["test_failed", "usage_limit"], "usage_limit"),
        (["ci_pending", "test_failed"], "test_failed"),
        (["ci_pending", "infra_wait"], "infra_wait"),
        (["ci_pending", "ci_pending"], "ci_pending"),
    ],
)
def test_the_kind_of_the_stopped_stage(kinds, expected):
    failures = tuple((_unit(f"u{i}"), UnitsStopped(k, k, retry_after=i)) for i, k in enumerate(kinds))

    got = units_flow.primary(failures)

    assert got.kind == expected


# --- マージキューと反映の待ち ---------------------------------------------------------------

QUEUED, OPEN, CLOSED = units_queue.State("queued"), units_queue.State("open"), units_queue.State("closed")
MERGED, UNKNOWN = units_queue.State("merged", "sha"), units_queue.State("unknown")


class Waiting(Fake):
    """キューの待ちを持つ偽。`queue` の単位は最初のマージが `Queued` になる。`states` は単位の名前 → 読むたびに返す状態
    （尽きれば最後の値）、`visible` は単位の名前 → 反映できるようになる時刻。打ち直し（`resend`）は `resends` の値（"merged" か "queued"）。"""

    def __init__(self, tmp_path, units, *finish, queue=(), states=None, visible=None, resends=(), budget=3600.0, **kw) -> None:
        super().__init__(units, *finish, **kw)
        self.tmp = tmp_path
        self.queue = set(queue)
        self.states = {k: list(v) for k, v in (states or {}).items()}
        self.visible = visible or {}
        self.resends = list(resends)
        self.budget = budget
        self.reads: list[tuple[float, list[str]]] = []
        self.advances: list[tuple[float, list[str]]] = []
        self.slept: list[float] = []

    def number(self, name: str) -> int:
        return [u.name for u in self.units].index(name) + 1

    def name(self, number: int) -> str:
        return self.units[number - 1].name

    def ticket(self, name: str) -> units_queue.Ticket:
        def resend():
            self.log.append(("resend", name))
            if self.resends.pop(0) == "queued":
                raise self.queued(name)
            return {"name": name, "status": "merged"}, github_pulls.MergedPull(self.number(name), name)

        return units_queue.Ticket(self.number(name), self.tmp / name / "queue.json", resend)

    def queued(self, name: str) -> units_flow.Queued:
        exc = units_flow.Queued("ci_pending", f"{name} はキュー")
        exc.ticket = self.ticket(name)
        return exc

    def start(self, unit: Unit) -> units_flow.Started:
        started = super().start(unit)

        def land(got):
            if unit.name in self.queue:
                self.queue.discard(unit.name)
                raise self.queued(unit.name)
            return started.land(got)

        return units_flow.Started(started.call, land)

    def read(self, numbers):
        names = [self.name(n) for n in numbers]
        self.reads.append((self.now, names))
        return {self.number(n): self.states[n].pop(0) if len(self.states[n]) > 1 else self.states[n][0] for n in names}

    def advance(self, pulls):
        names = [next(u.name for u in self.units if units_flow.key_of(u) == k) for k in pulls]
        self.advances.append((self.now, names))
        return {k for k, n in zip(pulls, names) if self.now >= self.visible.get(n, 0)}

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def watching(self) -> units_queue.Watching:
        return units_queue.Watching(self.read, self.advance, self.tmp / "ingest.json", self.budget, lambda: self.now, self.sleep)

    def flow(self, *, limit: int = 6, merged=(), chained: bool = False, landed=()) -> units_flow.Flowed:
        def failed(unit, exc):
            self.log.append(("fail", unit.name))
            return exc if isinstance(exc, UnitsStopped) else UnitsStopped("precondition", str(exc))

        order = units_flow.Order(self.units, frozenset(unit_key(3, m) for m in merged), chained=chained)
        ops = units_flow.Ops(self.start, failed, lambda got: got == "usage_limit", self.watching())
        outside = {unit_key(3, n): github_pulls.MergedPull(self.number(n), n) for n in landed}
        return units_flow.flow(order, ops, limit, self.workers(), outside)


def test_a_unit_merged_by_the_queue_starts_its_dependents_while_an_unrelated_unit_runs(tmp_path):
    # X は 2 回の時間切れの間も実行中のまま
    fake = Waiting(tmp_path, [_unit("A"), _unit("X"), _unit("B", "A")], ["A"], [], [], ["B"], queue=["A"], states={"A": [QUEUED, MERGED]})

    got = fake.flow()

    assert fake.at("start", "B") < fake.at("merge", "X")
    assert [names for _, names in fake.reads] == [["A"], ["A"]]
    assert [t for t, _ in fake.reads] == [10, 20]
    assert not got.failures and len(got.items) == 3
    assert fake.slept == []


def test_plans_that_finish_within_the_interval_do_not_add_queue_reads(tmp_path):
    fake = Waiting(
        tmp_path, [_unit("A"), _unit("X"), _unit("Y"), _unit("Z")], ["A"], ["X"], ["Y"], ["Z"], queue=["A"], states={"A": [MERGED]}
    )

    got = fake.flow()

    assert not got.failures and len(fake.reads) == 1 and fake.slept == [10]


def test_only_waiting_pulls_left_sleeps_to_the_next_read_once(tmp_path):
    fake = Waiting(tmp_path, [_unit("A")], queue=["A"], states={"A": [QUEUED, QUEUED, QUEUED, MERGED]})

    got = fake.flow()

    # 1 回の眠りで次の時刻まで待ち、状態が変わらない間は間隔を延ばす
    assert fake.slept == [10, 10, 15, 22.5] and len(fake.reads) == 4 and len(got.items) == 1


def test_merges_found_by_one_read_are_taken_in_by_one_call(tmp_path):
    fake = Waiting(tmp_path, [_unit("A"), _unit("B")], queue=["A", "B"], states={"A": [MERGED], "B": [MERGED]})

    got = fake.flow()

    assert len(fake.reads) == 1 and [sorted(n) for _, n in fake.advances] == [["A", "B"]]
    assert len(got.items) == 2


def test_a_merge_not_visible_yet_is_taken_in_at_the_next_time_before_the_dependents_start(tmp_path):
    fake = Waiting(tmp_path, [_unit("A"), _unit("B", "A")], visible={"A": 5})

    got = fake.flow()

    assert [t for t, _ in fake.advances][:2] == [0, 10] and fake.slept[0] == 10
    assert fake.reads == [] and fake.names("start") == ["A", "B"] and not got.failures
    assert json.loads((tmp_path / "ingest.json").read_text()) == {}


def test_a_unit_merged_before_the_run_holds_its_dependents_until_it_is_taken_in(tmp_path):
    fake = Waiting(tmp_path, [_unit("A"), _unit("B", "A"), _unit("C")], visible={"A": 5})

    got = fake.flow(landed=["A"])

    assert fake.names("start") == ["C", "B"] and fake.names("merge") == ["C", "B"]
    assert [t for t, names in fake.advances if "A" in names] == [0, 10]
    assert not got.failures and not got.waiting


def test_a_take_in_that_goes_over_the_limit_during_the_read_is_not_tried_again(tmp_path):
    (tmp_path / "ingest.json").write_text(json.dumps({unit_key(3, "A"): {"sha": "A", "merged_at": -3585}}), encoding="utf-8")
    fake = Waiting(tmp_path, [_unit("A"), _unit("B")], queue=["B"], states={"B": [MERGED]}, visible={"A": 10**9})
    read = fake.read

    def slow_read(numbers):
        fake.now += 10
        return read(numbers)

    fake.read = slow_read

    got = fake.flow(landed=["A"])

    # 上限を超えた A は、B の反映に含めて確かめるだけで、取り込まれていなければ失敗のまま
    assert [(t, sorted(names)) for t, names in fake.advances if "A" in names] == [(0, ["A"]), (20, ["A", "B"])]
    assert [(u.name, e.kind) for u, e in got.failures] == [("A", "ci_pending")]


def test_an_overdue_merge_taken_in_with_a_new_merge_is_no_longer_a_failure(tmp_path):
    (tmp_path / "ingest.json").write_text(json.dumps({unit_key(3, "A"): {"sha": "A", "merged_at": -3585}}), encoding="utf-8")
    units = [_unit("A"), _unit("B"), _unit("C", "A")]
    fake = Waiting(tmp_path, units, queue=["B"], states={"B": [MERGED]}, visible={"A": 15})
    read = fake.read

    def slow_read(numbers):
        fake.now += 10
        return read(numbers)

    fake.read = slow_read

    got = fake.flow(landed=["A"])

    assert not got.failures and not got.waiting
    assert fake.names("start") == ["B", "C"]
    assert json.loads((tmp_path / "ingest.json").read_text()) == {}


def test_the_wait_for_a_take_in_is_counted_from_the_recorded_time(tmp_path):
    (tmp_path / "ingest.json").write_text(json.dumps({unit_key(3, "A"): {"sha": "A", "merged_at": -3590}}), encoding="utf-8")
    units = [_unit("A"), _unit("w4", issue=4, whole=True)]
    fake = Waiting(tmp_path, units, visible={"A": 10**9})

    got = fake.flow(landed=["A"])

    assert fake.slept == [10] and [(u.name, e.kind) for u, e in got.failures] == [("A", "ci_pending")]
    # 反映できていないマージがある間は、補った単位を起動しない
    assert fake.names("start") == [] and [u.name for u, _ in got.waiting] == ["w4"]


@pytest.mark.parametrize(
    ("read_state", "kind"),
    [(QUEUED, "ci_pending"), (UNKNOWN, "ci_pending"), (units_queue.State("unknown", retry_after=10_000), "infra_wait")],
)
def test_a_pull_over_the_wait_limit_fails_while_other_units_run_and_holds_the_supplemented_units(tmp_path, read_state, kind):
    units = [_unit("A"), _unit("X"), _unit("w4", issue=4, whole=True)]
    # X は A の上限（25 秒）を過ぎるまで終わらない
    fake = Waiting(tmp_path, units, ["A"], [], [], ["X"], queue=["A"], states={"A": [read_state]}, budget=25)

    got = fake.flow()

    # 上限の時刻には読まずに外す
    assert [t for t, _ in fake.reads] == [10, 20]
    assert [(u.name, e.kind) for u, e in got.failures] == [("A", kind)]
    assert fake.names("start") == ["A", "X"] and [u.name for u, _ in got.waiting] == ["w4"]
    assert not [e for e in fake.log if e[0] == "resend"]


def test_a_wait_over_the_limit_is_neither_read_nor_sent_when_the_read_time_has_also_passed(tmp_path):
    fake = Waiting(tmp_path, [_unit("A")], queue=["A"], states={"A": [OPEN]}, budget=25)
    fake.sleep = lambda seconds: setattr(fake, "now", fake.now + seconds + 100)

    got = fake.flow()

    assert fake.reads == [] and fake.names("resend") == []
    assert [(u.name, e.kind) for u, e in got.failures] == [("A", "ci_pending")]


def test_a_pull_dropped_from_the_queue_is_sent_once_and_a_second_drop_holds_the_supplemented_units(tmp_path):
    units = [_unit("A"), _unit("w4", issue=4, whole=True)]
    fake = Waiting(tmp_path, units, queue=["A"], states={"A": [OPEN]}, resends=["queued"])

    got = fake.flow()

    assert fake.names("resend") == ["A"] and json.loads((tmp_path / "A" / "queue.json").read_text())["resent"] is True
    assert [(u.name, e.kind) for u, e in got.failures] == [("A", "precondition")]
    assert fake.names("start") == ["A"] and [u.name for u, _ in got.waiting] == ["w4"]


@pytest.mark.parametrize("state", [CLOSED, OPEN])
def test_a_closed_pull_or_a_drop_after_an_earlier_resend_releases_the_supplemented_units(tmp_path, state):
    (tmp_path / "A").mkdir()
    (tmp_path / "A" / "queue.json").write_text(json.dumps({"pr": 1, "resent": True, "queued_at": 0}), encoding="utf-8")
    units = [_unit("A"), _unit("w4", issue=4, whole=True)]
    fake = Waiting(tmp_path, units, queue=["A"], states={"A": [state]})

    got = fake.flow()

    assert [(u.name, e.kind) for u, e in got.failures] == [("A", "precondition")]
    assert fake.names("resend") == [] and fake.names("start") == ["A", "w4"]


def test_a_resent_unit_refused_as_behind_is_started_again_within_the_limit(tmp_path):
    units = [_unit("A"), _unit("X"), _unit("Y")]
    # A の打ち直しが古い HEAD で断られたとき、同時に流せる 1 本は X が使っている
    fake = Waiting(tmp_path, units, ["A"], [], ["X"], ["A"], ["Y"], queue=["A"], states={"A": [OPEN]})
    ticket = fake.ticket

    def outdated_ticket(name):
        def resend():
            raise units_flow.Outdated("not_ci", f"{name} が古い")

        return ticket(name)._replace(resend=resend)

    fake.ticket = outdated_ticket

    got = fake.flow(limit=1)

    assert fake.widest == 1 and fake.names("start").count("A") == 2
    assert not got.failures and len(got.items) == 3


def test_a_resent_merge_only_unit_refused_as_behind_stays_outdated(tmp_path):
    (tmp_path / "queue.json").write_text(json.dumps({"pr": 1, "resent": False, "queued_at": 0}), encoding="utf-8")

    def resend():
        raise units_flow.Outdated("not_ci", "古い")

    ticket = units_queue.Ticket(1, tmp_path / "queue.json", resend)

    with pytest.raises(units_flow.Outdated):
        units_flow.remerge(_unit("A"), ticket, lambda numbers: {1: OPEN}, lambda: 0)


def test_the_reset_time_of_the_first_read_of_a_rerun_is_kept_for_an_expired_wait(tmp_path):
    (tmp_path / "queue.json").write_text(json.dumps({"pr": 1, "resent": False, "queued_at": 0}), encoding="utf-8")
    ticket = units_queue.Ticket(1, tmp_path / "queue.json", lambda: pytest.fail("打ち直さない"))
    limited = units_queue.State("unknown", retry_after=600)
    with pytest.raises(units_flow.Queued) as raised:
        units_flow.remerge(_unit("A"), ticket, lambda numbers: {1: limited}, lambda: 4000)
    board = units_queue.Board(units_queue.Watching(budget=3600, clock=lambda: 4000))

    assert not board.add_queued("A", raised.value.ticket)
    assert board.resets_after_deadline("A") == 600


def test_the_queue_state_is_read_without_the_rest_fallback():
    def pull(**kw):
        base = dict(number=1, state="open", merged=False, head_sha="h", head_ref="b", base_ref="s", body="", merge_state="clean",
                    author="a", association="OWNER")  # fmt: skip
        return github_pulls.Pull(**{**base, **kw})

    class Hub:
        def pulls(self, numbers, spare=(), *, rest_fallback=True):
            assert rest_fallback is False
            other = pull(merged=True, state="closed", merge_sha="o", base_ref="other")
            return {
                1: pull(merged=True, state="closed", merge_sha="m"),
                2: pull(queued=True),
                3: pull(),
                4: pull(state="closed"),
                5: None,
                6: other,
            }

    class Limited:
        def pulls(self, numbers, spare=(), *, rest_fallback=True):
            raise github.GitHubError("上限", status=403, retry_after=60, quota="graphql")

    got = units_queue.read(Hub(), [1, 2, 3, 4, 5, 6], "s")

    assert [got[n].kind for n in range(1, 7)] == ["merged", "queued", "open", "closed", "unknown", "elsewhere"]
    assert got[1].merge_sha == "m"
    assert units_queue.read(Limited(), [1], "s") == {1: units_queue.State("unknown", retry_after=60)}


@pytest.mark.parametrize(
    ("kind", "resent", "expected"),
    [
        ("merged", False, "done"),
        ("queued", True, "wait"),
        ("unknown", False, "wait"),
        ("open", False, "resend"),
        ("open", True, "fail"),
        ("closed", False, "fail"),
        ("elsewhere", False, "fail"),
    ],
)
def test_the_next_step_for_the_state(kind, resent, expected):
    assert units_queue.action(units_queue.State(kind), resent) == expected


# --- run_stage を通す ---------------------------------------------------------------------------

PLAN_PATH = "issues/issue-3-plan.md"
UNITS_PATH = "issues/issue-3-plan.units.json"


def _task(tid: str, criteria=()) -> dict:
    return {"id": tid, "title": tid, "files": [f"src/{tid}.py"], "lines": 10, "criteria": list(criteria), "after": []}


def _doc() -> dict:
    """依存の無い単位 2 つ（甲・乙）。"""
    return {
        "version": 1,
        "issues": [3],
        "tasks": [_task("T1", ["3:1"]), _task("T2", ["3:2"])],
        "units": [{"name": "甲", "tasks": ["T1"], "after": []}, {"name": "乙", "tasks": ["T2"], "after": []}],
    }


class Parallel(World):
    """単位のファイルから流す `World`。`impl` は偽のプール（`fake_pool`）が、後に投入したものから終える。

    マージは、スプリントブランチをマージしたコミットを送る（先にマージされた別の単位があっても進められる。GitHub の代わり）。
    """

    def __init__(self, tmp_path) -> None:
        super().__init__(tmp_path)
        plan = "# 3: 実装計画\n\n## タスク分解\n\n### Task 1: 甲\n\n### Task 2: 乙\n\n## レビューの単位\n"
        (self.sprint / PLAN_PATH).write_text(plan, encoding="utf-8")
        (self.sprint / UNITS_PATH).write_text(json.dumps(_doc(), ensure_ascii=False), encoding="utf-8")
        _git(self.sprint, "add", "issues")
        _git(self.sprint, "commit", "-q", "-m", "units")
        self.fake_pool = FakePool()
        self.order: list[str] = []

    def unit_names(self) -> dict[str, str]:
        units = [("甲", 3), ("乙", 3), ("issue-4", 4)]
        return {n: units_invoke.unit_branch("sprint/s1", Unit(n, i, "", (), (), False)) for n, i in units}

    def name_of(self, branch: str) -> str:
        return next(n for n, b in self.unit_names().items() if b == branch)

    def ports(self) -> units_invoke.Ports:
        return super().ports()._replace(workers=self.fake_pool.workers())

    def land(self, worktree, branch, number):
        _git(worktree, "fetch", "-q", "origin")
        _git(worktree, "merge", "-q", "--no-edit", "origin/sprint/s1")
        got = super().land(worktree, branch, number)
        name = self.name_of(branch)
        self.order.append(name)
        return got


@pytest.fixture
def parallel_world(tmp_path, monkeypatch) -> Parallel:
    w = Parallel(tmp_path)
    monkeypatch.setattr(git.Git, "remote_repo", lambda self, remote=None: "o/r")

    class Hub:
        def __init__(self, repo, api) -> None:
            pass

        def nodes(self, numbers, node, fields, batch="github.graphql-batch"):
            body = "## 受け入れ条件\n\n- [ ] 1. a\n- [ ] 2. b\n"
            return {n: {"body": body} for n in numbers}

    monkeypatch.setattr(github, "GitHub", Hub)
    monkeypatch.setattr(github, "client", lambda *a, **k: None)
    monkeypatch.setattr(github_pulls, "merged_pulls", w.lookup)
    monkeypatch.setattr(issue_values, "issue_text", lambda location, n, repo=None: (f"#{n}", f"#{n}"))
    monkeypatch.setattr(named_cap, "read", lambda environ, state_dir: named_cap.Cap(6, "env"))
    return w


def test_independent_units_run_at_the_same_time_and_the_sprint_pr_follows_the_last_merge(parallel_world):
    w = parallel_world
    # 並びで先の甲は、後の乙のマージより後に終わる
    got = w.run()

    assert got is not None and got.status == "ok", got.report
    assert w.fake_pool.limit == 6 and w.fake_pool.widest == 2
    assert w.order == ["乙", "甲", "issue-4"]
    # 結果の項目は、終わった順でなくマージの順の並びで返す
    assert [i["name"] for i in got.report["items"]] == ["甲", "乙", "issue-4"]
    assert w.runs[-1][0].name == "pr"
    assert _git(w.sprint, "rev-parse", "HEAD") == _git(w.sprint, "rev-parse", "origin/sprint/s1")


@pytest.mark.parametrize(("behind", "starts"), [(True, [None, "test"]), (False, [None])])
def test_only_a_unit_refused_as_behind_the_sprint_branch_is_started_again(parallel_world, behind, starts):
    w = parallel_world
    original = w.command

    def up_to_date_required(argv, cwd, env):
        if "merge" in argv and "ci" in argv:
            _git(cwd, "fetch", "-q", "origin")
            if _git(cwd, "rev-list", "--count", "HEAD..origin/sprint/s1") != "0":
                refused = result.stopped("ci", "not_ci", "断った", metrics={"behind": True} if behind else {})
                return subprocess.CompletedProcess(argv, 1, json.dumps(refused) + "\n", "")
        return original(argv, cwd, env)

    w.command = up_to_date_required

    got = w.run()

    # 始め直した単位は、スプリントブランチのマージで HEAD が変わるので、手元のテストから流し直す
    again = [r for r, _ in w.runs if r.name == "impl" and w.name_of(r.overrides["branch"]) == "甲"]
    assert [r.start for r in again] == starts
    assert got.status == ("ok" if behind else "stopped"), got.report
    assert w.order == (["乙", "甲", "issue-4"] if behind else ["乙", "issue-4"])


def test_the_limit_of_one_runs_the_units_one_by_one(parallel_world, monkeypatch):
    monkeypatch.setattr(named_cap, "read", lambda environ, state_dir: named_cap.Cap(1, "env"))
    w = parallel_world

    assert w.run().status == "ok"

    assert w.order == ["甲", "乙", "issue-4"] and w.fake_pool.widest == 1


def test_a_broken_limit_stops_before_any_unit(parallel_world, monkeypatch):
    def broken(environ, state_dir):
        raise named_cap.NamedCapError("環境変数 NDF_RELAY_MAX_NAMED の上限 '0' が 1 以上の整数でない")

    monkeypatch.setattr(named_cap, "read", broken)

    with pytest.raises(UnitsStopped) as raised:
        parallel_world.run()

    assert raised.value.kind == "invalid" and "NDF_RELAY_MAX_NAMED" in raised.value.detail
    assert parallel_world.runs == []


def test_a_failed_unit_does_not_stop_the_independent_unit(parallel_world):
    w = parallel_world
    w.fail["甲"] = result.stopped("plan", "test_failed", "落ちた")

    got = w.run()

    assert got.status == "stopped" and got.cause["kind"] == "test_failed"
    assert "単位 甲" in got.cause["detail"]
    assert [(i["name"], i["status"]) for i in got.report["items"]] == [("甲", "stopped"), ("乙", "merged"), ("issue-4", "merged")]
    assert all(r.name != "pr" for r, _ in w.runs)


def test_a_failure_the_caller_classifies_stops_only_that_unit(parallel_world):
    w = parallel_world
    original = w.call_plan

    def broken(run, round_dir):
        if run.name == "impl" and w.name_of(run.overrides["branch"]) == "甲":
            raise OSError("書けない")
        return original(run, round_dir)

    ports = w.ports()._replace(call_plan=broken, failure=lambda exc: result.stopped("plan", "precondition", f"分類: {exc}"))

    got = units_invoke.run_stage(w.stage_run(), w.work, w.sprint, ports)

    assert isinstance(got, StageResult) and got.cause["kind"] == "precondition" and "分類: 書けない" in got.cause["detail"]
    assert w.order == ["乙", "issue-4"]


def test_invoke_classifies_an_error_of_one_unit_with_the_plan_failures_and_flows_the_others(parallel_world, monkeypatch):
    w = parallel_world
    original = units_invoke.run_stage

    def broken(run, round_dir):
        if run.name == "impl" and w.name_of(run.overrides["branch"]) == "甲":
            raise OSError("書けない")
        return w.call_plan(run, round_dir)

    def run_stage(r, wd, cwd, ports):
        return original(r, wd, cwd, ports._replace(call_plan=broken, workers=w.fake_pool.workers()))

    monkeypatch.setattr(units_invoke, "run_stage", run_stage)

    got = invoke.invoke(w.stage_run(), w.sprint, w.work, runner=w.command)

    assert got.status == "stopped" and got.cause["kind"] == "precondition" and "書けない" in got.cause["detail"]
    assert w.order == ["乙", "issue-4"]
