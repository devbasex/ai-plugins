"""中継のラッパー: 擬似端末（ptyprocess）の下で claude を動かし、会話の切れ目で次のセグメントのセッションへ切り替える。

見回り 1 回ごとにセグメントの状態（シグナルファイル・子の生き死に・静かな秒・画面の上限と認証の失敗・文脈の量）を
集めて `switch_decide.decide` へ値で渡し、返った手を打つ。判断はすべて純粋な関数にあり、ここは端末と時計と
ファイルを読んで手を打つだけである（#2962）。

- セグメントは `claude --session-id <新しい ID> <ブロック>` で始め、上限で替えるときと再起動するときは
  `claude --resume <ID> <1 行>` で同じ会話を続ける
- 子へ入力を送る前（起動の引数で渡す 1 行を含む）に中継のマーカーを立てる（`markers`。hook の数え手が数え直さない）
- 切り替えない理由は、前の見回りと変わったときだけ `log.jsonl` へ 1 行書く（同じ理由を見回りごとに書かない）
- 状態（`running`・`waiting`・`stalled`・`stopped`）と理由を `wrapper.json` に書き、一覧と耐久ワークフローが読む
  （認証の失敗で `/goal` が外れたレーンを running のまま扱わない。#3243）
- 計画のレーンの中継（設定の `ask_plan` がある）は、hook が書いた質問を計画のステートマシンのリクエストとして載せる
  （`lane_question`。#3035）。載せられなければ理由を状態と `log.jsonl` に書く
- 質問のファイルは質問が閉じるまで残し（消すのは hook）、1 つの質問は 1 回だけ転送する。ラッパーが消すのは、子が終わった
  ときと、質問が今の子のものでないときだけである（#3814）
"""

from __future__ import annotations

import functools
import os
import select
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, BinaryIO, Callable, ClassVar, Mapping, NamedTuple, Optional

from ptyprocess import PtyProcess

from ndf import identity
from ndf.adapters.claude import CLAUDE
from ndf.domains.relay import (
    account_decide,
    accounts,
    hub_decide,
    install,
    markers,
    observe,
    plugin_update,
    registry,
    session_env,
    signals,
    stop_signal,
    switch_decide,
)
from ndf.domains.relay.lane_question import AskPlan

# 画面の末尾として残すバイト数（上限と認証の失敗の行を探す範囲）
SCREEN_KEEP = 65536
READ_SIZE = 65536
DEFAULT_DIMS = (40, 120)
# 停止シグナルファイル（`ndf relay stop --all`）があるときに打たない手（セグメントを替える・再起動する・引継ぎを頼む）
STOP_HELD = frozenset({"switch", "account", "restart", "handover"})
STOP_REASON = "停止シグナルファイルがある"
# プラグインの更新（#3812）。端末の 1 行は子が居ない間に出す
UPDATE_FAILED_LINE = "ndf-relay: プラグインを更新できなかった（{reason}）。{launched} で次のセグメントを起動する"
UNKNOWN_VERSION = "不明なバージョン"
MAJOR_CHANGED_LINE = "ndf-relay: メジャーバージョンが {own} から {launched} に変わった。次のセグメントを起動しない。claude を打ち直す"
MAJOR_CHANGED_INSTALL_LINE = (
    "ndf-relay: メジャーバージョンが {own} から {launched} に変わった。次のセグメントを起動しない。"
    f"{{root}}/bin/{identity.FINAL_NAME} relay install を打ってから claude を打ち直す"
)
NO_UPDATE_REASON = "同じ会話を続ける手では更新しない"
# 停止で次のセグメントを保留したときに一度だけ端末へ出す 1 行（カットポイントの案内「ndf-relay: の 1 行が出たら従う」が待つ行）
HALT_LINE = f"ndf-relay: 次のセグメントを起動しない（{STOP_REASON}）。このまま続けるか、/exit する"
# 子の claude を起動できないときの停止理由の文（#3842）。`_ended` が `ndf-relay:` を前に付けて端末へ出す
LAUNCH_FAILED = "子の claude を起動できない（{why}）"
NO_CWD = "作業ディレクトリ {cwd} が無い"
NO_ENTER = "作業ディレクトリ {cwd} に入れない: {why}"
# 計画のリクエストへ渡す質問のコピー（質問のファイルは hook が消すまで残すので、渡すのはこの写し。#3814）
FORWARD_FILE = ".question.forward.json"


