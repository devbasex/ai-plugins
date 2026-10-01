"""`run`: 前景に常駐し、区間ごとの claude を擬似端末の子として起動する（#895・#1142 の C6）。

`Relay` はセッションを切り替える状態機械である。合図の判定の材料（会話の記録）は `claude`、
入出力と子の起動は `terminal`、記録は `record` が持つ。

区間のアカウント（#1389）の選び方・上限シグナルファイルの判定・定期の確認は `switch` の `AccountSwitch` と
`UsageWatch` が持つ。

セッションの切り替えで、更新後の版のバージョンディレクトリへラッパーを入れ替える（#1587）。判定と申し送りは
`handover` が持ち、`Relay.swap` が子の居ない 1 か所（前の `end` の後、次の `start` の前）でだけ呼ぶ。
入れ替えた後の版は `cmd_run` で申し送りを受け、`Relay.resume` から続ける。
"""

from __future__ import annotations

import os
import shlex
import signal
import sys
import time
from dataclasses import asdict, dataclass

from . import claude as cl
from . import handover as ho
from . import record, version_dir
from .common import (
    CHILD_FILE,
    LIMIT_FILE,
    LOCK_FILE,
    LOG_FILE,
    MARK_FILE,
    PID_FILE,
    PKG_ROOT,
    QUESTION_FILE,
    QUESTION_LOCK,
    STOP_FILE,
    _lock,
    _unlock,
    env_num,
    fallback_cwd,
    parse_iso,
    quiet_seconds,
    remove,
    stamp,
)
from .mark import asked_after
from .record import RelayRecord, StartLimit
from .switch import REASON_AUTH, AccountSwitch, UsageWatch
from .terminal import StartFailed, Terminal, pty_available, wait_exit_code

import claude_accounts as ca  # noqa: E402,I001  common が lib/ を sys.path に置く


class NoAccountEnv(Exception):
    """選んだアカウントのトークンを起動の直前に得られず、替えるアカウントも従量の接続の宣言も無い。子を起動しない。"""


@dataclass
class SectionInput:
    """区間の起動の入力。`plan` は上限の後に決めた (アカウント, 理由, 選んだ結果)。"""

    args: list[str]
    cwd: str
    command: str
    from_session: str
    cwd_fallback: str | None = None
    carried: list[str] | None = None
    plan: tuple | None = None

    def to_json(self) -> dict:
        """申し送りの `next`。`plan` の選んだ結果は `Choice` のキーの辞書になる。"""
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> SectionInput:
        plan = d.get("plan")
        if plan:
            to, reason, ch = plan
            if isinstance(ch, dict):
                ch = dict(ch)
                if ch.get("earliest"):
                    ch["earliest"] = tuple(ch["earliest"])
                ch = ca.Choice(**ch)
            plan = (to, reason, ch)
        return cls(**{k: d[k] for k in ho.NEXT_KEYS if k != "plan"}, plan=plan or None)


