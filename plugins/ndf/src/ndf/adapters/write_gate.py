"""GitHub への書き込みにマシン全体の順番を取らせる githubkit の throttler（#4016）。

- 同じ OS のユーザーの NDF の書き込みは、1 つの記録（書き込みの順番の記録。`gate_path()`）で順番を取る。
  記録はホームの下に置き、環境変数（`NDF_STATE_HOME`・`XDG_STATE_HOME`・`HOME`）で場所を変えない（決定 1）
- 予約は記録のロックの中で決めて書き、ロックの外で眠る（決定 2）。どの書き込みも前の書き込みから
  `github.write-spacing` の秒、内容を作る書き込みは前の内容を作る書き込みから `github.content-spacing` の秒を
  空ける（決定 3・I1・I2）
- 送る直前に送信のロックを取り、待ちの終わり（`block`）と最短の送信時刻を読み直す。待ちの終わりが延びていたら
  予約を置き換え（I3）、前の書き込みを送り終えた時刻から間隔が足りなければ待つ（I6）。送信のロックは応答まで持つ
- 記録を読めない・ロックを取れないときも書き込みを止めない。プロセスの中の記録で間を空け、標準エラーへ 1 行を出す（I5）。
  記録へ書けなかった分は、書けるまで記録を読むたびにプロセスの中の記録から重ねる
- 非同期の経路では、ロックの取得を含む手順をイベントループの外（`asyncio.to_thread`）で進める。送信のロックを取った後の
  手順（最短の送信時刻を待った後の読み直しと、放す `_finish`）は専用のスレッド（`_FINISHER`）で進め、ロックを待つ手順が
  既定のスレッドプールを埋めても、ロックを持つ手順が進めるようにする
- passwd に載らない UID は、環境に依らない UID ごとの保存先（`/tmp/ndf-github-<UID>`。オーナーと 0700 を確かめる）を使う
- 待ちの予算を持つ呼び手（`within(remaining)`）は、どのロックの待ちも眠りも `remaining()` に収め、収まらなければ送らずに
  `Busy` を出す。予約を書いた後なら `withdraw` で取り消す。予算で縛ったロックの時間切れは I5 へ落とさない（決定 10）

ロックは取るたびに `slot_pool.lock` で作る。1 つの `FileLock` をスレッドで共有すると、別のスレッドの取得が
再入として通るためである。
"""

from __future__ import annotations

import asyncio
import functools
import stat
import json
import os
import pwd
import re
import tempfile
import threading
import time
from collections.abc import AsyncGenerator, Callable, Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, closing, contextmanager
from pathlib import Path
from typing import Any, NamedTuple, Optional, TypeVar

import httpx
from filelock import FileLock, Timeout
from githubkit.throttling import BaseThrottler

from ndf import slot_pool
from ndf.adapters.run_log import diag
from ndf.contracts import limits

T = TypeVar("T")
# 書き込みの種類: どの書き込みも空ける・内容を作る書き込み（REST の POST と create・add で始まる mutation）
WRITE, CONTENT = "write", "content"
# 間隔とロックの待ちの方針（contracts/limits.json）
WRITE_SPACING = "github.write-spacing"
CONTENT_SPACING = "github.content-spacing"
SEND_LOCK = "github.write-send-lock"
PR_LOCK = "github.pr-publish-lock"
# 記録のロックを待つ上限の秒。ロックの中では読み・計算・書きだけを行う
LOCK_SECONDS = 10.0
# 記録を最後に書いた時刻が今よりこの秒を越えて先なら、壁時計が戻ったとみなす
CLOCK_SLACK = 1.0
GATE_FILE = "write-gate.json"
# passwd に載らない UID の保存先の親。環境変数（TMPDIR）を読まない（決定 1）
SHARED_TMP = Path("/tmp")
# 非同期の経路で送信のロックを持つ手順を進める専用のスレッド。送信のロックを待たないので、ここが埋まって止まることはない
_FINISHER = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ndf-write-gate-finish")
SEND_LOCK_FILE = "write-gate.send.lock"
PR_LOCK_DIR = "pr"
RECORD_VERSION = 1
# 記録の時刻の欄（UNIX 時刻の秒）。`written_at` は別に持つ
TIMES = ("next", "next_content", "sent_next", "sent_next_content", "blocked_until")
READS = ("GET", "HEAD")
GRAPHQL_PATH = "/graphql"
CONTENT_FIELDS = ("create", "add")
# GraphQL の文書の文字列とコメント（読み飛ばす。文字列の中の `#` をコメントと読まない）
_IGNORED = re.compile(r'"""[\s\S]*?"""|"(?:\\.|[^"\\\n])*"|#[^\n\r]*')
# 変数の定義と引数の括弧（入れ子を持たない）
_PARENS = re.compile(r"\([^()]*\)")
_MUTATION = re.compile(r"[\s,﻿]*mutation(?![_0-9A-Za-z])")
# 最初の選択の最初のフィールドの名前（別名 `a: field` なら field）
# 送信前の分類（`write_of` の結果）を残す、httpx の要求の `extensions` のキー
WRITES = "ndf.writes"
_FIRST_FIELD = re.compile(r"[^{]*\{[\s,]*(?:[_A-Za-z][_0-9A-Za-z]*[\s,]*:[\s,]*)?([_A-Za-z][_0-9A-Za-z]*)")