@dataclass(frozen=True)
class Settings:
    """ラッパー 1 本の設定。値は上限の設定と環境変数から `cli` が組む。

    `args` はすべてのセッションに付ける引数、`first_args` は最初のセグメントだけに付ける引数（`claude_args`）。
    `first_session` は最初のセグメントのセッション ID（`first_resume` が真なら `--resume` で続ける。無ければ新しい ID）。
    `ask_plan` は計画のレーンの質問を計画のリクエストとして載せる手（`lane_question.plan_asker`。計画のレーンでなければ None）。
    `update` は新しいセッションで始める前にプラグインを更新する手、`confirm` は同じ会話を続ける前に起動先を確かめる手
    （`plugin_update.Updater`。None なら呼ばない。#3812）。
    `cwd_lost` は設定を作るときに作業ディレクトリを読めなかったときの表示の文字列（None でなければ起動しない。#3842）。
    """

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
    ask_plan: Optional[AskPlan] = None
    first_args: tuple[str, ...] = ()
    first_session: Optional[str] = None
    first_resume: bool = False
    update: Optional[Callable[[], plugin_update.Updated]] = None
    confirm: Optional[Callable[[], plugin_update.Target]] = None
    cwd_lost: Optional[str] = None


@dataclass(frozen=True)
class Launch:
    """セグメントを始める要求。`resume` が真なら `--resume` で同じ会話を続け、偽なら `--session-id` で新しく始める。"""

    number: int
    session_id: str
    account: Optional[str]
    prompt: str
    resume: bool


@dataclass
class Section:
    """今のセグメント。`screen` は子の画面の末尾、`limit_at` は利用上限の行を見た時刻（壁時計）。

    `eof` は擬似端末が終わりを返したマーカー（子が終わった後は読める状態が続くため、見回りの待ちから外す）。
    `launch` はこの子の起動の ID（`NDF_RELAY_LAUNCH`。#3814）。`pending` はこの見回りで読んだ回答待ちの質問、
    `forwarded_question` は転送した質問の ID、`broken` は壊れた質問のファイルの行（`question_broken`）を書いたか。
    """

    number: int
    session_id: str
    account: Optional[str]
    child: Any
    launch: str
    screen: bytearray = field(default_factory=bytearray)
    last_output: float = 0.0
    handover_asked: bool = False
    limit_at: Optional[float] = None
    eof: bool = False
    pending: Optional[dict[str, Any]] = None
    broken: bool = False
    forwarded_question: Optional[str] = None


@dataclass(frozen=True)
class WallClock:
    """ラッパーが読む日付（今日起動し直した回数の日の境）と壁時計（使用率のキャッシュの時刻と比べる）。"""

    today: Callable[[], date] = date.today
    now: Callable[[], float] = time.time


class Ended(NamedTuple):
    """ラッパーの終わり。`cause` は停止理由の種類（済んだときは空）。"""

    cause: str
    reason: str


def _enter(cwd: Path) -> None:
    """fork した子で作業ディレクトリへ入る（`preexec_fn`）。入れなければ同じ型の例外を `NO_ENTER` の文で上げ直す。

    ptyprocess は `preexec_fn` の例外を型と文のまま親の `spawn` で上げるので、親は `OSError` として受けられる。
    """
    try:
        os.chdir(cwd)
    except OSError as exc:
        raise type(exc)(NO_ENTER.format(cwd=cwd, why=exc.strerror)) from exc