class Relay(AccountSwitch):
    """前景に常駐し、区間ごとの claude を擬似端末の子として起動する。"""

    def __init__(self, claude: str, relay_dir: str, marketplace: str, version: str, term: Terminal, limit: StartLimit):
        self.claude = claude
        self.dir = relay_dir
        self.record = RelayRecord(relay_dir)
        self.marketplace = marketplace
        self.version = version
        self.env = cl.child_env(relay_dir)
        self.term = term
        self.limit = limit
        self.section = 0
        self.started_at = 0.0
        self.session_id = ""
        self.halted = False
        self.exited = None
        self.saw_question = False
        self.quiet = quiet_seconds()
        # 登録済みのアカウントが 2 つ以上あるときだけ切り替える（I8。起動したときに決める）
        self.multi = ca.registered() >= 2
        self.account: str | None = None  # 今の区間のアカウント（`metered` は従量の接続。None は今と同じ環境）
        self.noted = self.told = None  # 上限の観測を残した・背景の作業の 1 行を出したシグナルファイルの written_at
        self.auth_section = 0  # 認証の失敗を扱った区間（区間ごとに 1 度だけ）
        self.first_args: list[str] = []
        self.install_path: str | None = None  # 更新後のプラグインの導入先（入れ替えの判定が読む）
        self.watch = UsageWatch(self) if self.multi else None
        self.lock = _lock(self.path(LOCK_FILE), None)  # 動いている間は持ち続ける（relay_running が見る）
        with open(self.path(PID_FILE), "w") as f:
            f.write(str(os.getpid()))

    def path(self, name: str) -> str:
        return os.path.join(self.dir, name)

    def log(self, **row) -> None:
        self.record.log(**row)

    # -- 子の起動

    def start_section(self, s: SectionInput) -> None:
        """区間を起動する。`s.plan` は上限の後に決めた (アカウント, 理由, 選んだ結果)。無ければここで選ぶ（F4）。"""
        args, cwd, command, from_session = s.args, s.cwd, s.command, s.from_session
        cwd_fallback, carried, plan = s.cwd_fallback, s.carried, s.plan
        self.record.drop_mark()
        remove(self.path(QUESTION_FILE), self.path(LIMIT_FILE))  # 上限シグナルファイルは前の子のもの（新しい子の hook はまだ書けない）
        prev = self.account
        self.settle_account(prev)  # 前のセッションの .claude.json の共有する部分を書き戻してから次を用意する（E12）
        to, reason, choice = (plan or self.pick(None)) if self.multi else (None, None, None)
        env = cl.section_env(self.env, to, self.note_account_dir) if to else None
        if to and env is None:  # 親の認証へ戻さない。選び直し、無ければ止める
            to, reason, choice, env = self.replace_unusable(to)
        if env is None:
            env = self.env
        argv = cl.metered_settings([*(carried or []), *args], env, cwd, store=self.path(cl.SETTINGS_FILE))
        at = self.term.spawn(self.claude, argv, cwd, env, self.path(CHILD_FILE))
        self.section += 1
        self.started_at = at
        self.account = to
        row = dict(
            event="start",
            at=stamp(at),
            section=self.section,
            pid=self.term.pid,
            command=command,
            from_session=from_session,
            plugin_version=self.version,
            cwd=cwd,
            relay_version_dir=ho.own_dir(),
        )
        if cwd_fallback is not None:
            row["cwd_fallback"] = cwd_fallback
        if carried is not None:
            row["carried"] = carried
        if self.multi:
            row["account"] = to
        self.log(**row)
        bad = ca.metered_problem(self.env) if self.section == 1 else None
        if bad:  # 保存した宣言が壊れていれば、宣言なしとして扱い 1 行出す（#1468 の I5）
            self.term.screen("ndf-relay: " + bad)
            self.log(event="metered_invalid", section=self.section, detail=bad)
        if self.multi:
            self.tell_account(prev, to, reason, choice)
            if self.watch is not None:
                self.watch.reset()
                if not self.watch.thread.is_alive():
                    self.watch.start()
        self.limit.release()

    def replace_unusable(self, failed: str) -> tuple[str, str, ca.Choice, dict]:
        """使えなかった `failed` の代わりを選ぶ。(名前か `metered`, 理由, 選んだ結果, 環境)。
        登録済みのアカウント → 従量の接続の宣言の順に試し、どれも無ければ NoAccountEnv を投げる。"""
        tried = {failed}
        while True:
            c = ca.choose(exclude=tried, keep=self.keep())
            if not c.name:
                break
            env = cl.section_env(self.env, c.name, self.note_account_dir)
            if env is not None:
                return c.name, REASON_AUTH, c, env
            tried.add(c.name)
        if failed != ca.METERED and ca.fallback_env(self.env):
            return ca.METERED, REASON_AUTH, c, cl.section_env(self.env, ca.METERED)
        raise NoAccountEnv(f"アカウント {failed} を使えず、替えるアカウントも従量の接続の宣言も無い")

    # -- 合図の判定

    def read_mark(self):
        return self.record.read_mark()

    def tick(self):
        if self.halted:
            return None
        try:
            self.flush_lines()
            return self._tick_limit() or self._tick()
        except Exception as e:  # 本体の例外で子を巻き込まない
            self.halt("error", f"ラッパーの中で例外が起きた（{type(e).__name__}）")
            return None

    def _may_start(self, written: float) -> bool:
        """次の区間の起動の許可を取る。取れない・起動の上限で断られたら偽（断られたら止める）。"""
        if not self.limit.take():
            return False
        refusal = self.limit.refusal(written, self.started_at)
        if refusal:
            self.limit.release()
            self.halt(*refusal)
            return False
        return True

    def _stop_requested(self) -> bool:
        """停止の合図があれば止めて真。"""
        if os.path.exists(self.path(STOP_FILE)):
            self.halt("stop-file", "停止の合図がある")
            return True
        return False

    def _tick(self):
        m = self.read_mark()
        if m is None:
            return None
        written = parse_iso(m.get("written_at")) or time.time()
        latest = max(written, self.term.last_input)
        tp = m.get("transcript_path") or ""
        unmet, cancel = cl.after_mark(tp, written)
        # 目標が未達で応答が続くあいだは、会話の記録の更新を静止に数えない
        snap = None if unmet else cl.file_snap(tp)
        if snap is not None:
            latest = max(latest, snap[1] / 1e9)
        if time.time() - latest < self.quiet:
            return None
        # (3) 質問の表示中と、合図の後に質問が出たときは書かない（G1）。(4) 合図の後に利用者の入力か背景の処理の起動があれば
        # 書かない。目標が未達の判定が無ければ、合図の後の応答の再開でも書かない（G2）
        if os.path.exists(self.path(QUESTION_FILE)) or cancel or asked_after(self.dir, self.path(MARK_FILE)):
            return None
        if not unmet and cl.replied_after(tp, written):
            return None
        if self._stop_requested() or not self._may_start(written):
            return None
        m["_snap"] = snap
        m["_unmet"] = unmet
        return m

    def recheck(self, m) -> tuple[str, str] | None:
        """質問の後に読み直した合図で起動する前に、`count.lock`・上限・空回りを判定し直す。"""
        end = time.time() + 5
        while not self.limit.take():
            if time.time() >= end:
                return "count-lock", "起動の数を数えるロックが取れない"
            time.sleep(0.1)
        return self.limit.refusal(parse_iso(m.get("written_at")) or time.time(), self.started_at)

    def halt(self, reason: str, why: str) -> None:
        self.halted = True
        self.log(event="stop", section=self.section, reason=reason)
        self.record.drop_mark()
        self.term.screen(f"ndf-relay: 次の区間を起動しない（{why}）。このまま続けるか、/exit して示されたコマンドを手で入力する")

    # -- 切り替え

    def _still_due(self, m) -> bool:
        """`/exit` を書く直前の確かめ直し。質問が無く、合図が同じで、取りやめの行が無いか。"""
        if m.get("_kind") == "limit":
            now = self.read_limit()
            return (
                now is not None
                and now.get("written_at") == m.get("written_at")
                and not self._limit_interrupted(m, parse_iso(m.get("written_at")) or 0)
            )
        now = self.read_mark()
        if (
            os.path.exists(self.path(QUESTION_FILE))
            or now is None
            or now.get("written_at") != m.get("written_at")
            or asked_after(self.dir, self.path(MARK_FILE))
        ):
            return False
        tp = m.get("transcript_path") or ""
        if m.get("_unmet"):
            return not cl.after_mark(tp, parse_iso(m.get("written_at")) or 0)[1]
        return cl.file_snap(tp) == m.get("_snap")

    def _pump_for(self, seconds: float, watch_question: bool = False) -> bool:
        """`seconds` 秒だけ入出力を中継する。子が終われば `exited` に残して真を返す。"""
        end = time.time() + seconds
        while time.time() < end:
            res = self.term.pump(until=min(end, time.time() + 0.1))
            if watch_question:
                self.saw_question |= os.path.exists(self.path(QUESTION_FILE))
            if res:
                self.exited = res
                return True
        return False

    def write_exit(self, m) -> bool:
        """G3。`question.lock` の中で確かめ直し、`/exit` と改行を 1 回の write で書き、1 秒おいて放す。
        目標が未達の判定の後なら、先に Esc を書いて 1 秒おき、確かめ直してから `/exit` を書く。
        確かめ直しで外れたら書かずに偽を返す（`count.lock` も放す）。"""
        fd = _lock(self.path(QUESTION_LOCK), 0)
        if fd is None:
            self.limit.release()
            return False
        try:
            if not self._still_due(m):
                self.limit.release()
                return False
            if m.get("_unmet"):
                # 応答の途中なら Esc で止める（入力待ちの Esc 1 回は何もしない。2 回は巻き戻しを開く）
                try:
                    os.write(self.term.fd, b"\x1b")
                except OSError:
                    pass
                esc_at = time.time()
                if self._pump_for(env_num("NDF_RELAY_ESC_WAIT", 1)):
                    return True
                if self.term.last_input > esc_at or not self._still_due(m):
                    self.limit.release()
                    return False
            try:
                os.write(self.term.fd, b"/exit\r")
            except OSError:
                pass
            # TUI が書いた入力を読み終える前に質問が描かれないよう、1 秒ロックを持つ
            self._pump_for(env_num("NDF_RELAY_EXIT_HOLD", 1), watch_question=True)
            return True
        finally:
            _unlock(fd)

    def wait_for_normal_exit(self, left: float, questioned: bool):
        """質問中は期限を減らさず、子の通常終了を待つ。"""
        while left > 0:
            t0 = time.time()
            res = self.term.pump(until=t0 + min(left, 0.2))
            if res:
                return res, left, questioned
            if os.path.exists(self.path(QUESTION_FILE)):
                questioned = True
                self.limit.release()
                continue
            left -= time.time() - t0
        return None, left, questioned

    def end_child(self) -> tuple[str, int, bool]:
        """`/exit` の後の待ち。(終わり方, 子の wait の状態, 待ちのあいだに質問が出たか) を返す。
        30 秒で SIGTERM、さらに 10 秒で SIGKILL。質問の合図がある間は秒を数えず、`count.lock` を放す。"""
        questioned, self.saw_question = self.saw_question, False
        if self.exited:
            res, self.exited = self.exited, None
            return "mark", res[1], questioned
        res, _, questioned = self.wait_for_normal_exit(env_num("NDF_RELAY_EXIT_WAIT", 30), questioned)
        if res:
            return "mark", res[1], questioned
        ended_by, status = self.term.stop_child()
        return ended_by, status, questioned

    def give_up(self, reason: str, why: str, command: str, **extra) -> int:
        self.limit.release()
        self.log(event="stop", section=self.section, reason=reason, **extra)
        self.term.screen(f"ndf-relay: 次の区間を起動できない（{why}）。次のコマンド:\r\n{command}")
        return 2

    def log_end(self, until: float, ended_by: str) -> None:
        self.log(event="end", section=self.section, pid=self.term.pid, seconds=round(until - self.started_at, 3), ended_by=ended_by)

    def finalize_section(self, m, written: float) -> tuple[dict | None, int | None]:
        """`/exit` の後の後処理。子を終わらせ、読み直した合図から続ける合図か終了コードを決める。
        `event="end"` はここで 1 度だけ記録する。続けるなら (合図, None)、終わるなら (None, 終了コード)。"""
        ended_by, status, questioned = self.end_child()
        again = self.read_limit() if m.get("_kind") == "limit" else self.read_mark()
        requeued = questioned or again is None or again.get("written_at") != m.get("written_at")
        if requeued:
            # 書いた /exit が質問の答えの後に働いた。答えの後の Stop が合図を書き直すか消している
            self.limit.release()
            if again is None:
                self.log_end(time.time(), "no-mark")
                return None, wait_exit_code(status)
        self.log_end(written, ended_by)
        if requeued:
            why = self.recheck(again)
            if why:
                return None, self.give_up(why[0], why[1], again.get("command") or self.resume_command(m))
            # 上限の後は、読み直したシグナルファイルでも決めたアカウントのまま続ける
            return (m if m.get("_kind") == "limit" else again), None
        return m, None

    def resume_command(self, m) -> str:
        """利用者が手で打つ次のコマンド（上限の後）。"""
        args, *_ = self.limit_start(m)
        return shlex.join(["claude", *args])

    def prepare_next(self, m) -> tuple[str, str | None] | None:
        """プラグインを更新し、合図から次の区間の (cwd, 退避前の cwd) を決める。更新に失敗したら None。"""
        got = cl.update_plugin(self.claude, self.marketplace)
        if got is None:
            return None
        self.version, self.install_path = got
        cwd = m.get("cwd") or os.getcwd()
        fb = None
        if not os.path.isdir(cwd):
            fb, cwd = cwd, fallback_cwd(cwd)
        return cwd, fb

    def loop(self, first_args: list[str]) -> int:
        self.first_args = first_args
        code = self._start_first_section(first_args)
        if code is not None:
            return code
        return self._serve()

    def _serve(self) -> int:
        """合図を待って切り替える。子が合図なしに終われば同じ終了コードで、止まれば決まった終了コードで返す。"""
        carried = cl.carried_args(self.first_args)
        while True:
            res = self.term.pump(tick=self.tick)
            if res[0] == "exit":
                self.log_end(time.time(), "no-mark")
                return wait_exit_code(res[1])
            m = res[1]
            if not self.write_exit(m):
                continue
            written = parse_iso(m.get("written_at")) or time.time()
            m, code = self.finalize_section(m, written)
            if code is not None:
                return code
            args, command, from_session, src, shown, plan = self._next_section_input(m)
            nxt = self.prepare_next(src)
            if nxt is None:
                return self.give_up("update-failed", "プラグインの更新か版の読み取りに失敗した", shown)
            cwd, fb = nxt
            s = SectionInput(args=args, cwd=cwd, command=command, from_session=from_session, cwd_fallback=fb, carried=carried, plan=plan)
            self.swap(s, shown)  # 入れ替えたら戻らない（I1。子の居ないこの 1 か所だけ）
            self.term.screen(f"── ndf-relay: 区間 {self.section + 1} ──")
            code = self._start_next_section(s, shown)
            if code is not None:
                return code

    def _start_first_section(self, first_args: list[str]) -> int | None:
        """最初の区間を起動する。起動できなければ終了コード。"""
        try:
            self.start_section(SectionInput(args=first_args, cwd=os.getcwd(), command=shlex.join(first_args), from_session=""))
        except StartFailed as e:
            self.log(event="stop", section=self.section + 1, reason="start-failed", errno=e.err)
            cl.say(f"claude を起動できない（{os.strerror(e.err)}）")
            return 127
        except NoAccountEnv as e:
            self.log(event="stop", section=self.section + 1, reason="auth")
            cl.say(f"claude を起動しない（{e}）")
            return 2
        return None

    def _next_section_input(self, m: dict) -> tuple[list[str], str, str, dict, str, tuple | None]:
        """次の区間の (引数, 記録のコマンド, 元の会話, cwd の元, 表示のコマンド, 上限の後の選び方)。"""
        if m.get("_kind") == "limit":
            self.drop_limit(m)
            args, command, from_session, src = self.limit_start(m)
            return args, command, from_session, src, shlex.join(["claude", *args]), m["_plan"]
        return [m["command"]], m["command"], m.get("session_id") or "", m, m["command"], None

    def _start_next_section(self, s: SectionInput, shown: str) -> int | None:
        """次の区間を起動する。起動できなければ終了コード。"""
        try:
            self.start_section(s)
        except StartFailed as e:
            return self.give_up("start-failed", f"claude を起動できない（{os.strerror(e.err)}）", shown, errno=e.err)
        except NoAccountEnv as e:
            return self.give_up("auth", str(e), shown)
        return None

    # -- ラッパーの入れ替え（#1587）

    def swap(self, s: SectionInput, shown: str) -> None:
        """動いているバージョンディレクトリと更新後に使うべきものが違えば、入れ替える（戻らない）。

        同じ名前・キャッシュからの起動では何もしない（外部コマンドも行も無い。I8・I10）。入れ替えられなければ
        画面に 1 行と `reexec_skipped` の行を残して戻る（I5）。例外を外へ出さない。"""
        running = ho.running_dir()
        if running is None:
            return
        t0 = time.time()
        stage, to = ho.UNREADABLE, None
        try:
            expected = ho.expected_dir(self.install_path, self.version)
            if expected == running:
                return
            if expected is None:
                raise ho.Skip(ho.UNREADABLE, "更新後のプラグインの導入先を読めない")
            base = ho.copy_base()
            stage = ho.STARTUP_FAILED
            to = ho.prepare(self.install_path, running, base)
            stage = ho.HANDOVER_WRITE
            path = ho.write(self.dir, self.handover(s, shown, running, to, t0))
            stage = ho.EXEC_FAILED
            self._exec(base, to, path)
        except ho.Skip as e:
            self._swap_skipped(e.reason, running, to, e.detail, t0)
        except Exception as e:  # 入れ替えの失敗で次のセッションを失わない
            self._swap_skipped(stage, running, to, f"想定外の例外（{type(e).__name__}）", t0)

    def handover(self, s: SectionInput, shown: str, running: str, to: str, t0: float) -> dict:
        """申し送りの中身（設計の申し送りの形の表。認証情報と環境変数を持たない）。"""
        now = time.time()
        return dict(
            shown=shown,
            from_dir=running,
            to_dir=to,
            written_at=now,
            prepare_seconds=round(now - t0, 3),
            claude=self.claude,
            marketplace=self.marketplace,
            version=self.version,
            first_args=list(self.first_args),
            state=dict(section=self.section, account=self.account, multi=self.multi, auth_section=self.auth_section),
            next=s.to_json(),
        )

    def _exec(self, base: str, to: str, path: str) -> None:
        """`count.lock` を放し、端末を入力を捨てずに戻して exec する。失敗したら元へ戻して Skip（exec-failed）。"""
        python, argv = ho.exec_argv(base, to, self.first_args)
        env = ho.exec_env(path)
        self.limit.release()
        self.term.restore(keep_input=True)
        sys.stdout.flush()
        sys.stderr.flush()
        try:
            os.execve(python, argv, env)
        except OSError as e:
            ho.discard(path)
            self.term.set_raw()
            end = time.time() + 5
            while not self.limit.take() and time.time() < end:
                time.sleep(0.1)
            raise ho.Skip(ho.EXEC_FAILED, f"{to} を起動できない（{e.strerror or type(e).__name__}）") from None

    def _swap_skipped(self, reason: str, running: str, to: str | None, detail: str, t0: float) -> None:
        row = {"section": self.section, "reason": reason, "from": running}
        if to:
            row["to"] = to
        if detail:
            row["detail"] = detail
        self.log(event="reexec_skipped", **row, seconds=ho.elapsed(t0))
        self.term.screen(f"ndf-relay: ラッパーを新しい版へ入れ替えずに今の版で続ける（{reason}: {detail or '理由は不明'}）")

    def restore_state(self, state: dict) -> None:
        """申し送りの状態を戻す（セッションの番号は続き。アカウントを切り替えるかは数え直さない）。"""
        self.section = state["section"]
        self.account = state["account"]
        self.auth_section = state["auth_section"]
        multi = bool(state["multi"])
        if multi != self.multi:
            self.multi = multi
            self.watch = UsageWatch(self) if multi else None

    def resume(self, h: dict) -> int:
        """入れ替えた後: `count.lock` を取り直し（上限は判定し直さない）、申し送りの次のセッションを起動して続ける。"""
        self.first_args = list(h["first_args"])
        end = time.time() + 5
        while not self.limit.take():
            if time.time() >= end:
                return self.give_up("count-lock", "起動の数を数えるロックが取れない", h["shown"])
            time.sleep(0.1)
        self.term.screen(f"── ndf-relay: 区間 {self.section + 1} ──")
        code = self._start_next_section(SectionInput.from_json(h["next"]), h["shown"])
        if code is not None:
            return code
        return self._serve()

    def close(self) -> None:
        self.stop_accounts()
        self.limit.release()
        remove(self.path(PID_FILE), self.path(cl.SETTINGS_FILE))  # まとめた設定は利用者の資格情報を持ちうるため残さない
        _unlock(self.lock)
        self.lock = None