class Writes(NamedTuple):
    """1 つの要求が送る書き込みの種類（`WRITE`・`CONTENT`）と件数（1 以上）。"""

    kind: str
    count: int


class Pause(NamedTuple):
    """手順が眠る秒と、眠る間に送信のロックを持っているか（持っていれば非同期の経路は再開を `_FINISHER` で進める）。"""

    seconds: float
    holding: bool = False


class Reservation(NamedTuple):
    """予約までの秒・予約の時刻・種類・書く前の `next` と `next_content`・件数。"""

    wait: float
    at: float
    kind: str
    before: tuple[float, float]
    count: int = 1


class Busy(Exception):
    """順番の待ちが呼び手の予算に収まらない。`seconds` は収まらなかった待ちの秒（ロックを待つ前に予算が切れていれば 0）。"""

    def __init__(self, seconds: float) -> None:
        super().__init__(f"書き込みの順番の待ち {seconds:.1f} 秒が待ちの予算に収まらない")
        self.seconds = seconds


def _bounded(remaining: Optional[Callable[[], float]], cap: float) -> tuple[float, bool]:
    """ロックを待つ秒と、それが予算で縛られたか。予算が 0 以下ならロックを待たずに `Busy`（負の秒は無期限の待ちになる）。"""
    if remaining is None:
        return cap, False
    left = remaining()
    if left <= 0:
        raise Busy(0.0)
    return (left, True) if left < cap else (cap, False)


def _within_budget(remaining: Optional[Callable[[], float]], wait: float) -> None:
    """待ちが予算以上なら `Busy`（待ち終えた時点で残りが 0 になり、送れないため等しいときも出す）。"""
    if remaining is not None and wait >= remaining():
        raise Busy(wait)


def _mutation_kind(document: str) -> Optional[str]:
    """GraphQL の文書を読み取り（None）・書き込み・内容を作る書き込みに分ける。読めない mutation は内容を作る側へ倒す。"""
    text = _IGNORED.sub(" ", document)
    head = _MUTATION.match(text)
    if head is None:
        return None
    field = _FIRST_FIELD.match(_PARENS.sub(" ", text[head.end() :]))
    if field is None or field.group(1).startswith(CONTENT_FIELDS):
        return CONTENT
    return WRITE


def write_of(request: httpx.Request) -> Optional[Writes]:
    """要求を読み取り（None）か、書き込みの種類と件数に分ける。件数は 1（数え方は #4059 が足す）。"""
    method = request.method.upper()
    if method in READS:
        return None
    if method != "POST":
        return Writes(WRITE, 1)
    if request.url.path != GRAPHQL_PATH:
        return Writes(CONTENT, 1)
    try:
        body = json.loads(request.content)
    except ValueError:  # 本文が JSON でなければ書き込みと数える
        return Writes(WRITE, 1)
    if not isinstance(body, dict) or not isinstance(body.get("query"), str):
        return Writes(WRITE, 1)
    kind = _mutation_kind(body["query"])
    return None if kind is None else Writes(kind, 1)


def classified(request: httpx.Request) -> Optional[Writes]:
    """`write_of` の結果を `request.extensions` に残して返す。送信前の分類を応答のフックで再利用する（PR 4265 の r3-1）。"""
    if WRITES in request.extensions:
        return request.extensions[WRITES]
    writes = request.extensions[WRITES] = write_of(request)
    return writes