def spawn_pty(argv: list[str], env: Mapping[str, str], cwd: Path) -> PtyProcess:
    """擬似端末で子を起動する。作業ディレクトリへは子の `preexec_fn` で入る（ptyprocess の `cwd` は入れないと子で例外のまま終わる）。"""
    try:
        size = os.get_terminal_size(sys.stdout.fileno())
        dims = (size.lines, size.columns)
    except OSError:  # 端末でなければ既定の大きさで起動する
        dims = DEFAULT_DIMS
    return PtyProcess.spawn(argv, env=dict(env), dimensions=dims, preexec_fn=functools.partial(_enter, cwd))


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
        """錠を取り、バージョンの混ざりを確かめてからセグメントを回す。"""
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
        stop_signal.drop_stop(self.dir)  # 前の実行が残したシグナルファイルで、始めたばかりの実行を止めない
        halt_shown = False
        first = self.s.first_session or str(uuid.uuid4())
        # 利用者が --resume で続けた会話の途中では更新しない（I3）
        ended = self._before_start(update=not self.s.first_resume)
        if ended is None:
            ended = self._start(Launch(1, first, accounts.chosen(self.s.environ, self.s.state_dir), prompt, resume=self.s.first_resume))
        if ended is not None:
            return self._end(ended)
        while True:
            self._pump(self.s.tick)
            try:
                step = switch_decide.decide(self._state())
            except (signals.SignalError, accounts.AccountError) as exc:
                self._stop_child()
                return self._end(Ended("config_broken", str(exc)))
            self._note(step)
            if step.kind in STOP_HELD and stop_signal.has_stop(self.dir):
                if self._current().child.isalive():
                    # 停止シグナルファイルの後は、今のセッションへ入力を送らず・替えず、利用者が終えるのを待つ
                    halt_shown = halt_shown or self._show_halt()
                    continue
                stop_signal.drop_stop(self.dir)
                return self._end(Ended("", STOP_REASON))
            ended = self._act(step)
            if ended is not None:
                return self._end(ended)

    def _state(self) -> switch_decide.SectionState:
        sec = self._current()
        nxt = self._next_mark(sec)
        alive = sec.child.isalive()
        question, forwarded, qid = self._question(sec, alive)
        screen = observe.plain(bytes(sec.screen))
        transcript = observe.transcript_path(self.s.environ, self.s.cwd, sec.session_id)
        return switch_decide.SectionState(
            question=question,
            question_forwarded=forwarded,
            question_id=qid,
            next_block=nxt["block"] if nxt else None,
            child_alive=alive,
            child_exit=None if alive else sec.child.exitstatus,
            starts_today=self._starts_today(),
            max_starts=self.s.max_starts,
            idle_seconds=self.clock() - sec.last_output,
            next_age_seconds=self._age(nxt["first_written_at"]) if nxt else 0.0,
            quiet_seconds=self.s.quiet,
            stall_seconds=self.s.stall,
            context_tokens=observe.context_tokens(transcript, self.s.transcript_bytes),
            context_limit=self.s.context_limit,
            handover_asked=sec.handover_asked,
            limit_pick=self._limit_pick(sec, screen),
            auth_failed=observe.auth_failed(screen),
        )

    def _question(self, sec: Section, alive: bool) -> tuple[bool, bool, Optional[str]]:
        """質問のファイルを読み、（回答待ちか, 転送したか, 質問の ID）を返す。今の子のものでない質問は消す（I3）。

        壊れた・読めないファイルは回答待ちとして扱い、転送しない（決定 5）。消せなかった質問はこの見回りでは無いとして返し、
        子を止める側の確かめ（質問のファイルが在れば止めない）に任せる。
        """
        sec.pending = None
        try:
            question = signals.read_question(self.dir)
        except signals.SignalError as exc:
            if not alive:
                signals.drop_question(self.dir, None, 0)
                return False, False, None
            if not sec.broken:
                self._log({"kind": "question_broken", "reason": str(exc)})
                sec.broken = True
            return True, True, None
        if question is None:
            sec.forwarded_question, sec.broken = None, False
            return False, False, None
        if not alive:
            signals.drop_question(self.dir, None, 0)
            return False, False, None
        stale = self._stale(sec, question)
        if stale is not None:
            match = {key: question.get(key) for key in ("tool_use_id", "at", "launch")}
            if signals.drop_question(self.dir, match, 0) == "dropped":
                self._log({"kind": "stale_question", "reason": stale})
            return False, False, None
        sec.pending = question
        return True, sec.forwarded_question == self._forward_key(question), signals.question_id(question)

    @staticmethod
    def _forward_key(question: Mapping[str, Any]) -> str:
        """転送した質問として残す値。ID の無い質問も 1 回だけ転送するよう、ID が無ければ空の文字列にする（I2）。"""
        qid = signals.question_id(question)
        return "" if qid is None else qid

    @staticmethod
    def _stale(sec: Section, question: Mapping[str, Any]) -> Optional[str]:
        """質問が今の子のものでなければ理由、今の子のものなら None。起動の ID が無ければセッションで比べる。"""
        launch = question.get("launch")
        if isinstance(launch, str):
            return None if launch == sec.launch else f"起動 {launch} の質問は今の子のものでない"
        session = question.get("session_id")
        return None if session == sec.session_id else f"セッション {session} の質問は今の子のものでない"

    def _next_mark(self, sec: Section) -> Optional[dict[str, Any]]:
        """今のセグメントのセッションが書いた次のセグメントのマーカー。前の実行のマーカー（セッションが違う）は消して読まない。"""
        nxt = signals.read_next(self.dir)
        if nxt is not None and nxt.get("session_id") != sec.session_id:
            signals.drop(self.dir, signals.NEXT_FILE)
            self._log({"kind": "stale_next", "reason": f"セッション {nxt.get('session_id')} のマーカーは今のセグメントのものでない"})
            return None
        return nxt

    def _age(self, written_at: str) -> float:
        """ISO の時刻から今（壁時計）までの秒。読めない時刻は `SignalError`（今の時刻へ倒さない）。"""
        try:
            return max(0.0, self.wall.now() - datetime.fromisoformat(written_at).timestamp())
        except ValueError as exc:
            raise signals.SignalError(f"次のセグメントのマーカーの first_written_at を読めない: {written_at}") from exc

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
        reason = step.reason
        if step.kind == "forward" and sec.pending is not None:
            # 質問のファイルは消さない（質問が閉じたら hook が消す）。同じ質問を転送し直さないよう ID を残す（I2）
            sec.forwarded_question = self._forward_key(sec.pending)
            note = self._ask_plan(sec.pending)
            reason = f"{reason}。計画のリクエストへ載せられない: {note}" if note else reason
        self._write_state("stalled" if step.kind == "stalled" else "waiting", reason)
        return None

    def _ask_plan(self, question: Mapping[str, Any]) -> str:
        """計画のレーンなら質問の写しを計画のリクエストとして載せ、載せられなかった理由を返す（載せた・計画のレーンでなければ空）。"""
        if self.s.ask_plan is None:
            return ""
        copy = self.dir / FORWARD_FILE
        signals.replace_json(copy, question)
        try:
            note = self.s.ask_plan(copy)
        finally:
            copy.unlink(missing_ok=True)
        self._log({"kind": "plan_question", "ok": not note, "reason": note or "質問を計画のリクエストへ載せた"})
        return note

    def _on_switch(self, step: switch_decide.Step, sec: Section) -> Optional[Ended]:
        account = self._account_for_next(sec.account)
        signals.drop(self.dir, signals.NEXT_FILE)
        self._stop_child()
        ended = self._before_start(update=True)  # 次の子は更新の後に起動する（I1）
        if ended is None:
            ended = self._start(Launch(sec.number + 1, str(uuid.uuid4()), account, step.prompt, resume=False))
        return ended

    def _on_resume(self, step: switch_decide.Step, sec: Section) -> Optional[Ended]:
        self._count_start()
        self._stop_child()
        ended = self._before_start(update=False)  # 同じ会話を続ける手は更新しない（I3）
        if ended is None:
            ended = self._start(Launch(sec.number, sec.session_id, step.account or sec.account, step.prompt, resume=True))
        return ended

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
        """カットポイントで使用率を見て、次のセグメントのアカウントを決める（#3336）。"""
        pick = account_decide.before_limit(current, accounts.usages(self.s.state_dir), self.s.usage_threshold)
        if pick.kind == "switch":
            self._log({"kind": "account", "reason": pick.reason})
        return pick.account

    def _start(self, req: Launch) -> Optional[Ended]:
        """子を起動して None を返す。起動できなければ置いたマーカーを消し、停止理由を返す（記録は `_loop` の `_end` が書く）。

        起動ごとに起動の ID を作り、`spawn` の前に今の起動として記録して子へ渡す。起動できなければ記録を外す（#3814）。
        """
        launch = uuid.uuid4().hex
        env = {**session_env.child_env(self.s.environ, self.s.state_dir, req.account), **signals.launch_env(self.dir, launch)}
        head = ["--resume", req.session_id] if req.resume else ["--session-id", req.session_id]
        missing = self._cwd_missing()  # `_before_start` の後に消えたときのために、マーカーを置く前にも確かめる
        if missing is not None:
            return missing
        once = self.s.first_args if req.number == 1 else ()
        argv = [CLAUDE, *head, *self.s.args, *once, *([req.prompt] if req.prompt else [])]
        if req.prompt:
            markers.mark(self.s.state_dir, req.session_id)
        signals.set_launch(self.dir, launch)
        try:
            child = self.spawn(argv, env, self.s.cwd)
        except OSError as exc:
            # 起動しなかった子の hook は動かないので、外すのにロックは要らない
            signals.set_launch(self.dir, None)
            if req.prompt:
                markers.take(self.s.state_dir, req.session_id)
            return Ended("precondition", LAUNCH_FAILED.format(why=exc))
        self.section = Section(req.number, req.session_id, req.account, child, launch, last_output=self.clock())
        self._write_state("running", f"セグメント {req.number} を始めた")
        log = {"kind": "section", "section": req.number, "session_id": req.session_id, "account": req.account, "resume": req.resume}
        self._log(log)
        return None

    def _before_start(self, update: bool) -> Optional[Ended]:
        """子を起動する直前の準備。止めるなら `Ended`、起動してよければ None を返す。

        `update` が真なら更新の手、偽なら確認の手を呼んで記録する（I1・I3）。起動先のメジャーバージョンが違えば止め（I7）、
        更新や確認を待つ間に停止シグナルファイルが置かれていれば止める。更新の失敗の 1 行は、次の子を実際に起動するときだけ出す（I2）。
        作業ディレクトリが無ければ、更新（作業ディレクトリでコマンドを打つ）も確認もせずに止める（#3842）。
        """
        missing = self._cwd_missing()
        if missing is not None:
            return missing
        got = self._update_plugin() if update else self._confirm_plugin()
        ended = self._stop_if_major_changed(got.target) if got is not None else None
        ended = ended or self._stop_requested()
        if ended is None and got is not None and got.result == "failed":
            self._say(UPDATE_FAILED_LINE.format(reason=got.reason, launched=got.target.version or UNKNOWN_VERSION))
        return ended

    def _cwd_missing(self) -> Optional[Ended]:
        """作業ディレクトリが絶対パスのディレクトリとして在れば None、無ければ停止理由を返す。

        設定を作るときに読めなかった（`cwd_lost`）なら、同じパスのディレクトリが在っても止める。
        """
        if self.s.cwd_lost is None and self.s.cwd.is_absolute() and self.s.cwd.is_dir():
            return None
        cwd = self.s.cwd if self.s.cwd_lost is None else self.s.cwd_lost
        return Ended("precondition", LAUNCH_FAILED.format(why=NO_CWD.format(cwd=cwd)))

    def _update_plugin(self) -> Optional[plugin_update.Updated]:
        """更新の手を呼んで記録し、結果を返す。手が無ければ None。"""
        if self.s.update is None:
            return None
        began = self.clock()
        got = self.s.update()
        self._log_update(got, began)
        return got

    def _confirm_plugin(self) -> Optional[plugin_update.Updated]:
        """確認の手で起動先を確かめて記録し、更新しなかった結果として返す。手が無ければ None。"""
        if self.s.confirm is None:
            return None
        began = self.clock()
        got = plugin_update.Updated("skipped", None, None, NO_UPDATE_REASON, self.s.confirm())
        self._log_update(got, began)
        return got

    def _stop_requested(self) -> Optional[Ended]:
        """停止シグナルファイルを読み直し、あれば消して止まる（子を止めた後の待ちの間に `ndf relay stop --all` が置いた分）。"""
        if not stop_signal.has_stop(self.dir):
            return None
        stop_signal.drop_stop(self.dir)
        return Ended("", STOP_REASON)

    def _stop_if_major_changed(self, target: plugin_update.Target) -> Optional[Ended]:
        """起動先のメジャーバージョンが自身と違えば止め、同じなら None を返す（I7）。"""
        return self._stop_for_major(target) if plugin_update.major_changed(self.s.version, target) else None

    def _log_update(self, got: plugin_update.Updated, began: float) -> None:
        self._log(
            {
                "kind": "plugin_update",
                "result": got.result,
                "from": got.before,
                "to": got.after,
                "launched": got.target.version,
                "reason": got.reason,
                "seconds": round(self.clock() - began, 1),
            }
        )

    def _stop_for_major(self, target: plugin_update.Target) -> Ended:
        """`relay.root` を起動先へ向け直し、向け直せたかで案内の 1 行を選んで、次の子を起動せずに止まる（I7）。"""
        launched, root = target.version, target.root
        if launched is None or root is None:
            raise ValueError("起動先が分からないまま止めない（I8）")
        try:
            pointed = install.follow(self.s.environ, root, launched) is not None
            pointed = pointed or install.points_at(self.s.environ, launched)
        except OSError:  # 向け直せなければ relay install を案内する
            pointed = False
        if pointed:
            line = MAJOR_CHANGED_LINE.format(own=self.s.version, launched=launched)
        else:
            line = MAJOR_CHANGED_INSTALL_LINE.format(own=self.s.version, launched=launched, root=root)
        self._say(line)
        return Ended("precondition", line)

    def _say(self, line: str) -> None:
        if self.out is not None:
            self.out.write(f"\r\n{line}\r\n".encode())
            self.out.flush()

    def _stop_child(self) -> None:
        sec = self.section
        if sec is not None and sec.child.isalive():
            sec.child.terminate(force=True)

    def _send(self, text: str) -> None:
        sec = self._current()
        markers.mark(self.s.state_dir, sec.session_id)
        sec.child.write(text.encode("utf-8") + b"\r")
        sec.screen.clear()

    def _pump(self, timeout: float) -> None:
        """子の画面を端末へ写し、端末の入力を子へ渡す。どちらも無ければ `timeout` 秒で返る。"""
        sec = self._current()
        child_fds = [] if sec.eof else [sec.child.fd]
        fds = child_fds + ([self.stdin_fd] if self.stdin_fd is not None else [])
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
            sec.eof = True  # 読める状態が続くので、次の見回りからは `tick` 秒を眠って待つ
            return
        if self.out is not None:
            self.out.write(data)
            self.out.flush()
        sec.screen += data
        del sec.screen[:-SCREEN_KEEP]
        sec.last_output = self.clock()

    def _show_halt(self) -> bool:
        """次のセグメントを保留したことを端末へ 1 行で知らせる（利用者が /exit するかを決められるように）。出したら真。"""
        self._log({"kind": "halt", "reason": STOP_REASON})
        if self.out is not None:
            self.out.write(f"\r\n{HALT_LINE}\r\n".encode())
            self.out.flush()
        return True

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
            raise RuntimeError("セグメントがまだ始まっていない")
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
