"""レビューの単位を依存の順に並列で流す順番（#3702 の決定 9）。

単位 1 つ分の操作（作業場所・`impl`・マージ）は呼び手（`units_invoke`）が渡し、ここは「いつ始め、いつマージし、
失敗でどれを始めないか」と、止まった結果の組み立てを持つ。起動とマージは呼んだスレッドで 1 つずつ行い、`impl` の
プランだけをプールで同時に流す。プールと待ちは引数で受ける（テストは終わる順を決める偽を渡す）。

- 単位は、先に要る単位をすべてマージしたら始める。依存していない単位の終了は待たない
- 失敗で始めないのは、その単位に依存する単位（間接を含む）だけである。利用上限（`usage_limit`）の後は何も始めず、
  実行中を待って成功した単位をマージする。`impl` が利用上限で終わっていれば、その結果を受ける前でも始めない
- 単位のファイルから流すとき、補った単位（`Unit.supplemented`。触るファイルが分からない）は、単位のファイルの単位に
  実行中も起動できるものも無くなった後に、課題の番号の順に 1 つずつ流す。キューの待ちで止まった単位がある間は始めない
  （その PR が後からマージされると競合しうる）
- 実装計画の表から読んだ並び（依存を持たない）は、並びの順に 1 つずつ流す
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent import futures
from graphlib import TopologicalSorter
from typing import Any, NamedTuple, Optional

from ndf.adapters import github, github_merge, github_pulls
from ndf.contracts import result
from ndf.core.plan import runner as plan_runner
from ndf.core.plan.review_units_decide import Unit, unit_key
from ndf.core.plan.transition_decide import USAGE_LIMIT
from ndf.progress.plan_runs import StageResult, UnitsStopped

# PR がキューに残りうる停止理由（待てば進むので、ほかの理由の失敗があればそちらを結果の種類にする。前ほど優先する）
WAIT_KINDS = ("infra_wait", github_merge.MERGE_QUEUE_PENDING)


class Started(NamedTuple):
    """始めた単位。`call` はプールで流す `impl`（マージだけを打ち直す単位は None）、`land` はその結果を受けてマージし、
    結果の項目とマージした PR を返す（呼んだスレッドで打つ。止めるときは `UnitsStopped`）。"""

    call: Optional[Callable[[], Any]]
    land: Callable[[Any], tuple[dict[str, Any], github_pulls.MergedPull]]


class Flowed(NamedTuple):
    """流し終えた並び。`items` はマージした単位（単位のキー → 結果の項目）、`pulls` はマージした順の PR、`failures` は
    止まった単位と理由（並びの順）、`waiting` は始めなかった単位と理由（並びの順）。"""

    items: dict[str, dict[str, Any]]
    pulls: list[github_pulls.MergedPull]
    failures: tuple[tuple[Unit, UnitsStopped], ...]
    waiting: tuple[tuple[Unit, str], ...]


def key_of(unit: Unit) -> str:
    return unit_key(unit.issue, unit.name)


def unit_item(unit: Unit, pr: Optional[int], status: str) -> dict[str, Any]:
    return {"kind": "unit", "name": unit.name, "issue": unit.issue, "pr": pr, "status": status}


class Order:
    """始められる単位を選ぶ状態。`units` はマージの順の並び、`merged` はマージ済みのキー、`chained` は表の順に 1 つずつ流すか。"""

    def __init__(self, units: Sequence[Unit], merged: frozenset[str], chained: bool) -> None:
        left = [u for u in units if key_of(u) not in merged]
        self.units = {key_of(u): u for u in left}
        self.position = {k: i for i, k in enumerate(self.units)}
        if chained:
            keys = list(self.units)
            self.after = {k: (keys[i - 1],) if i else () for i, k in enumerate(keys)}
        else:
            self.after = {k: tuple(a for a in u.after if a in self.units) for k, u in self.units.items()}
        self.late = frozenset() if chained else frozenset(k for k, u in self.units.items() if u.supplemented)
        self.sorter: TopologicalSorter[str] = TopologicalSorter(self.after)
        self.sorter.prepare()
        self.ready: set[str] = set()
        self.started: set[str] = set()

    def next(self, running: Sequence[str], held: bool) -> Optional[str]:
        """次に始める単位。補った単位は、ほかに実行中も起動できる単位も無いときに 1 つだけ返し、`held` の間は返さない。"""
        self.ready.update(self.sorter.get_ready())
        if any(k in self.late for k in running):
            return None
        plain = self.ready - self.late
        if plain:
            found = min(plain, key=self.position.__getitem__)
        elif running or held or not self.ready:
            return None
        else:
            found = min(self.ready, key=lambda k: (self.units[k].issue, self.position[k]))
        self.ready.remove(found)
        self.started.add(found)
        return found

    def waiting(self, failed: Sequence[str], halted: bool) -> tuple[tuple[Unit, str], ...]:
        """始めなかった単位と理由。"""
        out = []
        for key, unit in self.units.items():
            if key in self.started:
                continue
            blocking = next((a for a in self.after[key] if a in failed), None)
            unstarted = next((a for a in self.after[key] if a not in self.started), None)
            if halted:
                why = "利用上限で新しい単位を始めない"
            elif blocking is not None:
                why = f"先に要る単位 {self.units[blocking].name} が止まった"
            elif unstarted is not None:
                why = f"先に要る単位 {self.units[unstarted].name} が始まらない"
            else:
                why = "キューに残りうる PR があるので、触るファイルが分からない単位を始めない"
            out.append((unit, why))
        return tuple(out)


class Ops(NamedTuple):
    """単位 1 つの操作。`start` は単位を始め、`failed` は単位の例外を停止にする（分けられない例外は上げ直す）。`limited` は
    `impl` の結果が利用上限の停止かを、結果を受ける（マージする）前に読む。"""

    start: Callable[[Unit], Started]
    failed: Callable[[Unit, Exception], UnitsStopped]
    limited: Callable[[Any], bool]


class Workers(NamedTuple):
    """`impl` を流すプールと、終わりの待ち。"""

    pool: Callable[..., Any] = futures.ThreadPoolExecutor
    wait: Callable[..., Any] = futures.wait


class _Flow:
    """流している間の状態。"""

    def __init__(self, order: Order, ops: Ops) -> None:
        self.order = order
        self.ops = ops
        self.items: dict[str, dict[str, Any]] = {}
        self.pulls: list[github_pulls.MergedPull] = []
        self.failures: dict[str, UnitsStopped] = {}
        self.running: dict[Any, tuple[str, Started]] = {}

    def halted(self) -> bool:
        """利用上限は全単位に効くので、以後の単位を始めない。終わってまだ受けていない `impl` の停止理由も読む。"""
        if any(e.kind == USAGE_LIMIT for e in self.failures.values()):
            return True
        return any(f.done() and self._limited(f) for f in self.running)

    def _limited(self, future: Any) -> bool:
        error = future.exception()
        if error is not None:
            return isinstance(error, UnitsStopped) and error.kind == USAGE_LIMIT
        return self.ops.limited(future.result())

    def fill(self, executor: Any, limit: int) -> None:
        """空いた本数だけ単位を始める。"""
        while len(self.running) < limit and not self.halted():
            held = any(e.kind in WAIT_KINDS for e in self.failures.values())
            key = self.order.next([k for k, _ in self.running.values()], held)
            if key is None:
                return
            self._start(executor, key)

    def _start(self, executor: Any, key: str) -> None:
        try:
            started = self.ops.start(self.order.units[key])
        except Exception as exc:  # noqa: BLE001 外部コマンド: 単位の失敗にして依存しない単位を流す（分けられない例外は上げ直す）
            self.failures[key] = self.ops.failed(self.order.units[key], exc)
            return
        if self.halted():
            # 用意の間に利用上限が分かった単位は投入せず、始めていない単位に戻す
            self.order.started.discard(key)
        elif started.call is None:
            self.land(key, started, None)
        else:
            self.running[executor.submit(started.call)] = (key, started)

    def land(self, key: str, started: Started, future: Any) -> None:
        """終わった単位をマージする。"""
        try:
            item, pull = started.land(None if future is None else future.result())
        except Exception as exc:  # noqa: BLE001 外部コマンド: 単位の失敗にして依存しない単位を流す（分けられない例外は上げ直す）
            self.failures[key] = self.ops.failed(self.order.units[key], exc)
            return
        self.items[key] = item
        self.pulls.append(pull)
        self.order.sorter.done(key)

    def result(self) -> Flowed:
        stopped_units = tuple((u, self.failures[k]) for k, u in self.order.units.items() if k in self.failures)
        return Flowed(self.items, self.pulls, stopped_units, self.order.waiting(list(self.failures), self.halted()))


def flow(order: Order, ops: Ops, limit: int, workers: Workers = Workers()) -> Flowed:
    """`order` の単位を依存の順に、同時に `limit` 本まで流す。

    同時に終わった単位は、並び（マージの順）の順にマージする。`ops.failed` が例外を上げ直すときも、プールは実行中の単位の
    終わりを待つ。
    """
    state = _Flow(order, ops)
    with workers.pool(max_workers=limit) as executor:
        while True:
            state.fill(executor, limit)
            if not state.running:
                break
            done, _ = workers.wait(list(state.running), return_when=futures.FIRST_COMPLETED)
            for future in sorted(done, key=lambda f: order.position[state.running[f][0]]):
                state.land(*state.running.pop(future), future)
    return state.result()


def primary(failures: Sequence[tuple[Unit, UnitsStopped]]) -> UnitsStopped:
    """止まった工程の結果の種類を決める停止。利用上限、待ちでない最初の失敗、基盤の待ち、CI の待ちの順に選ぶ。"""
    found = next((e for _, e in failures if e.kind == USAGE_LIMIT), None)
    found = found or next((e for _, e in failures if e.kind not in WAIT_KINDS), None)
    if found is not None:
        return found
    return min((e for _, e in failures), key=lambda e: WAIT_KINDS.index(e.kind))


def stopped_stage(
    units: Sequence[Unit],
    flowed: Flowed,
    items: dict[str, dict[str, Any]],
    pr_of: Callable[[Unit], Optional[int]],
    metrics: dict[str, Any],
) -> StageResult:
    """単位で止まった結果。工程のステートマシンは `cause` だけを残すので、単位の名前と理由は説明の文に書く。

    `items` はマージ済みの単位の項目、`pr_of` は止まった単位の PR の番号を読む。
    """
    main = primary(flowed.failures)
    rows = dict(items)
    sentences = []
    for unit, exc in sorted(flowed.failures, key=lambda f: f[1] is not main):
        pr = pr_of(unit)
        where = f"単位 {unit.name}（課題 #{unit.issue}{f'、PR #{pr}' if pr is not None else ''}）"
        sentences.append(f"{where}で止まった: {exc.detail}")
        rows[key_of(unit)] = {**unit_item(unit, pr, "stopped"), "reason": exc.detail}
    if flowed.waiting:
        sentences.append("始めていない単位: " + "、".join(f"{u.name}（{why}）" for u, why in flowed.waiting))
    report = result.stopped(
        plan_runner.TOOL,
        main.kind,
        "。".join(sentences),
        retry_after=main.retry_after,
        items=[rows[key_of(u)] for u in units if key_of(u) in rows],
        metrics=metrics,
    )
    return StageResult(report, {})


def merged_pulls_of(hub: github.GitHub, base: str, landed: Sequence[tuple[str, github_pulls.MergedPull]]) -> list[github_pulls.MergedPull]:
    """単位のブランチと PR の組の、マージコミットを持つ PR の並び（スプリントの作業場所を進める先の候補）。

    開始時の一覧かマージの応答がマージコミットを持てば、それを使う。持たないものだけ GitHub へまとめて読み直す。読み直しの失敗は、
    打ち直せば通りうるもの（不達・5xx・上限）を transient、ほかを precondition にし、待つ秒を持たせたまま単位の停止にする。
    """
    lacking = [branch for branch, pull in landed if not pull.merge_sha]
    if not lacking:
        return [pull for _, pull in landed]
    where = f"単位のブランチ {'、'.join(lacking)} の、{base} へマージ済みの PR を GitHub から読めない"
    try:
        found = github_pulls.merged_pulls(hub, lacking, base)
    except github.GitHubError as exc:
        kind = "transient" if exc.passing else "precondition"
        raise UnitsStopped(kind, f"{where}: {exc}", retry_after=exc.retry_after) from exc
    if any(branch not in found for branch in lacking):
        raise UnitsStopped("precondition", where)
    return [pull if pull.merge_sha else found[branch] for branch, pull in landed]
