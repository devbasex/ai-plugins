"""中継のラッパー: 擬似端末（ptyprocess）の下で claude を動かし、会話の切れ目で次の区間のセッションへ切り替える。

見回り 1 回ごとに区間の状態（シグナルファイル・子の生き死に・静かな秒・画面の上限と認証の失敗・文脈の量）を
集めて `switch_decide.decide` へ値で渡し、返った手を打つ。判断はすべて純粋な関数にあり、ここは端末と時計と
ファイルを読んで手を打つだけである（#2962）。

- 区間は `claude --session-id <新しい ID> <ブロック>` で始め、上限で替えるときと起こし直すときは
  `claude --resume <ID> <1 行>` で同じ会話を続ける
- 子へ入力を送る前（起動の引数で渡す 1 行を含む）に中継の印を立てる（`marks`。hook の数え手が数え直さない）
- 切り替えない理由は、前の見回りと変わったときだけ `log.jsonl` へ 1 行書く（同じ理由を見回りごとに書かない）
- 状態（`running`・`waiting`・`stalled`・`stopped`）と理由を `wrapper.json` に書き、一覧と耐久ワークフローが読む
  （認証の失敗で `/goal` が外れたレーンを running のまま扱わない。#3243）
"""

from __future__ import annotations

import os
import select
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, BinaryIO, Callable, Mapping, NamedTuple, Optional

from ptyprocess import PtyProcess

from ndf.adapters.claude import CLAUDE
from ndf.domains.relay import account_decide, accounts, hub_decide, marks, observe, registry, session_env, signals, switch_decide

# 画面の末尾として残すバイト数（上限と認証の失敗の行を探す範囲）
SCREEN_KEEP = 65536
READ_SIZE = 65536
DEFAULT_DIMS = (40, 120)


@dataclass(frozen=True)
class Settings:
    """ラッパー 1 本の設定。値は上限の宣言と環境変数から `cli` が組む。"""

    state_dir: Path
    version: str
    name: str
    cwd: Path
    environ: Mapping[str, str]
    args: tuple[str, ...]
    tick: float
    quiet: float
    stall: float
    max_starts: int
    context_limit: int
    usage_threshold: float
    transcript_bytes: int


@dataclass
class Section:
    """今の区間。`screen` は子の画面の末尾、`limit_at` は利用上限の行を見た時刻（壁時計）。"""

    number: int
    session_id: str
    account: Optional[str]
    child: Any
    screen: bytearray = field(default_factory=bytearray)
    last_output: float = 0.0
    handover_asked: bool = False
    limit_at: Optional[float] = None


class Ended(NamedTuple):
    """ラッパーの終わり。`cause` は止まった理由の種類（済んだときは空）。"""

    cause: str
    reason: str


def spawn_pty(argv: list[str], env: Mapping[str, str], cwd: Path) -> PtyProcess:
    try:
        size = os.get_terminal_size(sys.stdout.fileno())
        dims = (size.lines, size.columns)
    except OSError:  # 端末でなければ既定の大きさで起動する
        dims = DEFAULT_DIMS
    return PtyProcess.spawn(argv, cwd=str(cwd), env=dict(env), dimensions=dims)