def _spacing(kind: str) -> tuple[float, float]:
    """1 件の書き込みが後ろへ空ける (どの書き込みとの秒, 内容を作る書き込みとの秒)。`WRITE` は後者が 0。"""
    write = limits.value(WRITE_SPACING, "interval")
    return write, (limits.value(CONTENT_SPACING, "interval") if kind == CONTENT else 0.0)


def _empty() -> dict[str, float]:
    return dict.fromkeys((*TIMES, "written_at"), 0.0)


def _valid(raw: object) -> bool:
    if not isinstance(raw, dict) or raw.get("version") != RECORD_VERSION:
        return False
    values = [raw.get(k) for k in (*TIMES, "written_at")]
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values)


def _rewound(record: dict[str, float], now: float) -> dict[str, float]:
    """壁時計が戻っていたら、戻った秒だけ記録の時刻を手前へずらす（待ちの終わりの残りを保つ）。"""
    back = record["written_at"] - now
    if back > CLOCK_SLACK:
        for key in TIMES:
            record[key] -= back
    return record


def _earliest_send(record: dict[str, float], kind: str) -> float:
    """前の書き込みを送り終えた時刻から数えた、この種類を送ってよい最も早い時刻（I6）。"""
    if kind == CONTENT:
        return max(record["sent_next"], record["sent_next_content"])
    return record["sent_next"]


def _advance(steps: Generator[Pause, None, Optional[FileLock]]) -> tuple[bool, Any]:
    """手順を 1 つ進める。(終わったか, `Pause` または送信のロック)。StopIteration を Future へ渡さないために包む。"""
    try:
        return False, next(steps)
    except StopIteration as done:
        return True, done.value


def _abandon(steps: Generator[Pause, None, Optional[FileLock]], step: asyncio.Future) -> None:
    """取り消された非同期の手順の後始末。スレッドが終えてから、持っている送信のロックを放す。"""
    if step.cancelled() or step.exception() is not None:
        return
    finished, value = step.result()
    if finished:
        if value is not None:
            value.release()
    else:
        steps.close()


def _restore(record: dict[str, float], old: Reservation) -> None:
    """`old` の書いた `next`（`CONTENT` なら `next_content` も）がそのままなら書く前へ戻す。後ろに別の予約があれば戻さない。"""
    write, content = _spacing(old.kind)
    if record["next"] == old.at + old.count * write:
        record["next"] = old.before[0]
    if old.kind == CONTENT and record["next_content"] == old.at + old.count * content:
        record["next_content"] = old.before[1]


@functools.cache
def _private_home() -> Path:
    """このプロセスだけが使う保存先（0700・名前を予測できない）。UID ごとの保存先を安全に使えないときだけ使う。"""
    return Path(tempfile.mkdtemp(prefix="ndf-github-"))


def _uid_home(uid: int) -> Optional[Path]:
    """UID ごとの保存先 `SHARED_TMP/ndf-github-<UID>`。シンボリックリンク・ほかのオーナー・0700 でなければ None。"""
    place = SHARED_TMP / f"ndf-github-{uid}"
    try:
        place.mkdir(mode=0o700, exist_ok=True)
        info = os.lstat(place)
    except OSError as exc:
        diag(f"書き込みの順番の記録の保存先を作れない: {place}: {exc}", kind="write_gate")
        return None
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o077:
        diag(f"書き込みの順番の記録の保存先を使えない（オーナーか権限が違う）: {place}", kind="write_gate")
        return None
    return place


def _home() -> Path:
    """OS のユーザーのホーム。passwd に載らない UID（コンテナの任意 UID）は `HOME` を読まず UID ごとの保存先へ落とす。"""
    uid = os.getuid()
    try:
        return Path(pwd.getpwuid(uid).pw_dir)
    except KeyError:
        fallback = _uid_home(uid) or _private_home()
        diag(f"UID {uid} が passwd に無い。書き込みの順番の記録を {fallback} の下に置く", kind="write_gate")
        return fallback


def gate_path() -> Path:
    """書き込みの順番の記録のパス。OS のユーザーのホームから引き、環境変数を読まない（決定 1）。"""
    return _home() / ".local" / "state" / "ndf-github" / GATE_FILE