def cmd_run(args: list[str]) -> int:
    path = os.environ.pop(ho.HANDOVER_ENV, None)  # 子の claude と hook へ継がせない
    if path:
        return run_from_handover(path)
    if os.environ.get("NDF_RELAY") == "0":
        claude = cl.resolve_claude()
        if claude is None:
            cl.say("本物の claude が見つからない")
            return 127
        cl.passthrough(claude, args)
    claude = cl.resolve_claude()
    if cl.depth() >= 2:
        cl.say(f"起動が入れ子になっている（{claude or '本物の claude が見つからない'}）")
        return 127
    if claude is None:
        cl.say("本物の claude が見つからない")
        return 127
    if os.environ.get("NDF_RELAY_DIR") or cl.needs_no_relay(args):
        cl.passthrough(claude, args)
    if not (os.isatty(0) and os.isatty(1)):
        cl.passthrough(claude, args)
    if not pty_available():
        cl.say("ラッパーを始めない（擬似端末を作れない）。カットポイントでは示されたコマンドを手で入力する")
        cl.passthrough(claude, args)
    got = cl.read_plugin(claude)
    if got is None:
        cl.say("ラッパーを始めない（ndf のプラグインの名前と版を読めない）。カットポイントでは示されたコマンドを手で入力する")
        cl.passthrough(claude, args)
    try:
        relay_dir = record.make_relay_dir()
    except OSError:
        cl.say("ラッパーを始めない（作業ディレクトリを作れない）。カットポイントでは示されたコマンドを手で入力する")
        cl.passthrough(claude, args)
    term = Terminal()
    relay = Relay(claude, relay_dir, got[0], got[1], term, StartLimit(os.path.join(relay_dir, LOG_FILE)))
    # 使うバージョンディレクトリに印を置く。startup はこの印のあるディレクトリを消さない
    inuse = version_dir.claim_inuse()
    return _serve_terminal(relay, term, inuse, lambda: relay.loop(args))


