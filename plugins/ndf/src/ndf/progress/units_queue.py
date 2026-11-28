"""マージキューで待つ単位と、作業場所へ反映できていないマージの状態（#3702 の決定 9）。

GitHub の状態は GraphQL で読み、REST へは読み替えない（REST の応答はマージキューの状態を持たず、キューにある PR も
「キューに無い」と読める）。読んだ状態から次の手を選ぶ `action`、打ち直しをまたいで持つ記録（単位ごとの `queue.json` と
`units/ingest.json`）、次に問い合わせる時刻と待ちの上限（`Board`）をここが持つ。単位を流す順番は `units_flow` が持つ。
時刻は打ち直しをまたいで比べるので、壁時計の秒である（テストは時計と眠りを差し替える）。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Collection, Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf.adapters import github, github_pulls
from ndf.contracts import limits
from ndf.core.plan import files as plan_files

# 読んだ PR の状態の種類
MERGED, QUEUED, OPEN, CLOSED, UNKNOWN = "merged", "queued", "open", "closed", "unknown"
ELSEWHERE = "elsewhere"  # スプリントブランチでないブランチへマージされた
# 次の手
DONE, WAIT, RESEND, FAIL = "done", "wait", "resend", "fail"


class State(NamedTuple):
    """PR 1 つの状態。`merge_sha` はマージ済みのときのマージのコミット、`retry_after` は読めないときに GitHub が示した待ちの秒。"""

    kind: str
    merge_sha: str = ""
    retry_after: Optional[float] = None


class Ticket(NamedTuple):
    """キューで待つ単位の PR。`record` は単位の保存先の `queue.json`、`resend` は `ndf ci merge` を打ち直し、マージできれば
    結果の項目とマージした PR を返す（呼んだスレッドで打つ）。`resent_now` はこの起動で打ち直し済みか。`retry_after` は
    待ちに加える前の状態の読み取りで GitHub が示した待ちの秒。"""

    number: int
    record: Path
    resend: Callable[[], tuple[dict[str, Any], github_pulls.MergedPull]]
    resent_now: bool = False
    retry_after: Optional[float] = None


class Watching(NamedTuple):
    """待ちの扱いに使う外との接点。`read` は PR の番号ごとの状態を読み、`advance` はマージのコミットを持つ単位をスプリントの
    作業場所へ反映、反映できた単位のキーを返す。2 つ目の引数の単位は反映先にも反映の成否にも使わず、反映後の HEAD が含めば
    返すキーに加える。`ingest` は反映待ちの記録、`budget` は待ちの上限の秒。"""

    read: Callable[[Sequence[int]], Mapping[int, State]] = lambda numbers: {n: State(UNKNOWN) for n in numbers}
    advance: Callable[[Mapping[str, github_pulls.MergedPull], Mapping[str, github_pulls.MergedPull]], Collection[str]] = (
        lambda pulls, overdue: pulls.keys()
    )
    ingest: Optional[Path] = None
    budget: float = 3600.0
    clock: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep


def read(hub: github.GitHub, numbers: Sequence[int], base: str) -> dict[int, State]:
    """番号ごとの PR の状態を GraphQL でまとめて読む（100 件ごとに 1 回）。読めなければ、すべて `unknown` にする。

    `base` は PR のマージ先として期待するブランチで、ほかのブランチへのマージはマージ済みと扱わない。
    """
    try:
        pulls = hub.pulls(numbers, rest_fallback=False)
    except github.GitHubError as exc:
        return {n: State(UNKNOWN, retry_after=exc.retry_after) for n in numbers}
    return {n: _state_from_pull(pulls[n] if n in pulls else None, base) for n in numbers}


def _state_from_pull(pull: Optional[github_pulls.Pull], base: str) -> State:
    if pull is None:
        return State(UNKNOWN)
    if pull.merged:
        return State(MERGED, pull.merge_sha) if pull.base_ref == base else State(ELSEWHERE)
    if pull.queued:
        return State(QUEUED)
    return State(OPEN if pull.state == "open" else CLOSED)


def action(state: State, resent: bool) -> str:
    """状態から次の手を選ぶ（決定 9 の表）。開いたままキューに無い PR は、打ち直しを 1 回だけ行う。"""
    if state.kind == MERGED:
        return DONE
    if state.kind == OPEN:
        return FAIL if resent else RESEND
    return FAIL if state.kind in (CLOSED, ELSEWHERE) else WAIT


# --- 打ち直しをまたぐ記録 -------------------------------------------------------------------------


def queue_file(unit_dir: Path) -> Path:
    """単位の保存先の、キューの待ちの記録。"""
    return unit_dir / "queue.json"


def held_record(path: Path, number: int) -> Optional[dict[str, Any]]:
    """PR `number` の待ちの記録（`queue.json`）。無いか、別の PR のものなら None。"""
    held = plan_files.read_json(path)
    return held if held is not None and held.get("pr") == number else None


def queued_record(path: Path, number: int, now: float) -> dict[str, Any]:
    """PR の待ちの記録を返す。無ければ `now` を最初にキューの待ちになった時刻として書く（あれば書き換えない）。"""
    held = held_record(path, number)
    if held is None:
        held = {"pr": number, "resent": False, "queued_at": now}
        plan_files.write_json(path, held)
    return held


def mark_resent(path: Path, number: int, now: float) -> None:
    """打ち直したと書く。`ndf ci merge` を打つ前に呼び、打つ途中で中断しても回数が戻らないようにする。"""
    plan_files.write_json(path, {**queued_record(path, number, now), "resent": True})


class Board:
    """流している間の待ちの一覧と、次に問い合わせる時刻。

    `queued` は単位のキー → キューの待ち、`ingest` は単位のキー → 反映待ちの PR、`since` は単位のキー → 待ちの始まりの時刻、
    `retry_after` は最後に読めなかったときに GitHub が示した、待ちが解ける時刻。
    """

    def __init__(self, watching: Watching) -> None:
        self.watching = watching
        self.queued: dict[str, Ticket] = {}
        self.ingest: dict[str, github_pulls.MergedPull] = {}
        self.since: dict[str, float] = {}
        self.resumable: dict[str, float] = {}
        # 問い合わせの間隔は `ndf ci watch` と同じ方針（結果が変わらない間は延ばす）
        policy = limits.policy("test-triage.check-wait")
        self.first, self.factor, self.longest = float(policy.interval), float(policy.factor), float(policy.max_interval)
        self.interval = self.first
        self.next_at: Optional[float] = None
        self.previous: Optional[dict[str, State]] = None

    def waiting(self) -> bool:
        return bool(self.queued or self.ingest)

    def add_queued(self, key: str, ticket: Ticket) -> bool:
        """キューの待ちに加える。上限をすでに超えていれば加えずに偽を返す。"""
        since = float(queued_record(ticket.record, ticket.number, self.watching.clock())["queued_at"])
        self.since[key] = since
        self._note_reset(key, ticket.retry_after)
        if self.expired(key):
            return False
        self.queued[key] = ticket
        self._arm()
        return True

    def add_ingest(self, key: str, pull: github_pulls.MergedPull) -> bool:
        """反映待ちに加える（記録の時刻は書き換えない）。上限をすでに超えていれば加えずに偽を返す。"""
        held = self._ingest_records()
        if key not in held:
            held[key] = {"sha": pull.merge_sha, "merged_at": self.watching.clock()}
            self._write_ingest(held)
        self.since[key] = float(held[key]["merged_at"])
        if self.expired(key):
            self.ingest.pop(key, None)
            return False
        self.ingest[key] = pull
        self._arm()
        return True

    def taken(self, *keys: str) -> None:
        """作業場所へ反映できた単位を一覧と記録から消す。記録の読み書きは、単位の数によらずそれぞれ 1 回までである。"""
        if not keys:
            return
        held = self._ingest_records()
        found = False
        for key in keys:
            self.ingest.pop(key, None)
            found = held.pop(key, None) is not None or found
        if found:
            self._write_ingest(held)

    def _ingest_records(self) -> dict[str, Any]:
        path = self.watching.ingest
        found = None if path is None else plan_files.read_json(path)
        return found if found is not None else {}

    def _write_ingest(self, held: dict[str, Any]) -> None:
        if self.watching.ingest is not None:
            plan_files.write_json(self.watching.ingest, held)

    def expired(self, key: str) -> bool:
        return self.watching.clock() - self.since[key] >= self.watching.budget

    def resets_after_deadline(self, key: str) -> Optional[float]:
        """GitHub の待ちが解ける時刻が上限より後なら、今から解けるまでの秒。そうでなければ None。"""
        at = self.resumable.get(key)
        if at is None or at <= self.since[key] + self.watching.budget:
            return None
        return max(0.0, at - self.watching.clock())

    def until(self) -> Optional[float]:
        """次の問い合わせ（か待ちの上限）までの秒。待ちが無ければ None。"""
        if not self.waiting():
            return None
        return max(0.0, self._target() - self.watching.clock())

    def due(self) -> bool:
        return self.waiting() and self.watching.clock() >= self._target()

    def read_due(self) -> bool:
        """次の問い合わせの時刻に達したか。待ちの上限が来ただけなら偽（ほかの PR を読まない）。"""
        return self.next_at is not None and self.watching.clock() >= self.next_at

    def _target(self) -> float:
        """次の問い合わせの時刻と、いちばん早い待ちの上限の早い方。"""
        assert self.next_at is not None, "待ちを加えると次の時刻を持つ"
        return min([self.next_at, *(self.since[k] + self.watching.budget for k in (*self.queued, *self.ingest))])

    def _arm(self) -> None:
        if self.next_at is None:
            self.next_at = self.watching.clock() + self.interval

    def read(self) -> dict[str, State]:
        """キューで待つ PR をまとめて読み、単位のキーごとの状態を返す。待つ PR が無ければ問い合わせない。"""
        if not self.queued:
            return {}
        found = self.watching.read([t.number for t in self.queued.values()])
        states = {k: found[t.number] for k, t in self.queued.items()}
        for key, state in states.items():
            if state.retry_after is None:
                self.resumable.pop(key, None)
            else:
                self._note_reset(key, state.retry_after)
        return states

    def _note_reset(self, key: str, retry_after: Optional[float]) -> None:
        """GitHub が示した待ちの秒があれば、待ちが解ける時刻を持つ。"""
        if retry_after is not None:
            self.resumable[key] = self.watching.clock() + retry_after

    def schedule(self, states: Mapping[str, State]) -> None:
        """問い合わせの後の次の時刻を決める。状態が変わらない間は間隔を延ばし、変われば 1 ステップ縮める。"""
        snapshot = dict(states)
        if self.previous is not None:
            if snapshot == self.previous:
                self.interval = min(self.interval * self.factor, self.longest)
            else:
                self.interval = max(self.first, self.interval / self.factor)
        self.previous = snapshot
        self.next_at = None
        if self.waiting():
            self._arm()
        else:
            self.rest()

    def rest(self) -> None:
        """待ちが無くなっていれば、次の問い合わせの時刻と間隔を最初に戻す。"""
        if not self.waiting():
            self.next_at, self.interval, self.previous = None, self.first, None
