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
- 非同期の経路では、ロックの取得を含む手順をイベントループの外（`asyncio.to_thread`）で進める。送信のロックを放す
  `_finish` は専用のスレッド（`_FINISHER`）で進め、ロックを待つ手順が既定のスレッドプールを埋めても放せるようにする
- passwd に載らない UID は、環境に依らない UID ごとの置き場（`/tmp/ndf-github-<UID>`。持ち主と 0700 を確かめる）を使う

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
# passwd に載らない UID の置き場の親。環境変数（TMPDIR）を読まない（決定 1）
SHARED_TMP = Path("/tmp")
# 非同期の経路で送信のロックを放す専用のスレッド。送信のロックを待たないので、ここが埋まって止まることはない
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
_FIRST_FIELD = re.compile(r"[^{]*\{[\s,]*(?:[_A-Za-z][_0-9A-Za-z]*[\s,]*:[\s,]*)?([_A-Za-z][_0-9A-Za-z]*)")


class Writes(NamedTuple):
    """1 つの要求が送る書き込みの種類（`WRITE`・`CONTENT`）と件数（1 以上）。"""

    kind: str
    count: int


class Reservation(NamedTuple):
    """予約までの秒・予約の時刻・種類・書く前の `next` と `next_content`・件数。"""

    wait: float
    at: float
    kind: str
    before: tuple[float, float]
    count: int = 1


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


def _advance(steps: Generator[float, None, Optional[FileLock]]) -> tuple[bool, Any]:
    """手順を 1 つ進める。(終わったか, 眠る秒または送信のロック)。StopIteration を Future へ渡さないために包む。"""
    try:
        return False, next(steps)
    except StopIteration as done:
        return True, done.value


def _abandon(steps: Generator[float, None, Optional[FileLock]], step: asyncio.Future) -> None:
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
    """このプロセスだけが使う置き場（0700・名前を予測できない）。UID ごとの置き場を安全に使えないときだけ使う。"""
    return Path(tempfile.mkdtemp(prefix="ndf-github-"))


def _uid_home(uid: int) -> Optional[Path]:
    """UID ごとの置き場 `SHARED_TMP/ndf-github-<UID>`。シンボリックリンク・ほかの持ち主・0700 でなければ None。"""
    place = SHARED_TMP / f"ndf-github-{uid}"
    try:
        place.mkdir(mode=0o700, exist_ok=True)
        info = os.lstat(place)
    except OSError as exc:
        diag(f"書き込みの順番の記録の置き場を作れない: {place}: {exc}", kind="write_gate")
        return None
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o077:
        diag(f"書き込みの順番の記録の置き場を使えない（持ち主か権限が違う）: {place}", kind="write_gate")
        return None
    return place


