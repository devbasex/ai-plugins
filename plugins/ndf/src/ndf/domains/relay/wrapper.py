"""中継のラッパー: 擬似端末（ptyprocess）の下で claude を動かし、会話の切れ目で次のセグメントのセッションへ切り替える。

見回り 1 回ごとにセグメントの状態（シグナルファイル・子の生き死に・静かな秒・画面の上限と認証の失敗・文脈の量）を
集めて `switch_decide.decide` へ値で渡し、返った手を打つ。判断はすべて純粋な関数にあり、ここは端末と時計と
ファイルを読んで手を打つだけである（#2962）。

- セグメントは `claude --session-id <新しい ID> <ブロック>` で始め、上限で替えるとき・再起動するとき・引継ぎを頼むときは
  `claude --resume <ID> <1 行>` で同じ会話を続ける。子の端末へ書くのは利用者の入力を渡すときだけである（#3814）
- 子へ入力を送る前（起動の引数で渡す 1 行を含む）に中継のマーカーを立てる（`markers`。hook の数え手が数え直さない）
- 切り替えない理由は、前の見回りと変わったときだけ `log.jsonl` へ 1 行書く（同じ理由を見回りごとに書かない）
- 状態（`running`・`waiting`・`stalled`・`stopped`）と理由を `wrapper.json` に書き、一覧と耐久ワークフローが読む
  （認証の失敗で `/goal` が外れたレーンを running のまま扱わない。#3243）
- 起動の回数は数えない。起動から間もないセグメントが続いたら（再起動ループ）次のセグメントを起動せず、子が動いていれば
  中継を続ける（`relay.spin`。#3864 の決定 13）
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
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, Callable, ClassVar, Mapping, Optional

from ptyprocess import PtyProcess

from ndf import identity
from ndf.adapters import cli_floor
from ndf.adapters.claude import CLAUDE
from ndf.contracts import limits
from ndf.domains.relay import (
    account_decide,
    account_registry,
    accounts,
    hub_decide,
    install,
    launch_inputs,
    launch_target,
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
from ndf.domains.relay.launch_target import Choice, Target
from ndf.domains.relay.wrapper_launch import AGAIN_RESTART, CHILD_ALIVE, LAUNCH_FAILED, STOP_REASON, Ended, Halt, Launched, LaunchSteps

# 画面の末尾として残すバイト数（上限と認証の失敗の行を探す範囲）
SCREEN_KEEP = 65536
READ_SIZE = 65536
DEFAULT_DIMS = (40, 120)
# 停止シグナルファイル（`ndf relay stop --all`）があるときに打たない手（セグメントを替える・再起動する・引継ぎを頼む）
STOP_HELD = frozenset({"switch", "account", "restart", "handover"})
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
NO_CWD = "作業ディレクトリ {cwd} が無い"
NO_ENTER = "作業ディレクトリ {cwd} に入れない: {why}"
# 計画のリクエストへ渡す質問のコピー（質問のファイルは hook が消すまで残すので、渡すのはこの写し。#3814）
FORWARD_FILE = ".question.forward.json"
# 質問の hook と同じロック。ラッパーはこれを持ったまま質問が無いと確かめて子を止める（#3814 の I4）
QUESTION_LOCK = f"{signals.QUESTION_FILE}.lock"
HALT_BROKEN = "質問が閉じるか子の終わりを待ってから止める"
# 再起動ループの検出に当たり、子が動いている間に一度だけ端末へ出す 1 行（#3864 の決定 13）
# `again` は端末のラッパーなら AGAIN_RUN、名前つきのラッパー（NDF_RELAY_SPAWNED）なら AGAIN_RESTART（起動引数の中身は出さない）
SPIN_LINE = (
    "ndf-relay: 起動から {seconds:g} 秒未満で次のセグメントへ移ったセグメントが {count} つ続いた（再起動ループ）。"
    "次のセグメントを起動しない。原因を直してから{again}"
)
AGAIN_RUN = " /exit し、ndf relay run で起動し直す"
# 次のセグメントを起動する手（セグメントの終わりの時刻を書く）
LAUNCHES = frozenset({"switch", "account", "restart", "handover"})


@dataclass(frozen=True)
class Settings:
    """ラッパー 1 本の設定。値は上限の設定と環境変数から `cli` が組む。

    `args` はすべてのセッションに付ける引数、`first_args` は最初のセグメントだけに付ける引数（`claude_args`）。
    `first_session` は最初のセグメントのセッション ID（`first_resume` が真なら `--resume` で続ける。無ければ新しい ID）。
    `ask_plan` は計画のレーンの質問を計画のリクエストとして載せる手（`lane_question.plan_asker`。計画のレーンでなければ None）。
    `update` は新しいセッションで始める前にプラグインを更新する手、`confirm` は同じ会話を続ける前に起動先を確かめる手
    （`plugin_update.Updater`。None なら呼ばない。#3812）。
    `cwd_lost` は設定を作るときに作業ディレクトリを読めなかったときの表示の文字列（None でなければ起動しない。#3842）。
    `spin` は再起動ループの検出の閾値（`relay.spin`。#3864 の決定 13）。
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
    spin: switch_decide.Spin
    context_limit: int
    guards: account_decide.Guards
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
    """セグメントを始める要求。`resume` が真なら `--resume` で同じ会話を続け、偽なら `--session-id` で新しく始める。

    `choice` は試す起動先、`reason` は起動の理由。`kind` は前のセグメントを終えた手（`switch`・`account`・`restart`・
    `handover`。最初のセグメントは None）。
    """

    number: int
    session_id: str
    choice: Choice
    prompt: str
    resume: bool
    kind: Optional[str] = None
    reason: str = "first"


