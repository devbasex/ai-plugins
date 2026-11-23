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

import errno
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
    wrapper_fetch,
)
from ndf.domains.relay.lane_question import AskPlan
from ndf.domains.relay.launch_target import Choice, Target
from ndf.domains.relay.wrapper_auth import AuthSteps
from ndf.domains.relay.wrapper_guard import GuardSteps
from ndf.domains.relay.wrapper_launch import AGAIN_RESTART, LAUNCH_FAILED, STOP_REASON, Ended, Halt, Launched, LaunchSteps
from ndf.domains.relay.wrapper_stop import StopSteps

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
NOT_FOUND = "子の PATH に {exe} が無い"
NO_ENTER = "作業ディレクトリ {cwd} に入れない: {why}"
# 計画のリクエストへ渡す質問のコピー（質問のファイルは hook が消すまで残すので、渡すのはこの写し。#3814）
FORWARD_FILE = ".question.forward.json"
# 質問の hook と同じロック。ラッパーはこれを持ったまま質問が無いと確かめて子を止める（#3814 の I4）
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
    `fetch` は使用率の API の取得の手（`accounts.fetch_usage`。None なら取らない。#3864 の決定 4）。
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
    fetch: Optional[wrapper_fetch.Fetch] = None


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
    `input_at` はラッパーが子へ最後に入力（利用者の入力の転送・起動の引数の 1 行）を送った時刻（壁時計）。
    `choice`・`choice_reason` は子を止める前に確定した次の起動順とその起動の理由（`guard`・`limit`・`auth`）。
    `wall_started` は起動を始めた時刻（壁時計）、`auth_at`・`auth_told` は認証の失敗を確かめた時刻・直し方の 1 行を出したか。
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
    input_at: Optional[float] = None
    choice: Optional[Choice] = None
    choice_reason: str = "limit"
    wall_started: float = 0.0
    auth_at: Optional[float] = None
    auth_told: bool = False
    # キーの子の停止（#3864 の決定 12）: 止める必要・待たずに止める・立てた時刻・立てた側（drift か auth）・設定の当たりの出所
    must_end: bool = False
    end_now: bool = False
    must_end_at: Optional[float] = None
    end_cause: Optional[str] = None
    end_source: Optional[str] = None
    stop_by: Optional[float] = (
        None  # `end_now` を立てた見回りで当たりを確かめた時刻 + `relay.child-exit`（その見回りの拒否のマークと旧子の停止が分け合う期限。`clock`）
    )
    denied: bool = False  # この子の起動で 401 の拒否のマークを記録へ書けたか（書けた後は見回りごとに書き足さず、錠も待たない。r3-3）
    # キーの子の最初の払い出しを見届けるまで子の端末へ送らずに保留する入力（None は保留しない）と、見届けたか（別冊の「最初の払い出しまで送らない」）
    held: Optional[list[bytes]] = None
    paid: bool = False
    replied: bool = False  # この子が応答を 1 つ返したか（返したら `Wrapper.tried` を空にする。決定 6）
    # キーの子の起動の後の設定の変更の検出（`_drift`）: 次に比べるスタンプ（None は未確かめ）と、同じ名前の登録し直しで止めたか
    drift_stamps: Optional[dict[str, Optional[tuple]]] = None
    rekeyed: bool = False


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
    実行ファイルは子の `PATH` と作業ディレクトリで解決した絶対パスで渡す（ptyprocess は親の `PATH` で解決するため、確かめたバージョンと
    別の `claude` を起動しない）。子の `PATH` で見つからなければ、親の `PATH` で探し直させずに `FileNotFoundError`（起動の失敗）。
    """
    found = cli_floor.locate(argv[0], env, cwd)
    if found is None:
        raise launch_target.NotFound(errno.ENOENT, NOT_FOUND.format(exe=argv[0]))
    argv = [found, *argv[1:]]
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


class Wrapper(GuardSteps, AuthSteps, StopSteps, LaunchSteps):
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
        self.memo = None  # 替え先の判断のキャッシュ（`GuardSteps._confirm`）
        self.fetcher = None if settings.fetch is None else wrapper_fetch.Fetcher(settings.fetch)
        # 取得の判定の結果の再利用（名前 → (世代・取得の記録のスタンプ・tried, 取り直してよい最初の時刻)）
        self.due_memo: dict[str, tuple[tuple, float]] = {}
        # この続きで認証に失敗した従量の接続と API キー（ラッパーの終わりで消える）と、キーの子の記録の読みの結果（決定 12）
        self.tried: set[str] = set()
        self.reader.tried = self.tried
        self.key_memo: Optional[tuple] = None

    def run(self, prompt: str) -> Ended:
        """錠を取り、バージョンの混ざりを確かめてからセグメントを回す。"""
        self.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:  # 錠と状態を書く前に、旧版の保存先も含めて自分の 0700 にそろえる（どの起動経路でも）
            os.chmod(self.dir, 0o700)
        except OSError as exc:
            return Ended("precondition", f"中継の保存先（{self.dir}）を自分だけが読める 0700 のディレクトリにできない（{exc}）")
        with signals.held(self.dir / registry.LOCK_FILE, 0) as got:
            if not got:
                return Ended("refused", f"{self.s.name} のラッパーは既に動いている")
            others = [r for r in registry.all_running(self.s.state_dir, self.s.environ) if r.name != self.s.name]
            mix = hub_decide.mixed(self.s.version, others)
            if not mix.ok:
                return Ended("precondition", mix.reason)
            try:
                return self._loop(prompt)
            finally:
                # 例外で抜けても取得のスレッドを残さない（始めた OAuth の更新が資格情報を書き終えるまで待つ）
                if self.fetcher is not None:
                    self.fetcher.close()

    def _loop(self, prompt: str) -> Ended:
        stop_signal.drop_stop(self.dir)  # 前の実行が残したシグナルファイルで、始めたばかりの実行を止めない
        halt_shown = False
        ended = self._first_start(prompt)
        if ended is not None:
            return self._end(ended)
        while True:
            self._pump(self.s.tick)
            try:
                ended, stopped = self._key_step()  # (2)〜(3) キーの子は替え先を読む前に止める（#3864 の決定 12）
                if ended is not None:
                    return self._end(ended)
                step = switch_decide.decide(self._state(stopped))  # (4)
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

    def _first_start(self, prompt: str) -> Optional[Ended]:
        """最初のセグメントを起動する。利用者が --resume で続けた会話の途中では更新しない（I3）。"""
        first = self.s.first_session or str(uuid.uuid4())
        self._tell_at_start()
        ended = self._before_start(update=not self.s.first_resume)
        return (
            ended
            if ended is not None
            else self._start(Launch(1, first, self._initial(self.wall.now()), prompt, resume=self.s.first_resume))
        )

    def _state(self, stopped: bool) -> switch_decide.SectionState:
        """見回り 1 回の状態。止める必要の立った子では上限・認証・見回りの替え先を読まず、止めた見回りでだけ替え先を取り直す。"""
        sec = self._current()
        sec.choice = None
        self._drain_fetch()
        self._queue_fetch(self.wall.now())
        nxt = self._next_mark(sec)
        alive = sec.child.isalive()
        question, forwarded, qid = self._question(sec, alive)
        screen = observe.plain(bytes(sec.screen))
        now = self.clock()
        spin = self._spin(sec, now)
        key = sec.target is not None and sec.target.helper is not None  # キーの子の認証の失敗は `_key_auth` が止める
        self._forget_tried(sec)
        # 上限の見回りでも先に確かめる（上限の手も `auth_at` で今のアカウントを除く）
        seen = not sec.must_end and not key and self._auth_seen(sec, screen)
        limit = None if sec.must_end else self._limit_pick(sec, screen)
        auth = self._auth_pick(sec) if seen and limit is None else None  # 上限が先（替え先の確定を上書きしない）
        # 見回りは上限も認証の失敗も無く子が動いている見回りだけ（子が終わった後に会話を別のアカウントで再開しない）
        guard = self._guard_pick(sec, self.wall.now()) if not sec.must_end and limit is None and auth is None and alive else None
        safe = guard is not None and guard.kind == "switch" and self._safe_point(sec, now - sec.last_output)
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
            context_tokens=observe.context_tokens(self._transcript(sec), self.s.transcript_bytes),
            context_limit=self.s.context_limit,
            handover_asked=sec.handover_asked,
            limit_pick=limit,
            auth_pick=auth,
            spin=spin,
            spin_seen=self.streak.seen,
            spin_held=self.spin_held,
            guard_pick=guard,
            safe=safe,
            must_end=sec.must_end,
            end_ready=stopped,
            drift_pick=self._revoked_pick(sec, self.wall.now()) if stopped else None,
            metered=account_decide.metered(sec.account),
        )

    def _queue_fetch(self, now: float) -> None:
        """取り直す登録済みアカウント（動いていないものも、待つ間も）を今の登録の世代と組にしてキューへ入れる（決定 4）。

        取り直すのは、最後に試した時刻から `relay.usage-fetch` を過ぎたか、その後のリセットを過ぎたとき（`accounts.due_at`）。
        取得の記録のスタンプと `tried` が同じ間は、その時刻を再利用する（見回りごとに取得の記録を開かない）。
        """
        f = self.fetcher
        usage = self._inputs(now, False).usage if f is not None else None
        if f is None or usage is None:
            return
        interval = limits.seconds("relay.usage-fetch")
        for name, gen in usage.generations.items():
            last = f.tried.get(name)
            tried = last[1] if last is not None and last[0] == gen else None  # 登録し直した名前は旧い世代の投入時刻を使わない
            if name in f.busy:
                continue
            path = accounts.fetch_path(self.store, name, gen)
            key = (gen, launch_inputs.stamp(path), tried)
            memo = self.due_memo.get(name)
            at = memo[1] if memo is not None and memo[0] == key else accounts.due_at(path, interval, tried)
            if at <= now:
                f.put(accounts.FetchTarget(self.store, name, gen), now)
                self.due_memo.pop(name, None)
            else:
                self.due_memo[name] = (key, at)

    def _drain_fetch(self) -> None:
        """取得の返りを読む。形の変化は 1 つのラッパーで 1 回だけ知らせ、取れたら次の変化でまた知らせる（決定 4）。"""
        f = self.fetcher
        if f is None:
            return
        failed = []
        for name, got in f.results():
            if isinstance(got, Exception):
                failed.append({"account": name, "error": type(got).__name__})
            elif got is not None and got.error == "shape" and not f.shape_told:
                f.shape_told = True
                self._say(wrapper_fetch.SHAPE_LINE)
                self._log({"kind": "usage_shape", "account": name, "items": list(got.missing)})
            elif got is not None and got.error is None:
                f.shape_told = False
        if failed:
            self._log({"kind": "usage_fetch_failed", "count": len(failed), "sample": failed[:10]})

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
        if step.kind == "account" and not sec.must_end:
            # 替え先の最後の確かめは子を止める直前に打つ。外れたら子を残し、次の見回りで候補を選び直す
            why = self._recheck_choice(sec)
            if why is not None:
                sec.ended_at = None
                self._log({"kind": "account_recheck", "account": sec.choice.names[0] if sec.choice else None, "reason": why})
                return None
        # キーの子は `_stop_revoked` が止め、`_revoked_pick` が止める直前の確かめも済ませてある
        stopped = "stopped" if sec.must_end else self._stop_if_clear(sec)
        if stopped != "stopped":
            return self._not_stopped(stopped, sec)
        ended = self._before_start(update=False)  # 同じ会話を続ける手は更新しない（I3）
        if ended is None:
            # 替える手は子を止める前に確定した起動順をそのまま使う（尽きたら今のアカウント。既定のログインへ落とさない）。
            # 起動し直しは今の起動先に固定する
            if step.kind == "account":
                choice, reason = sec.choice or Choice(launch_target.named(step.account), sec.account), sec.choice_reason
            else:
                choice, reason = Choice(launch_target.named(sec.account), fixed=True), "cut"
            req = Launch(sec.number, sec.session_id, choice, step.prompt, resume=True, kind=step.kind, reason=reason)
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
        "revoked": StopSteps._on_revoked,
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
        begun = self.wall.now()  # 子が記録を書くより前（認証の失敗の確かめは、この時刻より後の記録だけを読む）
        head = ["--resume", req.session_id] if req.resume else ["--session-id", req.session_id]
        once = self.s.first_args if req.number == 1 else ()
        if req.prompt:
            markers.mark(self.s.state_dir, req.session_id)
        signals.set_launch(self.dir, launch)
        try:
            got = self._launch_target(req, [CLAUDE, *head, *self.s.args, *once], launch)
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
        self.section.input_at = self.wall.now() if req.prompt and target.held is None else None
        # キーの子は初回のプロンプトの有無によらず、最初の払い出しを見届けるまで入力を保留する（未確認の接続先へ会話を送らない）
        if target.helper is not None:
            self.section.held = [] if target.held is None else [StopSteps.typed(target.held)]
        self.section.wall_started = begun
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
        """再登録が要るアカウントを 1 つにつき 1 行、従量の接続の設定が壊れているか環境変数の設定を使えなければ 1 行出す（画面だけ）。"""
        for line in launch_target.start_notes(self.store, self.s.environ):
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
            self.launched_root = self.reader.launched_root = None
            return self._stop_requested()
        self.launched_root = self.reader.launched_root = got.target.root
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
            if data and sec.held is not None and not sec.must_end:
                sec.held.append(data)  # 最初の払い出しを見届けるまで保留する
            elif data and not sec.must_end:  # 止める必要の立った子へは入力を送らずに捨てる（後継にも渡さない。決定 12）
                sec.child.write(data)
                sec.input_at = self.wall.now()
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
                # 会話の記録と背景のシェルの出力の場所は、子に渡した環境で組んだもの（一覧を呼ぶ側の環境に依らない。#4036）
                **(self._observed_paths(sec) if sec else {"transcript": None, "shell_outputs": None}),
            },
        )

    def _observed_paths(self, sec: Section) -> dict[str, str]:
        """子の会話の記録と背景のシェルの出力の場所（`wrapper.json` の `transcript`・`shell_outputs`）。

        子に渡した環境（`sec.target.env`。従量接続・登録済みキーでは共有の `CLAUDE_CONFIG_DIR` に切り替わる）で組み、
        相対のパスは子の作業ディレクトリを基準に絶対化する（一覧のコマンドは別の作業ディレクトリから読む）。
        """
        env = sec.target.env if sec.target is not None else self.s.environ
        transcript = observe.transcript_path(env, self.s.cwd, sec.session_id)
        shells = observe.shell_output_dir(env, self.s.cwd, sec.session_id)
        return {"transcript": str(self.s.cwd / transcript), "shell_outputs": str(self.s.cwd / shells)}

    def _log(self, row: Mapping[str, Any]) -> None:
        registry.append_log(self.dir, {"section": self.section.number if self.section else None, **dict(row)})

    def _end(self, ended: Ended) -> Ended:
        if self.fetcher is not None:
            self.fetcher.close()
        if self.section is not None:
            self._settle(self.section)
        self._write_state("stopped", ended.reason)
        self._log({"kind": "end", "cause": ended.cause, "reason": ended.reason})
        return ended
