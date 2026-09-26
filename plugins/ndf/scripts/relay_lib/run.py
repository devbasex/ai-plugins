"""`run`: 前景に常駐し、区間ごとの claude を擬似端末の子として起動する（#895・#1142 の C6）。

`Relay` はセッションを切り替える状態機械である。合図の判定の材料（会話の記録）は `claude`、
入出力と子の起動は `terminal`、記録は `record` が持つ。
"""
from __future__ import annotations

import os
import shlex
import signal
import time

from . import claude as cl
from . import record, version_dir
from .common import (CHILD_FILE, LOCK_FILE, LOG_FILE, MARK_FILE, PID_FILE, QUESTION_FILE, QUESTION_LOCK,
                     STOP_FILE, _lock, _unlock, env_num, fallback_cwd, parse_iso, quiet_seconds, remove, stamp)
from .mark import asked_after
from .record import RelayRecord, StartLimit
from .terminal import StartFailed, Terminal, pty_available, wait_exit_code


class Relay:
    """前景に常駐し、区間ごとの claude を擬似端末の子として起動する。"""

    def __init__(self, claude: str, relay_dir: str, marketplace: str, version: str,
                 term: Terminal, limit: StartLimit):
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
        self.lock = _lock(self.path(LOCK_FILE), None)  # 動いている間は持ち続ける（relay_running が見る）
        with open(self.path(PID_FILE), "w") as f:
            f.write(str(os.getpid()))

    def path(self, name: str) -> str:
        return os.path.join(self.dir, name)

    def log(self, **row) -> None:
        self.record.log(**row)

    # -- 子の起動

    def start_section(self, args: list[str], cwd: str, command: str, from_session: str,
                      cwd_fallback: str | None = None, carried: list[str] | None = None) -> None:
        self.record.drop_mark()
        remove(self.path(QUESTION_FILE))
        at = self.term.spawn(self.claude, [*(carried or []), *args], cwd, self.env, self.path(CHILD_FILE))
        self.section += 1
        self.started_at = at
        row = dict(event="start", at=stamp(at), section=self.section, pid=self.term.pid,
                   command=command, from_session=from_session,
                   plugin_version=self.version, cwd=cwd)
        if cwd_fallback is not None:
            row["cwd_fallback"] = cwd_fallback
        if carried is not None:
            row["carried"] = carried
        self.log(**row)
        self.limit.release()

    # -- 合図の判定

    def read_mark(self):
        return self.record.read_mark()

    def tick(self):
        if self.halted:
            return None
        try:
            return self._tick()
        except Exception as e:  # 本体の例外で子を巻き込まない
            self.halt("error", f"ラッパーの中で例外が起きた（{type(e).__name__}）")
            return None

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
        if (os.path.exists(self.path(QUESTION_FILE)) or cancel
                or asked_after(self.dir, self.path(MARK_FILE))):
            return None
        if not unmet and cl.replied_after(tp, written):
            return None
        if os.path.exists(self.path(STOP_FILE)):
            self.halt("stop-file", "停止の合図がある")
            return None
        if not self.limit.take():
            return None
        refusal = self.limit.refusal(written, self.started_at)
        if refusal:
            self.limit.release()
            self.halt(*refusal)
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
        self.term.screen(f"ndf-relay: 次の区間を起動しない（{why}）。このまま続けるか、"
                         "/exit して示されたコマンドを手で入力する")

    # -- 切り替え

    def _still_due(self, m) -> bool:
        """`/exit` を書く直前の確かめ直し。質問が無く、合図が同じで、取りやめの行が無いか。"""
        now = self.read_mark()
        if (os.path.exists(self.path(QUESTION_FILE)) or now is None
                or now.get("written_at") != m.get("written_at")
                or asked_after(self.dir, self.path(MARK_FILE))):
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
        self.log(event="end", section=self.section, pid=self.term.pid,
                 seconds=round(until - self.started_at, 3), ended_by=ended_by)

    def finalize_section(self, m, written: float) -> tuple[dict | None, int | None]:
        """`/exit` の後の後処理。子を終わらせ、読み直した合図から続ける合図か終了コードを決める。
        `event="end"` はここで 1 度だけ記録する。続けるなら (合図, None)、終わるなら (None, 終了コード)。"""
        ended_by, status, questioned = self.end_child()
        again = self.read_mark()
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
                return None, self.give_up(why[0], why[1], again["command"])
            return again, None
        return m, None

    def prepare_next(self, m) -> tuple[str, str | None] | None:
        """プラグインを更新し、合図から次の区間の (cwd, 退避前の cwd) を決める。更新に失敗したら None。"""
        version = cl.update_plugin(self.claude, self.marketplace)
        if version is None:
            return None
        self.version = version
        cwd = m.get("cwd") or os.getcwd()
        fb = None
        if not os.path.isdir(cwd):
            fb, cwd = cwd, fallback_cwd(cwd)
        return cwd, fb

    def loop(self, first_args: list[str]) -> int:
        carried = cl.carried_args(first_args)
        try:
            self.start_section(first_args, os.getcwd(), shlex.join(first_args), "")
        except StartFailed as e:
            self.log(event="stop", section=self.section + 1, reason="start-failed", errno=e.err)
            cl.say(f"claude を起動できない（{os.strerror(e.err)}）")
            return 127
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
            command = m["command"]
            nxt = self.prepare_next(m)
            if nxt is None:
                return self.give_up("update-failed", "プラグインの更新か版の読み取りに失敗した", command)
            cwd, fb = nxt
            self.term.screen(f"── ndf-relay: 区間 {self.section + 1} ──")
            try:
                self.start_section([command], cwd, command, m.get("session_id") or "", fb, carried)
            except StartFailed as e:
                return self.give_up("start-failed", f"claude を起動できない（{os.strerror(e.err)}）",
                                    command, errno=e.err)

    def close(self) -> None:
        self.limit.release()
        remove(self.path(PID_FILE))
        _unlock(self.lock)
        self.lock = None


def cmd_run(args: list[str]) -> int:
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
    relay = Relay(claude, relay_dir, got[0], got[1], term,
                  StartLimit(os.path.join(relay_dir, LOG_FILE)))
    # 使うバージョンディレクトリに印を置く。startup はこの印のあるディレクトリを消さない
    inuse = version_dir.claim_inuse()

    def on_signal(signum, _frame):
        term.restore()
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGHUP, on_signal)
    signal.signal(signal.SIGWINCH, lambda *_: term.copy_winsize())
    try:
        term.set_raw()
        return relay.loop(args)
    finally:
        term.restore()
        relay.close()
        version_dir.release_inuse(inuse)
