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
from typing import Any, BinaryIO, Callable, ClassVar, Mapping, NamedTuple, Optional

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


@dataclass(frozen=True)
class Launch:
    """区間を始める要求。`resume` が真なら `--resume` で同じ会話を続け、偽なら `--session-id` で新しく始める。"""

    number: int
    session_id: str
    account: Optional[str]
    prompt: str
    resume: bool


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


@dataclass(frozen=True)
class WallClock:
    """ラッパーが読む日付（今日起動し直した回数の日の境）と壁時計（使用率のキャッシュの時刻と比べる）。"""

    today: Callable[[], date] = date.today
    now: Callable[[], float] = time.time


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
    """`spawn`・`clock`・`out`・`stdin_fd`・`wall`（日付と壁時計）はテストで差し替える。"""

    def __init__(
        self,
        settings: Settings,
        spawn: Callable[[list[str], Mapping[str, str], Path], Any] = spawn_pty,
        clock: Callable[[], float] = time.monotonic,
        out: Optional[BinaryIO] = None,
        stdin_fd: Optional[int] = None,
        wall: Optional[WallClock] = None,
    ):
        self.s = settings
        self.dir = registry.wrapper_dir(settings.state_dir, settings.name)
        self.spawn = spawn
        self.clock = clock
        self.out = out
        self.stdin_fd = stdin_fd
        self.wall = wall or WallClock()
        self.section: Optional[Section] = None
        self.starts = registry.Starts.today(None, self.wall.today).as_json()
        self.last_reason: Optional[str] = None
        self.last_state: Optional[tuple[str, str, int]] = None

    def run(self, prompt: str) -> Ended:
        """錠を取り、版の混ざりを確かめてから区間を回す。"""
        self.dir.mkdir(parents=True, exist_ok=True)
        with signals.held(self.dir / registry.LOCK_FILE, 0) as got:
            if not got:
                return Ended("refused", f"{self.s.name} のラッパーは既に動いている")
            others = [r for r in registry.all_running(self.s.state_dir, self.s.environ) if r.name != self.s.name]
            mix = hub_decide.mixed(self.s.version, others)
            if not mix.ok:
                return Ended("precondition", mix.reason)
            return self._loop(prompt)

    def _loop(self, prompt: str) -> Ended:
        self._start(Launch(1, str(uuid.uuid4()), accounts.chosen(self.s.environ, self.s.state_dir), prompt, resume=False))
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
            sec.limit_at = self.wall.now()
        rows = accounts.usages(self.s.state_dir)
        cache = accounts.usage_path(self.s.state_dir)
        if not cache.is_file() or cache.stat().st_mtime < sec.limit_at:
            # 上限を見た後に測っていない使用率では、今のアカウントの上限が解けたと読まない
            rows = [r._replace(limited=True) if r.name == sec.account else r for r in rows]
        return account_decide.after_limit(sec.account, rows)

    def _act(self, step: switch_decide.Step) -> Optional[Ended]:
        """手の種類に応じた動きを打つ。表に無い種類（`wait`）は何もしない。終わるときは `Ended` を返す。"""
        sec = self._current()
        action = self._ACTIONS.get(step.kind)
        return action(self, step, sec) if action is not None else None

    def _on_wait_like(self, step: switch_decide.Step, sec: Section) -> Optional[Ended]:
        if step.kind == "forward":
            signals.drop(self.dir, signals.QUESTION_FILE)
        self._write_state("stalled" if step.kind == "stalled" else "waiting", step.reason)
        return None

    def _on_switch(self, step: switch_decide.Step, sec: Section) -> Optional[Ended]:
        account = self._account_for_next(sec.account)
        signals.drop(self.dir, signals.NEXT_FILE)
        self._replace(Launch(sec.number + 1, str(uuid.uuid4()), account, step.prompt, resume=False))
        return None

    def _on_resume(self, step: switch_decide.Step, sec: Section) -> Optional[Ended]:
        self._count_start()
        self._replace(Launch(sec.number, sec.session_id, step.account or sec.account, step.prompt, resume=True))
        return None

    def _on_handover(self, step: switch_decide.Step, sec: Section) -> Optional[Ended]:
        self._send(step.prompt)
        sec.handover_asked = True
        return None

    def _on_finish(self, step: switch_decide.Step, sec: Section) -> Optional[Ended]:
        return Ended("", step.reason)

    def _on_stop(self, step: switch_decide.Step, sec: Section) -> Optional[Ended]:
        return Ended("work_stopped", step.reason)

    # 手の種類 → 動き（`switch_decide.Step` の kind。`wait` は載せない）
    _ACTIONS: ClassVar[dict[str, Callable[[Wrapper, switch_decide.Step, Section], Optional[Ended]]]] = {
        "forward": _on_wait_like,
        "wait_reset": _on_wait_like,
        "stalled": _on_wait_like,
        "switch": _on_switch,
        "account": _on_resume,
        "restart": _on_resume,
        "handover": _on_handover,
        "finish": _on_finish,
        "stop": _on_stop,
    }

    def _account_for_next(self, current: Optional[str]) -> Optional[str]:
        """カットポイントで使用率を見て、次の区間のアカウントを決める（#3336）。"""
        pick = account_decide.before_limit(current, accounts.usages(self.s.state_dir), self.s.usage_threshold)
        if pick.kind == "switch":
            self._log({"kind": "account", "reason": pick.reason})
        return pick.account

    def _start(self, req: Launch) -> None:
        env = {**session_env.child_env(self.s.environ, self.s.state_dir, req.account), **signals.dir_env(self.dir)}
        head = ["--resume", req.session_id] if req.resume else ["--session-id", req.session_id]
        argv = [CLAUDE, *head, *self.s.args, *([req.prompt] if req.prompt else [])]
        if req.prompt:
            marks.mark(self.s.state_dir, req.session_id)
        child = self.spawn(argv, env, self.s.cwd)
        self.section = Section(req.number, req.session_id, req.account, child, last_output=self.clock())
        self._write_state("running", f"区間 {req.number} を始めた")
        log = {"kind": "section", "section": req.number, "session_id": req.session_id, "account": req.account, "resume": req.resume}
        self._log(log)

    def _replace(self, req: Launch) -> None:
        self._stop_child()
        self._start(req)

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
        starts = registry.Starts.today(self.starts, self.wall.today)
        self.starts = starts.as_json()
        return starts.count

    def _count_start(self) -> None:
        self.starts = registry.Starts.today(self.starts, self.wall.today).bumped().as_json()

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
        registry.write_state(
            self.dir,
            {
                "name": self.s.name,
                "version": self.s.version,
                "pid": os.getpid(),
                "cwd": str(self.s.cwd),
                "state": state,
                "reason": reason,
                "section": sec.number if sec else None,
                "session_id": sec.session_id if sec else None,
                "account": sec.account if sec else None,
                "starts": self.starts,
            },
        )

    def _log(self, row: Mapping[str, Any]) -> None:
        registry.append_log(self.dir, {"section": self.section.number if self.section else None, **dict(row)})

    def _end(self, ended: Ended) -> Ended:
        self._write_state("stopped", ended.reason)
        self._log({"kind": "end", "cause": ended.cause, "reason": ended.reason})
        return ended