def run_from_handover(path: str) -> int:
    """入れ替えた後の `run`。起動の判定と `claude plugin list` を通さず、申し送りから続ける（決定 4）。"""
    d = os.path.dirname(path)
    h, shown = ho.read(path)
    if h is None:
        return handover_unreadable(d, shown)
    term = Terminal()
    relay = Relay(h["claude"], d, h["marketplace"], h["version"], term, StartLimit(os.path.join(d, LOG_FILE)))
    relay.restore_state(h["state"])
    # 新しい方へ印を置いてから古い方の印を消す（I11・決定 7。pid は exec で変わらない）
    inuse = version_dir.claim_inuse()
    if h["from_dir"] != os.path.basename(PKG_ROOT):
        version_dir.release_inuse(os.path.join(ho.copy_base(), h["from_dir"], f"inuse-{os.getpid()}"))
    relay.log(
        event="reexec",
        section=h["state"]["section"],
        **{"from": h["from_dir"], "to": h["to_dir"]},
        prepare_seconds=h.get("prepare_seconds"),
        seconds=ho.elapsed(h["written_at"]) if isinstance(h["written_at"], (int, float)) else None,
    )
    return _serve_terminal(relay, term, inuse, lambda: relay.resume(h))


def handover_unreadable(d: str, shown: str | None) -> int:
    """申し送りを読めない: 次のセッションを起動せず、手で打つコマンドを示して止まる（I6）。"""
    try:
        RelayRecord(d).log(event="stop", section=record.current_section(d), reason="handover")
    except OSError:
        pass
    command = shown or "claude を打ち直し、前の会話は /resume で選ぶ"
    Terminal.screen(f"ndf-relay: 次の区間を起動できない（ラッパーの入れ替えの申し送りを読めない）。次のコマンド:\r\n{command}")
    return 2


def _serve_terminal(relay: Relay, term: Terminal, inuse: str | None, body) -> int:
    """端末を raw にして `body` を動かし、終わったら端末・ラッパー・使用中の印を片づける。"""

    def on_signal(signum, _frame):
        term.restore()
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGHUP, on_signal)
    signal.signal(signal.SIGWINCH, lambda *_: term.copy_winsize())
    try:
        term.set_raw()
        return body()
    finally:
        term.restore()
        relay.close()
        version_dir.release_inuse(inuse)