def machine_gate() -> WriteGate:
    """OS のユーザーに 1 つの記録を使う `WriteGate`。"""
    return WriteGate(gate_path())


def gate_of(throttler: BaseThrottler) -> Optional[WriteGate]:
    """githubkit のクライアントの throttler が使う `WriteGate`（`within` の throttler ならその元）。ほかの throttler は None。"""
    if isinstance(throttler, WriteGate):
        return throttler
    return throttler.gate if isinstance(throttler, Within) else None


def pr_lock(repo: str, pr: int) -> FileLock:
    """同じ PR の公開のロック。取るたびに別のインスタンスを作り、待つ上限は `github.pr-publish-lock` の秒。

    GitHub はリポジトリ名の大文字と小文字を区別しないので、小文字にそろえてからファイル名にする。
    """
    name = f"{repo.lower().replace('/', '__')}__{pr}.lock"
    held = slot_pool.lock(gate_path().parent / PR_LOCK_DIR / name)
    held.timeout = limits.value(PR_LOCK, "lock")
    return held


class WriteGate(BaseThrottler):
    """書き込みの要求にマシン全体の順番を取らせる。読み取りはすぐ通す。"""

    def __init__(self, path: Path, clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep) -> None:
        self.path = path
        self.clock = clock
        self.sleep = sleep
        # 記録を使えないときに間を空けるプロセスの中の記録（I5）。記録を書けたら写す
        self._memory = _empty()
        # プロセスの中の記録に、記録へ書けていない変更があるか
        self._unsaved = False
        # プロセスの中の記録だけを変えた回数。記録へ重ねた後に増えていれば、その変更を保存の結果で消さない
        self._fallbacks = 0
        self._mutex = threading.Lock()

    @contextmanager
    def acquire(self, request: httpx.Request) -> Generator[None, Any, Any]:
        with self.passing(request):
            yield

    @asynccontextmanager
    async def async_acquire(self, request: httpx.Request) -> AsyncGenerator[None, Any]:
        async with self.async_passing(request):
            yield

    def within(self, remaining: Callable[[], float]) -> Within:
        """同じ記録を使い、どのロックの待ちも眠りも `remaining()` に収める throttler（決定 10）。"""
        return Within(self, remaining)

    @contextmanager
    def passing(self, request: httpx.Request, remaining: Optional[Callable[[], float]] = None) -> Generator[None, Any, Any]:
        """書き込みなら順番を取ってから通す（`remaining` があれば予算の中で。収まらなければ `Busy`）。読み取りはすぐ通す。"""
        writes = classified(request)
        if writes is None:
            yield
            return
        with self.hold(writes.kind, writes.count, remaining):
            yield

    @asynccontextmanager
    async def async_passing(self, request: httpx.Request, remaining: Optional[Callable[[], float]] = None) -> AsyncGenerator[None, Any]:
        """`passing` の非同期の口。"""
        writes = classified(request)
        if writes is None:
            yield
            return
        send = await self._turn(writes.kind, writes.count, remaining)
        try:
            yield
        finally:
            # 既定のスレッドプールは送信のロックを待つ手順で埋まりうるので、放す処理は専用のスレッドで進める。
            # 呼び出し元が取り消されても、投入済みの放す処理は取り消さない（shield）
            finishing = asyncio.get_running_loop().run_in_executor(_FINISHER, self._finish, writes.kind, send)
            await asyncio.shield(finishing)

    async def _turn(self, kind: str, count: int, remaining: Optional[Callable[[], float]] = None) -> Optional[FileLock]:
        """`_steps` の各手順（ロックの取得を含む）をイベントループの外で進め、眠りだけをループの上で待つ。

        送信のロックを持ったまま眠った手順の再開は `_FINISHER` で進める。既定のスレッドプールが送信のロックを待つ手順で
        埋まっても、ロックを持つ手順がその空きを待たない。
        """
        steps = self._steps(kind, count, remaining)
        loop = asyncio.get_running_loop()
        holding = False
        while True:
            step = asyncio.ensure_future(loop.run_in_executor(_FINISHER if holding else None, _advance, steps))
            try:
                finished, value = await asyncio.shield(step)
            except asyncio.CancelledError:
                step.add_done_callback(lambda done: _abandon(steps, done))
                raise
            if finished:
                return value
            holding = value.holding
            try:
                await asyncio.sleep(value.seconds)
            except BaseException:
                steps.close()
                raise

    @contextmanager
    def hold(self, kind: str, count: int = 1, remaining: Optional[Callable[[], float]] = None) -> Generator[None, Any, Any]:
        """種類と件数で順番を取り、文脈の中で送らせる。出口で送り終えた時刻を記録し、送信のロックを放す（例外でも）。

        `remaining` があれば待ちを予算に収め、収まらなければ送らずに `Busy` を出す（出口の記録もしない）。
        """
        with closing(self._steps(kind, count, remaining)) as steps:
            send = None
            try:
                while True:
                    self.sleep(next(steps).seconds)
            except StopIteration as done:
                send = done.value
        try:
            yield
        finally:
            self._finish(kind, send)

    def reserve(
        self,
        kind: str,
        replacing: Optional[Reservation] = None,
        count: int = 1,
        remaining: Optional[Callable[[], float]] = None,
    ) -> Reservation:
        """記録のロックの中で予約を決めて書く。`replacing` は同じロックの中で書く前へ戻してから決める（I3 の待ち直し）。

        `remaining` があれば記録のロックの待ちを予算で縛り、予約までの秒が `remaining()` 以上なら新しい予約を書かずに
        `Busy` を出す。`replacing` の戻しは `Busy` でも書く（ロックを取れなければ `withdraw` で試す）。
        """

        def change(record: dict[str, float], now: float) -> Reservation | Busy:
            if replacing is not None:
                _restore(record, replacing)
            before = (record["next"], record["next_content"])
            at = max(now, record["next"], record["blocked_until"])
            if kind == CONTENT:
                at = max(at, record["next_content"])
            try:
                _within_budget(remaining, at - now)
            except Busy as busy:
                if replacing is None:
                    raise
                return busy
            write, content = _spacing(kind)
            record["next"] = at + count * write
            if kind == CONTENT:
                record["next_content"] = at + count * content
            return Reservation(at - now, at, kind, before, count)

        try:
            made = self._apply(change, remaining)
        except Busy:
            if replacing is not None:
                self.withdraw(replacing)
            raise
        if isinstance(made, Busy):
            raise made
        return made

    def withdraw(self, reservation: Reservation) -> None:
        """送らなかった予約を取り消す。`next`（`CONTENT` なら `next_content` も）が自分の書いた値のままなら書く前へ戻す。

        予算が切れた後に呼ぶので記録のロックを待たない。すぐ取れなければ何もせずに返り、予約は残る（時刻が過ぎれば効かない）。
        """
        held = slot_pool.lock(self._record_lock_path())
        try:
            held.acquire(timeout=0)
        except OSError:  # noqa: NDF401 ファイル: 取り消しは待たない。予約は時刻が過ぎれば効かない（Timeout を含む）
            return
        self._under(held, lambda record, _now: _restore(record, reservation))

    def sent(self, kind: str) -> None:
        """送り終えた時刻から、次の書き込みの時刻と最短の送信時刻を延ばす（I6）。"""

        def change(record: dict[str, float], now: float) -> None:
            write, content = _spacing(kind)
            for key in ("next", "sent_next"):
                record[key] = max(record[key], now + write)
            if kind == CONTENT:
                for key in ("next_content", "sent_next_content"):
                    record[key] = max(record[key], now + content)

        self._apply(change)

    def block(self, seconds: float) -> None:
        """待ちの終わりを `max(今の待ちの終わり, 今 + seconds)` へ延ばす（縮めない）。"""

        def change(record: dict[str, float], now: float) -> None:
            record["blocked_until"] = max(record["blocked_until"], now + seconds)

        self._apply(change)

    def _steps(self, kind: str, count: int, remaining: Optional[Callable[[], float]] = None) -> Generator[Pause, None, Optional[FileLock]]:
        """眠る秒を順に出し、送ってよくなったら送信のロック（取れなければ None。I5）を返す。

        予算の切れ（`Busy`）では、送信のロックを放し、書いた予約を `withdraw` で取り消してから出す。置き換えの `reserve` が
        `Busy` を出したときは、旧予約は `reserve` が戻しているので取り消さない。
        """
        reservation: Optional[Reservation] = None
        try:
            reservation = self.reserve(kind, count=count, remaining=remaining)
            while True:
                if reservation.wait > 0:
                    yield Pause(reservation.wait)
                blocked, send = yield from self._sendable(kind, remaining)
                if not blocked:
                    return send
                replaced, reservation = reservation, None
                reservation = self.reserve(kind, replacing=replaced, count=count, remaining=remaining)
        except Busy:
            if reservation is not None:
                self.withdraw(reservation)
            raise

    def _sendable(self, kind: str, remaining: Optional[Callable[[], float]]) -> Generator[Pause, None, tuple[bool, Optional[FileLock]]]:
        """送信のロックを取り、最短の送信時刻まで眠る秒を出す。(待ちの終わりが延びたか, 送信のロック)。延びたらロックを放して返す。"""
        send = self._send_lock(remaining)
        try:
            blocked = yield from self._settle(kind, send is not None, remaining)
            if not blocked and remaining is not None and remaining() <= 0:
                raise Busy(0.0)
        except BaseException:
            if send is not None:
                send.release()
            raise
        if blocked and send is not None:
            send.release()
        return blocked, (None if blocked else send)

    def _settle(self, kind: str, holding: bool, remaining: Optional[Callable[[], float]] = None) -> Generator[Pause, None, bool]:
        """送信のロックを持ったまま（`holding`）最短の送信時刻まで眠る秒を出す。待ちの終わりが延びていたら真。"""
        while True:
            seen = self._peek(kind, remaining)
            if seen is None:
                return False
            blocked, gap = seen
            if blocked:
                return True
            if gap <= 0:
                return False
            _within_budget(remaining, gap)
            yield Pause(gap, holding)

    def _finish(self, kind: str, send: Optional[FileLock]) -> None:
        try:
            self.sent(kind)
        finally:
            if send is not None:
                send.release()

    def _peek(self, kind: str, remaining: Optional[Callable[[], float]] = None) -> Optional[tuple[bool, float]]:
        """(待ちの終わりが今より後か, 最短の送信時刻までの秒)。記録のロックを取れない・読めなければプロセスの中の記録で読む（I5）。"""
        held = self._record_lock(remaining)
        try:
            record = None if held is None else self._load()
            if record is None:
                now = self.clock()
                record = self._memory_view(now)
            else:
                record, _, now = self._with_unsaved(record)
            return record["blocked_until"] > now, _earliest_send(record, kind) - now
        finally:
            if held is not None:
                held.release()

    def _apply(self, change: Callable[[dict[str, float], float], T], remaining: Optional[Callable[[], float]] = None) -> T:
        """記録のロックの中で記録を変えて書く。使えなければプロセスの中の記録で変える（I5）。"""
        held = self._record_lock(remaining)
        if held is None:
            return self._in_process(change)
        return self._under(held, change)

    def _under(self, held: FileLock, change: Callable[[dict[str, float], float], T]) -> T:
        """取った記録のロック `held` の中で記録を変えて書き、ロックを放す。記録を読めなければプロセスの中の記録で変える。"""
        try:
            record = self._load()
            if record is None:
                return self._in_process(change)
            record, seen, now = self._with_unsaved(record)
            result = change(record, now)
            self._store(record, now, seen)
            return result
        finally:
            held.release()

    def _in_process(self, change: Callable[[dict[str, float], float], T]) -> T:
        with self._mutex:
            now = self.clock()
            result = change(self._memory, now)
            self._memory["written_at"] = now
            self._unsaved = True
            self._fallbacks += 1
            return result

    def _memory_view(self, now: float) -> dict[str, float]:
        with self._mutex:
            memory = dict(self._memory)
        return _rewound(memory, now)

    def _with_unsaved(self, record: dict[str, float]) -> tuple[dict[str, float], int, float]:
        """記録へ書けていない変更があれば、プロセスの中の記録の時刻を重ねる（遅い方を採る）。重ねた時点の変更の回数と基準の時刻も返す。

        基準の時刻はプロセスの中の記録の写しと同じ `_mutex` の中で取る。写しに入った変更はどれもこの時刻より前に
        書かれているので、正常に進む時計で `_rewound` が待ちを縮めない。読んだ記録もこの時刻へそろえる。
        """
        with self._mutex:
            now = self.clock()
            unsaved, seen, memory = self._unsaved, self._fallbacks, dict(self._memory)
        record = _rewound(record, now)
        if unsaved:
            memory = _rewound(memory, now)
            for key in TIMES:
                record[key] = max(record[key], memory[key])
        return record, seen, now

    def _record_lock_path(self) -> Path:
        return self.path.with_name(self.path.name + ".lock")

    def _record_lock(self, remaining: Optional[Callable[[], float]] = None) -> Optional[FileLock]:
        """記録のロック。`LOCK_SECONDS` の時間切れは None（I5）、予算で縛った待ちの時間切れは `Busy`。"""
        wait, budgeted = _bounded(remaining, LOCK_SECONDS)
        held = slot_pool.lock(self._record_lock_path())
        try:
            held.acquire(timeout=wait)
        except Timeout:
            if budgeted:
                raise Busy(wait) from None
            diag(f"書き込みの順番の記録のロックを取れない: {self.path}", kind="write_gate")
            return None
        except OSError as exc:
            diag(f"書き込みの順番の記録を読めない: {self.path}: {exc}", kind="write_gate")
            return None
        return held

    def _send_lock(self, remaining: Optional[Callable[[], float]] = None) -> Optional[FileLock]:
        """送信のロック。`github.write-send-lock` の時間切れは None（I5）、予算で縛った待ちの時間切れは `Busy`。"""
        wait, budgeted = _bounded(remaining, limits.value(SEND_LOCK, "lock"))
        path = self.path.with_name(SEND_LOCK_FILE)
        held = slot_pool.lock(path)
        try:
            held.acquire(timeout=wait)
        except OSError as exc:  # Timeout を含む
            if budgeted and isinstance(exc, Timeout):
                raise Busy(wait) from None
            diag(f"書き込みの送信のロックを取れない: {path}", kind="write_gate")
            return None
        return held

    def _load(self) -> Optional[dict[str, float]]:
        """記録を読む（時刻はそろえない。`_with_unsaved` がそろえる）。無ければ空、壊れていれば 1 行を出して空。読めない（OSError）なら 1 行を出して None。"""
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return _empty()
        except OSError as exc:
            diag(f"書き込みの順番の記録を読めない: {self.path}: {exc}", kind="write_gate")
            return None
        try:
            raw = json.loads(text)
        except ValueError:
            raw = None
        if not _valid(raw):
            diag(f"書き込みの順番の記録を読めない: {self.path}: 形が違う", kind="write_gate")
            return _empty()
        return {k: float(raw[k]) for k in (*TIMES, "written_at")}

    def _store(self, record: dict[str, float], now: float, seen: int) -> None:
        """記録を書き、プロセスの中の記録へ写す。`seen` の後にプロセスの中だけで変えた分は残し、未保存の変更も消さない。"""
        record["written_at"] = now
        with self._mutex:
            self._memory = self._merged(record, seen)
        try:
            slot_pool.write_json(self.path, {"version": RECORD_VERSION, **record})
        except OSError as exc:
            diag(f"書き込みの順番の記録を書けない: {self.path}: {exc}", kind="write_gate")
            unsaved = True
        else:
            unsaved = False
        with self._mutex:
            self._memory = self._merged(record, seen)
            self._unsaved = unsaved or self._fallbacks != seen

    def _merged(self, record: dict[str, float], seen: int) -> dict[str, float]:
        """（`_mutex` の中で呼ぶ）記録の写し。`seen` の後にプロセスの中だけで変えた時刻があれば遅い方を採る。

        その変更は保存を始めた時刻より後に入りうるので、両方を今の時刻へそろえてから重ね、書いた時刻も今にする。
        """
        if self._fallbacks == seen:
            return dict(record)
        now = self.clock()
        saved = _rewound(dict(record), now)
        memory = _rewound(dict(self._memory), now)
        return {**saved, **{key: max(saved[key], memory[key]) for key in TIMES}, "written_at": now}


class Within(BaseThrottler):
    """`WriteGate` の記録を使い、順番の待ちを呼び手の予算（`remaining`）に収める throttler（決定 10）。"""

    def __init__(self, gate: WriteGate, remaining: Callable[[], float]) -> None:
        self.gate = gate
        self.remaining = remaining

    @contextmanager
    def acquire(self, request: httpx.Request) -> Generator[None, Any, Any]:
        with self.gate.passing(request, self.remaining):
            yield

    @asynccontextmanager
    async def async_acquire(self, request: httpx.Request) -> AsyncGenerator[None, Any]:
        async with self.gate.async_passing(request, self.remaining):
            yield
