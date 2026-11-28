"""レビューの単位を依存の順に並列で流す順番（#3702 の決定 9）。

単位 1 つ分の操作（作業場所・`impl`・マージ）は呼び手（`units_invoke`）が渡し、ここは「いつ始め、いつマージし、
失敗でどれを始めないか」と、止まった結果の組み立てを持つ。起動とマージは呼んだスレッドで 1 つずつ行い、`impl` の
プランだけをプールで同時に流す。プールと待ちは引数で受ける（テストは終わる順を決める偽を渡す）。

- 単位は、先に要る単位をすべてマージして作業場所へ反映したら始める。依存していない単位の終了は待たない
- 同時に分かったマージは、まとめて 1 回で反映する。反映できないマージは反映待ちにし、次の問い合わせの時刻に反映し直す
- PR がマージキューで待つ単位（`Queued`）があれば、実行中の単位を待ちながら次の問い合わせの時刻にだけ状態を読む。
  待ちの状態と記録、時刻と上限は `units_queue` が持つ
- マージがスプリントブランチの先行で断られた単位（`Outdated`）は、始め直して最新化・テスト・レビューを通してから再送する
- 失敗で始めないのは、その単位に依存する単位（間接を含む）だけである。利用上限（`usage_limit`）の後は何も始めず、
  実行中を待って成功した単位をマージする。`impl` が利用上限で終わっていれば、その結果を受ける前でも始めない
- 単位のファイルから流すとき、補った単位（`Unit.supplemented`。触るファイルが分からない）は、単位のファイルの単位に
  実行中も起動できるものも無くなった後に、課題の番号の順に 1 つずつ流す。キューか反映の待ちがある間と、PR がキューに
  残りうる停止がある間は始めない（その PR が後からマージされると競合しうる）
- 実装計画の表から読んだ並び（依存を持たない）は、並びの順に 1 つずつ流す

打ち直しで単位の回をどう続けるかの判定（`round_state`・`resume_of`）と、回の記録の読み書きもここが持つ。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Collection, Mapping, Sequence
from concurrent import futures
from graphlib import TopologicalSorter
from pathlib import Path
from types import MappingProxyType
from typing import Any, NamedTuple, Optional

from ndf.adapters import github, github_merge, github_pulls
from ndf.contracts import result, worker_paths
from ndf.core import route
from ndf.core.plan import runner as plan_runner
from ndf.core.plan import units_files
from ndf.core.plan.review_units_decide import Unit, unit_key
from ndf.core.plan.transition_decide import USAGE_LIMIT
from ndf.progress import plan_runs, units_queue
from ndf.progress.plan_runs import StageResult, UnitsStopped

# PR がキューに残りうる停止理由（待てば進むので、ほかの理由の失敗があればそちらを結果の種類にする。前ほど優先する）
WAIT_KINDS = ("infra_wait", github_merge.MERGE_QUEUE_PENDING)


# 単位の回の保存先のファイル（`reviewed-head` は `impl` のプラン、`stopped-head` と `merge.json` は単位を流す処理が書く）
REVIEWED_HEAD_FILE = units_files.REVIEWED_HEAD
STOPPED_HEAD_FILE = "stopped-head"
MERGE_FILE = "merge.json"
# 打ち直しの判定の、レビューのステップ（ほかのステップと停止理由の語は `resume_of` が持つ。定数にすると同じ語の別の事実と重なる）
REVIEW_STEPS = frozenset({"review", "review-delta", "resolved"})


class RoundState(NamedTuple):
    """単位の、プランの実行の記録（`run.json`）を持つ最新の回。`ran` が偽なら回が無く、`round_dir` は 1 回目の保存先。

    `done` はその回に保存した結果（終わっていなければ None）、`merge` は `merge.json` の結果の種類、`reviewed` は
    `reviewed-head`、`stopped_head` は `stopped-head` の値（無ければどれも None）。
    """

    round_dir: Path
    ran: bool
    done: Optional[dict[str, Any]]
    merge: Optional[str]
    reviewed: Optional[str]
    stopped_head: Optional[str]


class Resume(NamedTuple):
    """打ち直しの扱い。`action` は `run`（impl を流す）か `merge`（マージだけを打ち直す）、`new_round` は次の回の保存先を使うか、
    `start` は新しい回の `--from`（None なら最初からか、プランの打ち直しに任せる）。"""

    action: str
    new_round: bool
    start: Optional[str]


class Outdated(UnitsStopped):
    """単位の HEAD がスプリントブランチの HEAD を含まず、マージを断られた停止。単位を始め直せば進む。"""


class Queued(UnitsStopped):
    """単位の PR がマージキューの待ちになった停止。`ticket` は上げる呼び手が付ける。"""

    ticket: units_queue.Ticket


class Unconfirmed(UnitsStopped):
    """打ち直した `ndf ci merge` の後に、マージできたと確かめられない停止。PR はキューに残りうる。"""


class Started(NamedTuple):
    """始めた単位。`call` はプールで流す `impl`（マージだけを打ち直す単位は None）、`land` はその結果を受けてマージし、
    結果の項目とマージした PR を返す（呼んだスレッドで打つ。止めるときは `UnitsStopped`、始め直すときは `Outdated`、
    キューの待ちは `Queued`）。"""

    call: Optional[Callable[[], Any]]
    land: Callable[[Any], tuple[dict[str, Any], github_pulls.MergedPull]]


class Flowed(NamedTuple):
    """流し終えた並び。`items` はマージした単位（単位のキー → 結果の項目）、`pulls` はマージした順の PR、`failures` は
    止まった単位と理由（並びの順）、`waiting` は始めなかった単位と理由（並びの順）、`broken` はマージを作業場所へ反映する
    処理が止めた停止（無ければ None）。"""

    items: dict[str, dict[str, Any]]
    pulls: list[github_pulls.MergedPull]
    failures: tuple[tuple[Unit, UnitsStopped], ...]
    waiting: tuple[tuple[Unit, str], ...]
    broken: Optional[UnitsStopped] = None


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
        self.outside: set[str] = set()  # 前の起動でマージ済みで、始めない単位
        self.taken: set[str] = set()  # 作業場所へ反映できた単位
        self.returned: set[str] = set()
        self.closed: set[str] = set()

    def hold(self, keys: Collection[str]) -> None:
        """前の起動でマージ済みの単位。始めず、作業場所へ反映できた（`finish`）後に後の単位を始める。"""
        self.outside.update(keys)
        self.started.update(keys)

    def again(self, key: str) -> None:
        """始めた単位を、空きができれば始め直す単位に戻す。"""
        self.started.discard(key)
        self.ready.add(key)

    def finish(self, key: str) -> None:
        """マージを作業場所へ反映できた単位。"""
        self.taken.add(key)

    def _release(self) -> None:
        """反映できた単位をグラフで終わりにし、始められるようになった単位を取り出す。終わりにできるのは、グラフが取り出した単位だけである。"""
        while True:
            fresh = self.sorter.get_ready()
            self.ready.update(fresh)
            self.returned.update(fresh)
            finished = (self.taken & self.returned) - self.closed
            if not finished:
                return
            self.closed |= finished
            self.ready -= finished
            self.sorter.done(*finished)

    def next(self, running: Sequence[str], held: bool) -> Optional[str]:
        """次に始める単位。補った単位は、ほかに実行中も起動できる単位も無いときに 1 つだけ返し、`held` の間は返さない。"""
        self._release()
        if any(k in self.late for k in running):
            return None
        startable = self.ready - self.outside
        plain = startable - self.late
        if plain:
            found = min(plain, key=self.position.__getitem__)
        elif running or held or not startable:
            return None
        else:
            found = min(startable, key=lambda k: (self.units[k].issue, self.position[k]))
        self.ready.remove(found)
        self.started.add(found)
        return found

    def waiting(self, failed: Sequence[str], halted: str) -> tuple[tuple[Unit, str], ...]:
        """始めなかった単位と理由。`halted` は何も始めなくなった理由（無ければ空）。"""
        out = []
        for key, unit in self.units.items():
            if key in self.started:
                continue
            blocking = next((a for a in self.after[key] if a in failed), None)
            unstarted = next((a for a in self.after[key] if a not in self.started), None)
            if halted:
                why = halted
            elif blocking is not None:
                why = f"先に要る単位 {self.units[blocking].name} が止まった"
            elif unstarted is not None:
                why = f"先に要る単位 {self.units[unstarted].name} が始まらない"
            else:
                why = "キューに残りうる PR か反映できていないマージがあるので、触るファイルが分からない単位を始めない"
            out.append((unit, why))
        return tuple(out)


class Ops(NamedTuple):
    """単位 1 つの操作。`start` は単位を始め、`failed` は単位の例外を停止にする（分けられない例外は上げ直す）。`limited` は
    `impl` の結果が利用上限の停止かを、結果を受ける（マージする）前に読む。`watching` は待ちの扱いの接点。"""

    start: Callable[[Unit], Started]
    failed: Callable[[Unit, Exception], UnitsStopped]
    limited: Callable[[Any], bool]
    watching: units_queue.Watching = units_queue.Watching()


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
        self.board = units_queue.Board(ops.watching)
        self.fresh: dict[str, github_pulls.MergedPull] = {}  # マージが分かり、まだ作業場所へ反映していない単位
        self.resent: set[str] = set()  # この起動で `ndf ci merge` を打ち直した単位
        self.stuck: set[str] = set()  # 打ち直しの後にマージを確かめられずに止まった単位（PR がキューに残りうる）
        self.broken: Optional[UnitsStopped] = None

    def halted(self) -> bool:
        """利用上限は全単位に効くので、以後の単位を始めない。終わってまだ受けていない `impl` の停止理由も読む。
        作業場所への反映が止めた後も始めない。"""
        if self.broken is not None or any(e.kind == USAGE_LIMIT for e in self.failures.values()):
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
            held = bool(self.fresh or self.stuck) or self.board.waiting() or any(e.kind in WAIT_KINDS for e in self.failures.values())
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
        self.settle(key, lambda: started.land(None if future is None else future.result()))

    def settle(self, key: str, merge: Callable[[], tuple[dict[str, Any], github_pulls.MergedPull]]) -> None:
        """`merge` の結果を受ける。`Outdated` の単位は、空きの本数の中で始め直す（利用上限の後は始めずに失敗にする）。

        始め直しは、単位の HEAD を最新にしても別のマージが先に入ったときだけ繰り返すので、回数はマージの数を超えない。
        """
        try:
            item, pull = merge()
        except Outdated as exc:
            if self.halted():
                self.failures[key] = exc
            else:
                self.order.again(key)
            return
        except Queued as exc:
            if exc.ticket.resent_now:
                self.resent.add(key)
            if not self.board.add_queued(key, exc.ticket):
                self._expired_in_queue(key, exc.ticket.number)
            return
        except Exception as exc:  # noqa: BLE001 外部コマンド: 単位の失敗にして依存しない単位を流す（分けられない例外は上げ直す）
            if isinstance(exc, Unconfirmed):
                self.stuck.add(key)
            self.failures[key] = self.ops.failed(self.order.units[key], exc)
            return
        self.items[key] = item
        self.pulls.append(pull)
        self.fresh[key] = pull

    def step(self, executor: Any, limit: int, workers: Workers) -> bool:
        """実行中の単位の終わりか次の問い合わせの時刻まで待ち、分かった結果を受ける。待つものが無ければ偽。"""
        waiting = self.broken is None and self.board.waiting()
        if self.running:
            timeout = self.board.until() if waiting else None
            done, _ = workers.wait(list(self.running), timeout=timeout, return_when=futures.FIRST_COMPLETED)
            for future in sorted(done, key=lambda f: self.order.position[self.running[f][0]]):
                self.land(*self.running.pop(future), future)
                self.fill(executor, limit)
        elif waiting:
            self.ops.watching.sleep(self.board.until())
        else:
            return False
        self.take_in()
        self.poll()
        return True

    def take_in(self, retry: bool = False) -> bool:
        """分かったマージ（`retry` なら反映待ちも）を 1 回でまとめて作業場所へ反映する。反映を試したら真。"""
        batch = {**(self.board.ingest if retry else {}), **self.fresh}
        self.fresh.clear()
        if not batch or self.broken is not None:
            return False
        try:
            taken = set(self.ops.watching.advance(batch))
        except UnitsStopped as exc:
            self.broken = exc
            return True
        for key, pull in batch.items():
            if key in taken:
                self.board.taken(key)
                self.order.finish(key)
            elif not self.board.add_ingest(key, pull):
                self._expired_in_ingest(key, pull)
        return True

    def poll(self) -> None:
        """上限を超えた待ちを外してから、次の問い合わせの時刻に達していれば、残る PR の状態をまとめて読み、反映待ちを反映し直す。

        上限が来ただけのときは、問い合わせずに超えた待ちだけを外し、次の問い合わせの時刻を変えない。
        """
        if self.broken is not None or not self.board.due():
            return
        for key in [k for k in self.board.queued if self.board.expired(k)]:
            self._expired_in_queue(key, self.board.queued.pop(key).number)
        for key in [k for k in self.board.ingest if self.board.expired(k)]:
            self._expired_in_ingest(key, self.board.ingest.pop(key))
        if not self.board.read_due():
            self.board.rest()
            return
        states = self.board.read()
        for key in sorted(states, key=self.order.position.__getitem__):
            self._apply(key, states[key])
        self.take_in(retry=True)
        self.board.schedule(states)

    def _apply(self, key: str, state: units_queue.State) -> None:
        """読んだ状態の次の手を当てる。"""
        ticket = self.board.queued[key]
        record = units_queue.held_record(ticket.record, ticket.number)
        act = units_queue.action(state, record is not None and bool(record["resent"]))
        if act == units_queue.WAIT:
            return
        del self.board.queued[key]
        if act == units_queue.DONE:
            merged = unit_item(self.order.units[key], ticket.number, "merged"), github_pulls.MergedPull(ticket.number, state.merge_sha)
            self.settle(key, lambda: merged)
        elif act == units_queue.FAIL:
            self.failures[key] = UnitsStopped("precondition", dropped(ticket.number, state))
            if state.kind == units_queue.OPEN and key in self.resent:
                self.stuck.add(key)
        elif self.board.expired(key):
            # 先の単位の打ち直しの間に上限を超えた PR は、打ち直さない
            self._expired_in_queue(key, ticket.number)
        else:
            units_queue.mark_resent(ticket.record, ticket.number, self.ops.watching.clock())
            self.resent.add(key)
            self.settle(key, ticket.resend)
            if key in self.failures:
                self.stuck.add(key)

    def _expired_in_queue(self, key: str, number: int) -> None:
        """待ちの上限を超えた PR を失敗にする。GitHub の利用枠が戻る時刻が上限より後なら、基盤の待ちにする。"""
        detail = f"PR #{number} がマージキューの待ちの上限（{self.ops.watching.budget:g} 秒）を超えた"
        wait = self.board.resets_after_deadline(key)
        kind = github_merge.MERGE_QUEUE_PENDING if wait is None else "infra_wait"
        self.failures[key] = UnitsStopped(kind, detail, retry_after=wait)

    def _expired_in_ingest(self, key: str, pull: github_pulls.MergedPull) -> None:
        """反映を待つ上限を超えたマージを失敗にする。"""
        detail = f"PR #{pull.number} のマージ {pull.merge_sha} を、上限の時間内にスプリントの作業場所へ反映できない"
        self.failures[key] = UnitsStopped(github_merge.MERGE_QUEUE_PENDING, detail)

    def result(self) -> Flowed:
        for key, ticket in self.board.queued.items():
            detail = f"PR #{ticket.number} はマージキューの待ち"
            self.failures[key] = UnitsStopped(github_merge.MERGE_QUEUE_PENDING, detail)
        stopped_units = tuple((u, self.failures[k]) for k, u in self.order.units.items() if k in self.failures)
        halted = "利用上限で新しい単位を始めない" if self.halted() else ""
        if self.broken is not None:
            halted = "スプリントの作業場所へマージを反映できないので始めない"
        return Flowed(self.items, self.pulls, stopped_units, self.order.waiting(list(self.failures), halted), self.broken)


def dropped(number: int, state: units_queue.State) -> str:
    """キューの待ちをやめて失敗にする理由。"""
    if state.kind == units_queue.CLOSED:
        return f"PR #{number} がマージされずに閉じられた"
    if state.kind == units_queue.ELSEWHERE:
        return f"PR #{number} がスプリントブランチでないブランチへマージされた"
    return f"PR #{number} は打ち直した後もマージキューから外れた"


def remerge(
    unit: Unit,
    ticket: units_queue.Ticket,
    read: Callable[[Sequence[int]], Mapping[int, units_queue.State]],
    clock: Callable[[], float],
) -> tuple[dict[str, Any], github_pulls.MergedPull]:
    """マージだけを打ち直す単位。先に PR の状態を読み、`ndf ci merge` は開いたままキューに無い PR にだけ打つ（決定 9）。

    一度キューの待ちになった PR への打ち直しは 1 回だけで、打つ前に記録する。
    """
    number = ticket.number
    state = read([number])[number]
    if state.kind == units_queue.MERGED:
        return unit_item(unit, number, "merged"), github_pulls.MergedPull(number, state.merge_sha)
    record = units_queue.held_record(ticket.record, number)
    act = units_queue.action(state, record is not None and bool(record["resent"]))
    if act == units_queue.WAIT:
        waiting = Queued(github_merge.MERGE_QUEUE_PENDING, f"PR #{number} はマージキューの待ち")
        waiting.ticket = ticket._replace(retry_after=state.retry_after)
        raise waiting
    if act == units_queue.FAIL:
        raise UnitsStopped("precondition", dropped(number, state))
    if record is None:
        return ticket.resend()
    units_queue.mark_resent(ticket.record, number, clock())
    try:
        return ticket.resend()
    except Queued as exc:
        exc.ticket = exc.ticket._replace(resent_now=True)
        raise
    except Outdated:
        raise  # 断られた PR はキューに残らず、単位を始め直せば進む
    except UnitsStopped as exc:
        raise Unconfirmed(exc.kind, exc.detail, retry_after=exc.retry_after) from exc


def flow(
    order: Order, ops: Ops, limit: int, workers: Workers = Workers(), landed: Mapping[str, github_pulls.MergedPull] = MappingProxyType({})
) -> Flowed:
    """`order` の単位を依存の順に、同時に `limit` 本まで流す。`landed` は前の起動でマージ済みで、作業場所へ反映する単位。

    同時に終わった単位は、並び（マージの順）の順にマージし、マージのたびに空きを埋める。`ops.failed` が例外を上げ直すときも、プールは実行中の単位の
    終わりを待つ。実行中が無く待ちだけが残れば、次の問い合わせの時刻まで眠る。
    """
    state = _Flow(order, ops)
    state.fresh.update(landed)
    order.hold(state.fresh)
    with workers.pool(max_workers=limit) as executor:
        more = True
        while more:
            state.fill(executor, limit)
            more = state.take_in() or state.step(executor, limit, workers)
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
    main = flowed.broken or primary(flowed.failures)
    rows = dict(items)
    sentences = []
    for unit, exc in sorted(flowed.failures, key=lambda f: f[1] is not main):
        pr = pr_of(unit)
        where = f"単位 {unit.name}（課題 #{unit.issue}{f'、PR #{pr}' if pr is not None else ''}）"
        sentences.append(f"{where}で止まった: {exc.detail}")
        rows[key_of(unit)] = {**unit_item(unit, pr, "stopped"), "reason": exc.detail}
    if flowed.waiting:
        sentences.append("始めていない単位: " + "、".join(f"{u.name}（{why}）" for u, why in flowed.waiting))
    if flowed.broken is not None:
        # 作業場所の反映の停止は、利用者が打つコマンドで終わる理由を持つので、末尾に置く
        sentences.append(flowed.broken.detail)
    report = result.stopped(
        plan_runner.TOOL,
        main.kind,
        "。".join(sentences),
        retry_after=main.retry_after,
        items=[rows[key_of(u)] for u in units if key_of(u) in rows],
        metrics=metrics,
    )
    return StageResult(report, {})


def merged_pulls_of(
    hub: github.GitHub, base: str, landed: Sequence[tuple[str, github_pulls.MergedPull]], tip: str
) -> list[github_pulls.MergedPull]:
    """単位のブランチと PR の組の、マージコミットを持つ PR の並び（スプリントの作業場所を進める先の候補）。

    開始時の一覧かマージの応答がマージコミットを持てば、それを使う。持たないものだけ GitHub へまとめて読み直し、持っているものの 1 つが
    スプリントブランチのリモートの HEAD（`tip`）なら、ほかの候補は要らないので読み直さない。読み直しの失敗は、
    打ち直せば通りうるもの（不達・5xx・上限）を transient、ほかを precondition にし、待つ秒を持たせたまま単位の停止にする。
    """
    lacking = [branch for branch, pull in landed if not pull.merge_sha]
    if not lacking or any(pull.merge_sha == tip for _, pull in landed):
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


# --- 打ち直しの判定 -----------------------------------------------------------------------------


def _round_number(path: Path) -> Optional[int]:
    return int(path.name) if path.is_dir() and path.name.isdigit() else None


def saved_head(path: Path) -> Optional[str]:
    return path.read_text(encoding="utf-8").strip() if path.is_file() else None


def _own_records(round_dir: Path) -> tuple[Optional[str], Optional[str]]:
    """この処理が書いた `merge.json` の結果の種類と `stopped-head` の値。呼んだ利用者が持ち、ほかが書き込めないものだけを読む。"""
    with worker_paths.open_or_create(round_dir) as wd:
        merged, stopped = (worker_paths.read_file(wd, n, owned=True) for n in (MERGE_FILE, STOPPED_HEAD_FILE))
    try:
        kind = json.loads(merged).get("kind") if merged is not None else None
    except (ValueError, AttributeError):
        kind = None
    return (kind if isinstance(kind, str) else None), (stopped.strip() if stopped is not None else None)


def write_round_record(round_dir: Path, name: str, text: str) -> None:
    """回の保存先へ記録を書く。既存のファイルを開いて切り詰めず、ほかの利用者が名前を差し替えられない保存先にだけ書く。"""
    with worker_paths.open_or_create(round_dir) as wd:
        worker_paths.check_unshared(wd)
        worker_paths.write_file(wd, name, text, private=True)


def round_state(unit_dir: Path) -> RoundState:
    """`run.json` を持つ最新の回の、結果・マージの結果・`reviewed-head`・`stopped-head`。記録の無い空の回は数えない。"""
    latest = max(
        (
            (n, p)
            for p in (unit_dir.iterdir() if unit_dir.is_dir() else [])
            if (n := _round_number(p)) is not None and (p / plan_runner.RUN_FILE).is_file()
        ),
        default=None,
    )
    if latest is None:
        return RoundState(round_dir=unit_dir / "1", ran=False, done=None, merge=None, reviewed=None, stopped_head=None)
    path = latest[1]
    _, done = plan_runs.previous(path)
    kind, stopped = _own_records(path)
    return RoundState(path, True, done, kind, saved_head(path / REVIEWED_HEAD_FILE), stopped)


def stopped_step(done: Optional[Mapping[str, Any]]) -> Optional[str]:
    """止まった回の、結果の `items` の先頭（`kind` が `plan`）の `step`。止まっていないか無ければ None。"""
    if done is None or done.get("status") != result.STOPPED:
        return None
    items = done.get("items")
    first = items[0] if isinstance(items, list) and items and isinstance(items[0], Mapping) else {}
    step = first.get("step") if first.get("kind") == plan_runner.TOOL else None
    return step if isinstance(step, str) else None


def resume_of(state: RoundState, head: str, local_tests: bool) -> Resume:
    """打ち直しの扱い（docs/specifications/ndf-implement-stage-review-units.md の「打ち直し」の表。上の行を先に当てる）。`head` は、スプリントブランチをマージした後の単位の作業場所の HEAD。

    マージだけを打ち直すのは、マージが CI の待ち（マージキューの待ちを含む）か基盤の待ちで止まった回と、`impl` の成功の後にマージの結果を
    残す前に中断した回である。HEAD が変わった単位を手元のテストから流し直すのは、テストより後のステップで止まった回で、作業・受け入れ条件・テスト・修正で止まった回は含めない。
    """
    # None は `impl` の成功の後、マージの結果を残す前の中断
    merge_only = {None, *WAIT_KINDS}
    if not state.ran:
        return Resume("run", False, None)
    finished = state.done is not None and state.done.get("status") == result.OK
    if finished and state.merge in merge_only and state.reviewed == head:
        return Resume("merge", False, None)
    step = stopped_step(state.done)
    moved = state.stopped_head != head
    retest = "test" if local_tests else "push"
    if finished or step in REVIEW_STEPS:
        return Resume("run", True, retest if moved else "push")
    if step is not None and route.stopped_after_test(step) and moved:
        return Resume("run", True, retest)
    return Resume("run", False, None)