def _home() -> Path:
    """OS のユーザーのホーム。passwd に載らない UID（コンテナの任意 UID）は `HOME` を読まず UID ごとの置き場へ落とす。"""
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
        writes = write_of(request)
        if writes is None:
            yield
            return
        with self.hold(writes.kind, writes.count):
            yield

    @asynccontextmanager
    async def async_acquire(self, request: httpx.Request) -> AsyncGenerator[None, Any]:
        writes = write_of(request)
        if writes is None:
            yield
            return
        send = await self._turn(writes.kind, writes.count)
        try:
            yield
        finally:
            # 既定のスレッドプールは送信のロックを待つ手順で埋まりうるので、放す処理は専用のスレッドで進める
            await asyncio.get_running_loop().run_in_executor(_FINISHER, self._finish, writes.kind, send)

    async def _turn(self, kind: str, count: int) -> Optional[FileLock]:
        """`_steps` の各手順（ロックの取得を含む）をイベントループの外で進め、眠りだけをループの上で待つ。"""
        steps = self._steps(kind, count)
        while True:
            step = asyncio.ensure_future(asyncio.to_thread(_advance, steps))
            try:
                finished, value = await asyncio.shield(step)
            except asyncio.CancelledError:
                step.add_done_callback(lambda done: _abandon(steps, done))
                raise
            if finished:
                return value
            try:
                await asyncio.sleep(value)
            except BaseException:
                steps.close()
                raise

    @contextmanager
    def hold(self, kind: str, count: int = 1) -> Generator[None, Any, Any]:
        """種類と件数で順番を取り、文脈の中で送らせる。出口で送り終えた時刻を記録し、送信のロックを放す（例外でも）。"""
        with closing(self._steps(kind, count)) as steps:
            send = None
            try:
                while True:
                    self.sleep(next(steps))
            except StopIteration as done:
                send = done.value
        try:
            yield
        finally:
            self._finish(kind, send)

    def reserve(self, kind: str, replacing: Optional[Reservation] = None, count: int = 1) -> Reservation:
        """記録のロックの中で予約を決めて書く。`replacing` は同じロックの中で書く前へ戻してから決める（I3 の待ち直し）。"""

        def change(record: dict[str, float], now: float) -> Reservation:
            if replacing is not None:
                _restore(record, replacing)
            before = (record["next"], record["next_content"])
            at = max(now, record["next"], record["blocked_until"])
            if kind == CONTENT:
                at = max(at, record["next_content"])
            write, content = _spacing(kind)
            record["next"] = at + count * write
            if kind == CONTENT:
                record["next_content"] = at + count * content
            return Reservation(at - now, at, kind, before, count)

        return self._apply(change)

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

    def _steps(self, kind: str, count: int) -> Generator[float, None, Optional[FileLock]]:
        """眠る秒を順に出し、送ってよくなったら送信のロック（取れなければ None。I5）を返す。"""
        reservation = self.reserve(kind, count=count)
        while True:
            if reservation.wait > 0:
                yield reservation.wait
            send = self._send_lock()
            try:
                blocked = yield from self._settle(kind)
            except BaseException:
                if send is not None:
                    send.release()
                raise
            if not blocked:
                return send
            if send is not None:
                send.release()
            reservation = self.reserve(kind, replacing=reservation, count=count)

    def _settle(self, kind: str) -> Generator[float, None, bool]:
        """送信のロックを持ったまま最短の送信時刻まで眠る秒を出す。待ちの終わりが延びていたら真。"""
        while True:
            seen = self._peek(kind)
            if seen is None:
                return False
            blocked, gap = seen
            if blocked:
                return True
            if gap <= 0:
                return False
            yield gap

    def _finish(self, kind: str, send: Optional[FileLock]) -> None:
        try:
            self.sent(kind)
        finally:
            if send is not None:
                send.release()

    def _peek(self, kind: str) -> Optional[tuple[bool, float]]:
        """(待ちの終わりが今より後か, 最短の送信時刻までの秒)。記録のロックを取れない・読めなければプロセスの中の記録で読む（I5）。"""
        held = self._record_lock()
        try:
            now = self.clock()
            record = None if held is None else self._load(now)
            record = self._memory_view(now) if record is None else self._with_unsaved(record, now)[0]
            return record["blocked_until"] > now, _earliest_send(record, kind) - now
        finally:
            if held is not None:
                held.release()

    def _apply(self, change: Callable[[dict[str, float], float], T]) -> T:
        """記録のロックの中で記録を変えて書く。使えなければプロセスの中の記録で変える（I5）。"""
        held = self._record_lock()
        if held is None:
            return self._in_process(change)
        try:
            now = self.clock()
            record = self._load(now)
            if record is None:
                return self._in_process(change)
            record, seen = self._with_unsaved(record, now)
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

    def _with_unsaved(self, record: dict[str, float], now: float) -> tuple[dict[str, float], int]:
        """記録へ書けていない変更があれば、プロセスの中の記録の時刻を重ねる（遅い方を採る）。重ねた時点の変更の回数も返す。"""
        with self._mutex:
            unsaved, seen, memory = self._unsaved, self._fallbacks, dict(self._memory)
        if unsaved:
            memory = _rewound(memory, now)
            for key in TIMES:
                record[key] = max(record[key], memory[key])
        return record, seen

    def _record_lock(self) -> Optional[FileLock]:
        held = slot_pool.lock(self.path.with_name(self.path.name + ".lock"))
        try:
            held.acquire(timeout=LOCK_SECONDS)
        except Timeout:
            diag(f"書き込みの順番の記録のロックを取れない: {self.path}", kind="write_gate")
            return None
        except OSError as exc:
            diag(f"書き込みの順番の記録を読めない: {self.path}: {exc}", kind="write_gate")
            return None
        return held

    def _send_lock(self) -> Optional[FileLock]:
        path = self.path.with_name(SEND_LOCK_FILE)
        held = slot_pool.lock(path)
        try:
            held.acquire(timeout=limits.value(SEND_LOCK, "lock"))
        except OSError:  # Timeout を含む
            diag(f"書き込みの送信のロックを取れない: {path}", kind="write_gate")
            return None
        return held

    def _load(self, now: float) -> Optional[dict[str, float]]:
        """記録を読む。無ければ空、壊れていれば 1 行を出して空。読めない（OSError）なら 1 行を出して None。"""
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
        return _rewound({k: float(raw[k]) for k in (*TIMES, "written_at")}, now)

    def _store(self, record: dict[str, float], now: float, seen: int) -> None:
        """記録を書き、プロセスの中の記録へ写す。`seen` の後にプロセスの中だけで変えた分は残し、未保存の印も消さない。"""
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
        """（`_mutex` の中で呼ぶ）記録の写し。`seen` の後にプロセスの中だけで変えた時刻があれば遅い方を採る。"""
        if self._fallbacks == seen:
            return dict(record)
        memory = _rewound(dict(self._memory), record["written_at"])
        return {**record, **{key: max(record[key], memory[key]) for key in TIMES}}