class Wrapper:
    """`spawn`・`clock`・`out`・`stdin_fd` はテストで差し替える。"""

    def __init__(
        self,
        settings: Settings,
        spawn: Callable[[list[str], Mapping[str, str], Path], Any] = spawn_pty,
        clock: Callable[[], float] = time.monotonic,
        out: Optional[BinaryIO] = None,
        stdin_fd: Optional[int] = None,
    ):
        self.s = settings
        self.dir = registry.wrapper_dir(settings.state_dir, settings.name)
        self.spawn = spawn
        self.clock = clock
        self.out = out
        self.stdin_fd = stdin_fd
        self.section: Optional[Section] = None
        self.starts = {"date": date.today().isoformat(), "count": 0}
        self.last_reason: Optional[str] = None
        self.last_state: Optional[tuple[str, str, int]] = None

    def run(self, prompt: str) -> Ended:
        """錠を取り、版の混ざりを確かめてから区間を回す。"""
        self.dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.dir / registry.LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if not signals.lock(fd, 0):
                return Ended("refused", f"{self.s.name} のラッパーは既に動いている")
            others = [r for r in registry.all_running(self.s.state_dir, self.s.environ) if r.name != self.s.name]
            mix = hub_decide.mixed(self.s.version, others)
            if mix.kind != "go":
                return Ended("precondition", mix.reason)
            return self._loop(prompt)
        finally:
            os.close(fd)

    def _loop(self, prompt: str) -> Ended:
        self._start(1, str(uuid.uuid4()), accounts.chosen(self.s.environ, self.s.state_dir), prompt, resume=False)
        while True:
            self._pump(self.s.tick)
            try:
                step = switch_decide.decide(self._state())
            except (signals.SignalError, accounts.AccountError) as exc:
                self._stop_child()
                return self._end(Ended("config_broken", str(exc)))
            self._note(step)
            ended = self._act(step)
            if ended is not None:
                return self._end(ended)

    def _state(self) -> switch_decide.SectionState:
        sec = self._current()
        nxt = signals.read_next(self.dir)
        alive = sec.child.isalive()
        screen = observe.plain(bytes(sec.screen))
        transcript = observe.transcript_path(self.s.environ, self.s.cwd, sec.session_id)
        return switch_decide.SectionState(
            question=signals.has_question(self.dir),
            next_block=nxt["block"] if nxt else None,
            child_alive=alive,
            child_exit=None if alive else sec.child.exitstatus,
            starts_today=self._starts_today(),
            max_starts=self.s.max_starts,
            idle_seconds=self.clock() - sec.last_output,
            quiet_seconds=self.s.quiet,
            stall_seconds=self.s.stall,
            context_tokens=observe.context_tokens(transcript, self.s.transcript_bytes),
            context_limit=self.s.context_limit,
            handover_asked=sec.handover_asked,
            limit_pick=self._limit_pick(sec, screen),
            auth_failed=observe.auth_failed(screen),
        )

    def _limit_pick(self, sec: Section, screen: str) -> Optional[account_decide.Pick]:
        if observe.limit_line(screen) is None:
            return None
        if sec.limit_at is None:
            sec.limit_at = time.time()
        rows = accounts.usages(self.s.state_dir)
        cache = accounts.usage_path(self.s.state_dir)
        if not cache.is_file() or cache.stat().st_mtime < sec.limit_at:
            # 上限を見た後に測っていない使用率では、今のアカウントの上限が解けたと読まない
            rows = [r._replace(limited=True) if r.name == sec.account else r for r in rows]
        return account_decide.after_limit(sec.account, rows)

    def _act(self, step: switch_decide.Step) -> Optional[Ended]:
        sec = self._current()
        if step.kind in ("forward", "wait_reset", "stalled"):
            if step.kind == "forward":
                signals.drop(self.dir, signals.QUESTION_FILE)
            self._write_state("stalled" if step.kind == "stalled" else "waiting", step.reason)
            return None
        if step.kind == "switch":
            account = self._account_for_next(sec.account)
            signals.drop(self.dir, signals.NEXT_FILE)
            self._replace(sec.number + 1, str(uuid.uuid4()), account, step.prompt, resume=False)
            return None
        if step.kind in ("account", "restart"):
            self._count_start()
            self._replace(sec.number, sec.session_id, step.account or sec.account, step.prompt, resume=True)
            return None
        if step.kind == "handover":
            self._send(step.prompt)
            sec.handover_asked = True
            return None
        return self._final(step)

    def _final(self, step: switch_decide.Step) -> Optional[Ended]:
        if step.kind == "finish":
            return Ended("", step.reason)
        if step.kind == "stop":
            return Ended("work_stopped", step.reason)
        return None

    def _account_for_next(self, current: Optional[str]) -> Optional[str]:
        """カットポイントで使用率を見て、次の区間のアカウントを決める（#3336）。"""
        pick = account_decide.before_limit(current, accounts.usages(self.s.state_dir), self.s.usage_threshold)
        if pick.kind == "switch":
            self._log({"kind": "account", "reason": pick.reason})
        return pick.account

    def _start(self, number: int, session_id: str, account: Optional[str], prompt: str, resume: bool) -> None:
        env = {**session_env.child_env(self.s.environ, self.s.state_dir, account), **signals.dir_env(self.dir)}
        head = ["--resume", session_id] if resume else ["--session-id", session_id]
        argv = [CLAUDE, *head, *self.s.args, *([prompt] if prompt else [])]
        if prompt:
            marks.mark(self.s.state_dir, session_id)
        child = self.spawn(argv, env, self.s.cwd)
        self.section = Section(number, session_id, account, child, last_output=self.clock())
        self._write_state("running", f"区間 {number} を始めた")
        self._log({"kind": "section", "section": number, "session_id": session_id, "account": account, "resume": resume})

    def _replace(self, number: int, session_id: str, account: Optional[str], prompt: str, resume: bool) -> None:
        self._stop_child()
        self._start(number, session_id, account, prompt, resume)

    def _stop_child(self) -> None:
        sec = self.section
        if sec is not None and sec.child.isalive():
            sec.child.terminate(force=True)

    def _send(self, text: str) -> None:
        sec = self._current()
        marks.mark(self.s.state_dir, sec.session_id)
        sec.child.write(text.encode("utf-8") + b"\r")
        sec.screen.clear()

    def _pump(self, timeout: float) -> None:
        """子の画面を端末へ写し、端末の入力を子へ渡す。どちらも無ければ `timeout` 秒で返る。"""
        sec = self._current()
        fds = [sec.child.fd] + ([self.stdin_fd] if self.stdin_fd is not None else [])
        ready, _, _ = select.select(fds, [], [], timeout)
        if sec.child.fd in ready:
            self._from_child(sec)
        if self.stdin_fd is not None and self.stdin_fd in ready:
            data = os.read(self.stdin_fd, READ_SIZE)
            if data:
                sec.child.write(data)
                sec.screen.clear()
                sec.last_output = self.clock()

    def _from_child(self, sec: Section) -> None:
        try:
            data = os.read(sec.child.fd, READ_SIZE)
        except OSError:  # 子が終わると擬似端末は EIO を返す。生き死には isalive で読む
            data = b""
        if not data:
            return
        if self.out is not None:
            self.out.write(data)
            self.out.flush()
        sec.screen += data
        del sec.screen[:-SCREEN_KEEP]
        sec.last_output = self.clock()

    def _note(self, step: switch_decide.Step) -> None:
        if step.reason != self.last_reason:
            self._log({"kind": step.kind, "reason": step.reason})
            self.last_reason = step.reason

    def _starts_today(self) -> int:
        today = date.today().isoformat()
        if self.starts["date"] != today:
            self.starts = {"date": today, "count": 0}
        return int(self.starts["count"])

    def _count_start(self) -> None:
        self.starts = {"date": date.today().isoformat(), "count": self._starts_today() + 1}

    def _current(self) -> Section:
        if self.section is None:
            raise RuntimeError("区間がまだ始まっていない")
        return self.section

    def _write_state(self, state: str, reason: str) -> None:
        sec = self.section
        key = (state, reason, sec.number if sec else 0)
        if key == self.last_state:
            return
        self.last_state = key
        registry.write_state(self.dir, {
            "name": self.s.name, "version": self.s.version, "pid": os.getpid(), "cwd": str(self.s.cwd),
            "state": state, "reason": reason, "section": sec.number if sec else None,
            "session_id": sec.session_id if sec else None, "account": sec.account if sec else None, "starts": self.starts,
        })

    def _log(self, row: Mapping[str, Any]) -> None:
        registry.append_log(self.dir, {"section": self.section.number if self.section else None, **dict(row)})

    def _end(self, ended: Ended) -> Ended:
        self._write_state("stopped", ended.reason)
        self._log({"kind": "end", "cause": ended.cause, "reason": ended.reason})
        return ended