@dataclass
class Section:
    """今のセグメント。`screen` は子の画面の末尾、`limit_at` は利用上限の行を見た時刻（壁時計）。

    `eof` は擬似端末が終わりを返したマーカー（子が終わった後は読める状態が続くため、見回りの待ちから外す）。
    `launch` はこの子の起動の ID（`NDF_RELAY_LAUNCH`。#3814）。`pending` はこの見回りで読んだ回答待ちの質問、
    `forwarded_question` は転送した質問の ID、`broken` は壊れた質問のファイルの行（`question_broken`）を書いたか。
    `started_at`・`ended_at` は子を起動した時刻と、次のセグメントを起動する手を初めて決めた時刻（どちらも `clock`）。
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
    started_at: float = 0.0
    ended_at: Optional[float] = None
    target: Optional[Target] = None
    settled: bool = False


@dataclass(frozen=True)
class WallClock:
    """ラッパーが読む壁時計（使用率のキャッシュの時刻と比べる）。"""

    now: Callable[[], float] = time.time


def _enter(cwd: Path, keep: tuple[int, ...] = ()) -> None:
    """fork した子で作業ディレクトリへ入る（`preexec_fn`）。入れなければ同じ型の例外を `NO_ENTER` の文で上げ直す。

    `keep` の口を継承可能にする（`os.pipe()` の口は継承不可で、ptyprocess の `pass_fds` は子で閉じないだけなので、
    そのままでは exec で閉じる）。ptyprocess は `preexec_fn` の例外を型と文のまま親の `spawn` で上げるので、
    親は `OSError` として受けられる。
    """
    for fd in keep:
        os.set_inheritable(fd, True)
    try:
        os.chdir(cwd)
    except OSError as exc:
        raise type(exc)(NO_ENTER.format(cwd=cwd, why=exc.strerror)) from exc


def _command_succeeded(got: plugin_update.Updated) -> bool:
    """更新のコマンドが成功した行か（`plugin_update.COMMAND_SUCCEEDED`）。確認の成否は問わない。"""
    return got.result in plugin_update.COMMAND_SUCCEEDED


def spawn_pty(argv: list[str], env: Mapping[str, str], cwd: Path, pass_fds: tuple[int, ...] = ()) -> PtyProcess:
    """擬似端末で子を起動する。作業ディレクトリへは子の `preexec_fn` で入る（ptyprocess の `cwd` は入れないと子で例外のまま終わる）。

    `pass_fds` は関所（`account_dir.gated`）の開ける 1 バイトの読み口と起動の知らせの書き口。
    """
    try:
        size = os.get_terminal_size(sys.stdout.fileno())
        dims = (size.lines, size.columns)
    except OSError:  # 端末でなければ既定の大きさで起動する
        dims = DEFAULT_DIMS
    enter = functools.partial(_enter, cwd, tuple(pass_fds))
    return PtyProcess.spawn(argv, env=dict(env), dimensions=dims, preexec_fn=enter, pass_fds=pass_fds)


def exit_code(child: PtyProcess) -> Optional[int]:
    """終わった子の終了コード。シグナルで終わった子（`exitstatus` が None）は `128 + signalstatus`（シェルと同じ）。"""
    if child.exitstatus is not None:
        return child.exitstatus
    if child.signalstatus is not None:
        return 128 + child.signalstatus
    return None


class Wrapper(LaunchSteps):
    """`spawn`・`clock`・`out`・`stdin_fd`・`wall`（壁時計）はテストで差し替える。"""

    def __init__(
        self,
        settings: Settings,
        spawn: Callable[..., Any] = spawn_pty,
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
        # 短いセグメントの続き（メモリだけ）・検出に当たった後の保持・SPIN_LINE を出したか（#3864 の決定 13）
        self.streak = switch_decide.Streak(0, frozenset())
        self.spin_held = False
        self.spin_told = False
        self.last_reason: Optional[str] = None
        self.last_state: Optional[tuple[str, str, int]] = None
        # 更新・確認の手が確かめた起動先の根。子の PATH をそのバージョンにそろえる（#3909）
        self.launched_root: Optional[Path] = None
        # 前の `plugin_update` の行までに知っていた起動先のバージョン。外で上がったかを比べる（#3909）
        self.known_version: Optional[str] = settings.version
        self.store = account_registry.store_dir(settings.environ)
        self.reader = launch_inputs.Reader(self.store, settings.state_dir, settings.environ, settings.cwd, [CLAUDE, *settings.args])
        self.last_skip = ""

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
        self._tell_at_start()
        ended = self._before_start(update=not self.s.first_resume)
        if ended is None:
            ended = self._start(Launch(1, first, self._initial(self.wall.now()), prompt, resume=self.s.first_resume))
        if ended is not None:
            return self._end(ended)
        while True:
            self._pump(self.s.tick)
            try:
                step = switch_decide.decide(self._state())
            except (signals.SignalError, accounts.AccountError) as exc:
                return self._end(self._halt_broken(exc))
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
        now = self.clock()
        spin = self._spin(sec, now)
        return switch_decide.SectionState(
            question=question,
            question_forwarded=forwarded,
            question_id=qid,
            next_block=nxt["block"] if nxt else None,
            child_alive=alive,
            child_exit=None if alive else exit_code(sec.child),
            idle_seconds=now - sec.last_output,
            next_age_seconds=self._age(nxt["first_written_at"]) if nxt else 0.0,
            quiet_seconds=self.s.quiet,
            stall_seconds=self.s.stall,
            context_tokens=observe.context_tokens(transcript, self.s.transcript_bytes),
            context_limit=self.s.context_limit,
            handover_asked=sec.handover_asked,
            limit_pick=self._limit_pick(sec, screen),
            auth_failed=observe.auth_failed(screen),
            spin=spin,
            spin_seen=self.streak.seen,
            spin_held=self.spin_held,
        )

    @staticmethod
    def _length(sec: Section, now: float) -> float:
        """セグメントの長さ。終わりを決めていればその時刻まで、決めていなければ `now` まで。"""
        end = sec.ended_at if sec.ended_at is not None else now
        return end - sec.started_at

    def _spin(self, sec: Section, now: float) -> bool:
        """今のセグメントを数える手で終えると検出に当たるか。セグメントが閾値の秒に達していれば保持を下ろす（決定 13）。"""
        length = self._length(sec, now)
        if self.spin_held and length >= self.s.spin.seconds:
            self.spin_held = self.spin_told = False
        here = switch_decide.target_key(sec.account)
        return switch_decide.hit(switch_decide.advance(self.streak, length, "restart", here, here, self.s.spin), self.s.spin)

    def _question(self, sec: Section, alive: bool) -> tuple[bool, bool, Optional[str]]:
        """質問のファイルを読み、（回答待ちか, 転送したか, 質問の ID）を返す。今の子のものでない質問は消す（I3）。

        壊れた・読めないファイルは回答待ちとして扱い、転送しない（決定 5）。消せなかった質問はこの見回りでは無いとして返し、
        子を止める側の確かめ（質問のファイルが在れば止めない）に任せる。
        """
        no_question: tuple[bool, bool, Optional[str]] = (False, False, None)

        def drop_for_dead_child() -> tuple[bool, bool, Optional[str]]:
            """子が終わっていれば質問のファイルは不要なので消し、質問なしとして返す。"""
            signals.drop_question(self.dir, None, 0)
            return no_question

        sec.pending = None
        try:
            question = signals.read_question(self.dir)
        except signals.SignalError as exc:
            if not alive:
                return drop_for_dead_child()
            if not sec.broken:
                self._log({"kind": "question_broken", "reason": str(exc)})
                sec.broken = True
            return True, True, None
        if question is None:
            sec.forwarded_question, sec.broken = None, False
            return no_question
        if not alive:
            return drop_for_dead_child()
        stale = self._stale(sec, question)
        if stale is not None:
            match = {key: question.get(key) for key in ("tool_use_id", "at", "launch")}
            if signals.drop_question(self.dir, match, 0) == "dropped":
                self._log({"kind": "stale_question", "reason": stale})
            return no_question
        sec.pending = question
        qid = signals.question_id(question)
        forward_key = "" if qid is None else qid  # _forward_key と同じ値
        return True, sec.forwarded_question == forward_key, qid

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
        now = self.wall.now()
        if sec.limit_at is None:
            sec.limit_at = now
        inputs = self._inputs(now, True)  # 登録の今の世代の使用率と上限の観測（status line の書く名前・世代別の記録）
        rows = inputs.usage.rows if inputs.usage is not None else []
        measured = self._measured_at(sec)
        if measured is None or measured < sec.limit_at:
            # 上限を見た後に測っていない使用率では、今のアカウントの上限が解けたと読まない
            rows = [r._replace(limited=True) if r.name == sec.account else r for r in rows]
        return account_decide.after_limit(sec.account, rows, inputs.candidates(), [], self.s.guards)

    def _measured_at(self, sec: Section) -> Optional[float]:
        """今のアカウントの今の世代の使用率を最後に測った時刻（status line の記録と取得の記録の新しいほうの mtime）。"""
        t = sec.target
        if t is None or t.account is None or t.lease is None:
            return None
        paths = (accounts.usage_path(self.s.state_dir, t.account, t.generation), accounts.fetch_path(self.store, t.account, t.generation))
        return max((p.stat().st_mtime for p in paths if p.is_file()), default=None)

    def _act(self, step: switch_decide.Step) -> Optional[Ended]:
        """手の種類に応じた動きを打つ。表に無い種類（`wait`）は何もしない。終わるときは `Ended` を返す。

        次のセグメントを起動する手なら、子を止める前にセグメントの終わりの時刻を書く（子を止める待ちをセグメントに入れない）。
        """
        sec = self._current()
        if step.kind in LAUNCHES and sec.ended_at is None:
            sec.ended_at = self.clock()
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
        # 起動先は止める前に選び、マーカーの削除は止めた後にする（止めなければ何も変えない。#3814）
        try:
            choice = self._cut_choice(sec)
        except accounts.AccountError as exc:  # 壊れた指定は今と同じく止める
            return self._halt_broken(exc)
        stopped = self._stop_if_clear(sec)
        if stopped != "stopped":
            return self._not_stopped(stopped, sec)
        signals.drop(self.dir, signals.NEXT_FILE)
        ended = self._before_start(update=True)  # 次の子は更新の後に起動する（I1）
        if ended is None:
            req = Launch(sec.number + 1, str(uuid.uuid4()), choice, step.prompt, resume=False, kind=step.kind, reason="cut")
            ended = self._start(req)
        return ended

    def _cut_choice(self, sec: Section) -> Choice:
        """カットポイントの起動の要求。`P`（`_pinned_or` を当てた判断の結果）→ `order` の残り。`P` が None なら既定のログイン。"""
        now = self.wall.now()
        rows, candidates = self._inputs(now, True).usage.rows, self._candidates(now)
        pick = account_decide.before_limit(sec.account, rows, candidates, self.s.guards)
        first = self._pinned_or(pick.account, "cut", sec.account, now)
        if first is None:
            return Choice(())
        rest = account_decide.order(sec.account, rows, candidates, self.s.guards)
        return Choice((first, *[n for n in rest if n != first]))

    def _on_resume(self, step: switch_decide.Step, sec: Section) -> Optional[Ended]:
        stopped = self._stop_if_clear(sec)
        if stopped != "stopped":
            return self._not_stopped(stopped, sec)
        ended = self._before_start(update=False)  # 同じ会話を続ける手は更新しない（I3）
        if ended is None:
            # 上限の後は替え先が尽きたら今のアカウント（既定のログインへ落とさない）。起動し直しは今の起動先に固定する
            limit = step.kind == "account"
            if limit:
                choice = Choice(launch_target.named(step.account or sec.account), sec.account)
            else:
                choice = Choice(launch_target.named(sec.account), fixed=True)
            req = Launch(sec.number, sec.session_id, choice, step.prompt, resume=True, kind=step.kind, reason="limit" if limit else "cut")
            ended = self._start(req)
        if ended is None:
            # 同じセグメントを続けるので、引継ぎを頼んだかどうかも引き継ぐ（同じセグメントで 2 回頼まない）
            self._current().handover_asked = sec.handover_asked
        return ended

    def _on_handover(self, step: switch_decide.Step, sec: Section) -> Optional[Ended]:
        # 子の端末へは書かず、子を止めて同じ会話を引継ぎを頼む文（HANDOVER_PROMPT）で起動し直す。起動し直しの回数には数えない（I6）
        stopped = self._stop_if_clear(sec)
        if stopped != "stopped":
            return self._not_stopped(stopped, sec)
        ended = self._before_start(update=False)  # 同じ会話を続ける手は更新しない（I3）
        if ended is None:
            choice = Choice(launch_target.named(sec.account), fixed=True)  # 同じ会話を別の認証で続けない
            req = Launch(sec.number, sec.session_id, choice, step.prompt, resume=True, kind=step.kind, reason="cut")
            ended = self._start(req)
        if ended is None:
            self._current().handover_asked = True
        return ended

    def _on_finish(self, step: switch_decide.Step, sec: Section) -> Optional[Ended]:
        return Ended("", step.reason)

    def _on_spin(self, step: switch_decide.Step, sec: Section) -> Optional[Ended]:
        """再起動ループの検出に当たった手。次のセグメントを起動しない（#3864 の決定 13）。

        子が動いていれば止めずに中継を続け、保持を立てる。画面の 1 行と `stop` の行は保持の間に 1 回だけ出す。
        子が終わっていれば子の終了コードで終わる（`_end` が `end` の行に `spin` を書く）。
        """
        if not sec.child.isalive():
            return Ended("spin", step.reason, code=exit_code(sec.child))
        self.spin_held = True
        if not self.spin_told:
            self.spin_told = True
            self._say(self._spin_line())
            self._log({"kind": "stop", "cause": "spin", "reason": step.reason})
        return None

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
        "spin": _on_spin,
    }

    def _start(self, req: Launch) -> Optional[Ended]:
        """子を起動して None を返す。起動できなければ置いたマーカーを消し、停止理由を返す（記録は `_loop` の `_end` が書く）。

        前のセグメントがあれば先に書き戻す。起動先は `_launch_target` が確定し、その `Target` の環境と起動引数をそのまま使う。
        起動ごとに起動の ID を作り、候補を試す前に今の起動として記録して子へ渡す。起動できなければ記録を外す（#3814）。
        起動先の根が分かれば、子の `PATH` を `session_env.with_launched_ndf` で起動先のバージョンにそろえる（#3909）。
        """
        prev = self.section
        if prev is not None:
            self._settle(prev)
        launch = uuid.uuid4().hex
        missing = self._cwd_missing()  # `_before_start` の後に消えたときのために、マーカーを置く前にも確かめる
        if missing is not None:
            return missing
        try:
            cli_floor.require("claude", CLAUDE, self._env_for(None, launch), self.s.cwd)
        except cli_floor.TooOld as exc:
            return Ended("precondition", LAUNCH_FAILED.format(why=exc))
        head = ["--resume", req.session_id] if req.resume else ["--session-id", req.session_id]
        once = self.s.first_args if req.number == 1 else ()
        if req.prompt:
            markers.mark(self.s.state_dir, req.session_id)
        signals.set_launch(self.dir, launch)
        try:
            got = self._launch_target(req, [CLAUDE, *head, *self.s.args, *once, *([req.prompt] if req.prompt else [])], launch)
        except Halt as halt:
            got = halt.ended
        if not isinstance(got, Launched):
            # 起動しなかった子の hook は動かないので、外すのにロックは要らない
            signals.set_launch(self.dir, None)
            if req.prompt:
                markers.take(self.s.state_dir, req.session_id)
            return self._not_launched(got, prev)
        target, child = got
        started = self.clock()
        self.section = Section(
            req.number, req.session_id, target.account, child, launch, last_output=started, started_at=started, target=target
        )
        self._advance(prev, req.kind, target.account, started)
        self._write_state("running", f"セグメント {req.number} を始めた")
        log = {"kind": "section", "section": req.number, "session_id": req.session_id, "account": target.account, "resume": req.resume}
        self._log(log)
        prev_account = prev.account if prev is not None else None
        self._log(
            {"kind": "account", "from": prev_account, "account": target.account, "reason": target.reason, "skipped": list(target.skipped)}
        )
        return None

    def _spin_line(self) -> str:
        """再起動ループの案内の 1 行。端末のラッパーは `ndf relay run`、名前つきのラッパーは tmux の外の `ndf relay restart <名前>`。"""
        again = AGAIN_RESTART.format(name=self.s.name) if self.s.environ.get("NDF_RELAY_SPAWNED") else AGAIN_RUN
        return SPIN_LINE.format(seconds=self.s.spin.seconds, count=self.s.spin.count, again=again)

    def _env_for(self, account: Optional[str], launch: str) -> dict[str, str]:
        """起動先を確定した子の環境の基になる環境（`child_env(decided=True)`・起動先の NDF の `PATH`・起動の ID）。"""
        env = session_env.child_env(self.s.environ, self.s.state_dir, account, decided=True)
        if self.launched_root is not None:
            env = session_env.with_launched_ndf(env, self.launched_root)
        return {**env, **signals.launch_env(self.dir, launch)}

    def _inputs(self, now: float, need_candidates: bool) -> launch_inputs.Inputs:
        return self.reader.read(now, need_candidates)

    def _candidates(self, now: float) -> list[str]:
        """再登録が要らず、外した間でなく、`ready` に通る登録済みアカウント（キャッシュのスタンプが変わった名前だけ確かめ直す）。"""
        return self._inputs(now, True).candidates()

    def _pinned_or(self, account: Optional[str], reason: str, current: Optional[str], now: float) -> Optional[str]:
        """判断の結果 `account` より指定を先に使うか（決定 7。`Inputs.pin_first`）。壊れた指定は `AccountError`。"""
        pin = accounts.pinned(self.s.state_dir)
        if pin is None or reason in ("guard", "auth", "revoked") and pin == current:
            return account
        return pin if self._inputs(now, True).pin_first(pin, self.s.guards) else account

    def _tell_at_start(self) -> None:
        """再登録が要るアカウントを 1 つにつき 1 行、従量の接続の設定が壊れていれば 1 行出す（画面だけ）。"""
        for line in launch_target.start_notes(self.store):
            self._say(line)

    def _advance(self, prev: Optional[Section], kind: Optional[str], account: Optional[str], now: float) -> None:
        """起動した子で短いセグメントの続きを進める。最初のセグメントも数える（#3864 の決定 13）。"""
        target = switch_decide.target_key(account)
        if prev is None or kind is None:
            self.streak = switch_decide.Streak(0, frozenset({target}))
            return
        current = switch_decide.target_key(prev.account)
        self.streak = switch_decide.advance(self.streak, self._length(prev, now), kind, current, target, self.s.spin)

    def _before_start(self, update: bool) -> Optional[Ended]:
        """子を起動する直前の準備。止めるなら `Ended`、起動してよければ None を返す。

        `update` が真なら更新の手、偽なら確認の手を呼んで記録する（I1・I3）。起動先のメジャーバージョンが違えば止め（I7）、
        更新や確認を待つ間に停止シグナルファイルが置かれていれば止める。更新の失敗の 1 行は、次の子を実際に起動するときだけ出す（I2）。
        作業ディレクトリが無ければ、更新（作業ディレクトリでコマンドを打つ）も確認もせずに止める（#3842）。
        確かめた起動先の根を `launched_root` に覚え、`_start` が子の `PATH` をそのバージョンにそろえる（#3909）。
        """
        missing = self._cwd_missing()
        if missing is not None:
            return missing
        got = self._update_plugin() if update else self._confirm_plugin()
        if got is None:
            self.launched_root = None
            return self._stop_requested()
        self.launched_root = got.target.root
        ended = self._stop_if_major_changed(got.target) or self._stop_requested()
        if ended is None and got.result == "failed":
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
        """`plugin_update` の行を書き、知っていたバージョンを起動先へ進める（#3909）。

        確認が失敗して起動先が null でも、更新のコマンドが成功した行（`plugin_update.COMMAND_SUCCEEDED`）なら `to` へ進める
        （自分で入れたバージョンを次の行で外の更新と取り違えない）。どちらも無ければ進めない。
        """
        launched = got.target.version
        self._log(
            {
                "kind": "plugin_update",
                "result": got.result,
                "from": got.before,
                "to": got.after,
                "launched": launched,
                "wrapper": self.s.version,
                "outside": self._outside(got),
                "reason": got.reason,
                "seconds": round(self.clock() - began, 1),
            }
        )
        if launched is not None:
            self.known_version = launched
        elif _command_succeeded(got) and got.after is not None:
            self.known_version = got.after

    def _outside(self, got: plugin_update.Updated) -> Optional[bool]:
        """ラッパーの外で上がっていたか。判定できなければ None（#3909）。

        前の比べは、知っていたバージョンとこの行の更新の前（`from`。null なら `launched`）。後の比べは、更新のコマンドが
        成功した行（`plugin_update.COMMAND_SUCCEEDED`）の `to` と確認で読んだ `launched`（コマンドと確認の間に上がった分）。
        どちらかが違えば真。後の比べができない行で `from` と `launched` が違えば、その差を更新の結果から説明できないので None。
        """
        launched = got.target.version
        before = got.before if got.before is not None else launched
        before_compared = self.known_version is not None and before is not None
        moved_before = before_compared and self.known_version != before
        after_compared = _command_succeeded(got) and None not in (got.before, got.after, launched)
        moved_after = after_compared and got.after != launched
        if moved_before or moved_after:
            return True
        unexplained = not after_compared and None not in (got.before, launched) and got.before != launched
        if unexplained:
            return None
        return False if before_compared or after_compared else None

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

    def _stop_if_clear(self, sec: Section) -> str:
        """質問の hook と同じロックを待たずに取り、質問のファイルが無ければロックを持ったまま子を止める（I4）。

        `"stopped"` は子の終わりを確かめた、`"held"` はロックを取れない・質問のファイルが在る（子にも何もしない）、
        `"alive"` は強制終了しても子が終わらない。止めたら止めた子の起動を今の起動から外し、残った hook が書けないようにする。
        """
        with signals.held(self.dir / QUESTION_LOCK, 0) as got:
            if not got or signals.question_present(self.dir):
                return "held"
            stopped = self._stop_child(sec)
            signals.set_launch(self.dir, None)
            return "stopped" if stopped else "alive"

    def _not_stopped(self, stopped: str, sec: Section) -> Optional[Ended]:
        """子を止められなかったときの終わり。確かめが外れた（`held`）なら何もせず次の見回りで決め直し、終わらない子なら止まる。

        `held` ならセグメントは続くので、終わりの時刻を取り消す（回答待ちの間もセグメントの長さに数え、次の判断で求め直す）。
        """
        if stopped == "held":
            sec.ended_at = None
            return None
        return self._child_alive(sec)

    @staticmethod
    def _child_alive(sec: Section) -> Ended:
        return Ended("refused", CHILD_ALIVE.format(pid=sec.child.pid, seconds=limits.seconds("relay.child-exit")))

    def _halt_broken(self, exc: Exception) -> Ended:
        """設定が壊れたときの終わり。回答待ちの質問を残したまま子を止めず、ロックの中の確かめが通るか子が終わるまで待つ（決定 5）。

        待つ間は判断をせず（切り替え・起動し直し・引継ぎ・転送をしない）、子の出力と利用者の入力だけをそのまま渡す。
        """
        sec = self._current()
        self._write_state("waiting", f"{exc}。{HALT_BROKEN}")
        while sec.child.isalive():
            stopped = self._stop_if_clear(sec)
            if stopped == "alive":
                return self._child_alive(sec)
            if stopped == "stopped":
                break
            self._pump(self.s.tick)
        return Ended("config_broken", str(exc))

    def _stop_child(self, sec: Section) -> bool:
        """子を強制終了し、子の終わりを確かめたら真。強制終了が偽を返したら `relay.child-exit` の秒まで終わりを待つ。"""
        child = sec.child
        if not child.isalive() or child.terminate(force=True):
            return True
        deadline = self.clock() + limits.seconds("relay.child-exit")
        while child.isalive():
            if self.clock() >= deadline:
                return False
            time.sleep(self.s.tick)
        return True

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
        if step.kind == "spin" and self.spin_held:
            return  # 保持の間の spin は理由が見回りごとに変わっても書かない（`_on_spin` が stop の行を 1 回書く）
        if step.reason != self.last_reason:
            self._log({"kind": step.kind, "reason": step.reason})
            self.last_reason = step.reason

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
            },
        )

    def _log(self, row: Mapping[str, Any]) -> None:
        registry.append_log(self.dir, {"section": self.section.number if self.section else None, **dict(row)})

    def _end(self, ended: Ended) -> Ended:
        if self.section is not None:
            self._settle(self.section)
        self._write_state("stopped", ended.reason)
        self._log({"kind": "end", "cause": ended.cause, "reason": ended.reason})
        return ended
