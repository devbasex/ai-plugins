"""端末と、擬似端末の子の claude（#895・#1142 の C6）。

入出力の中継・子の起動と停止・端末の raw の設定と戻しを持つ。区間の切り替えの判断は `run.Relay` が持つ。
擬似端末の子の起動と窓の大きさは ptyprocess で扱い、このモジュールがその包みを兼ねる（決定 19・20）。
`pty`・`termios` を import するのもこのモジュールだけである（構造チェックの I14）。
"""
from __future__ import annotations

import errno
import os
import select
import signal
import time

from ptyprocess import PtyProcess

from .common import env_num


class StartFailed(Exception):
    def __init__(self, err: int):
        super().__init__(err)
        self.err = err


def pty_available() -> bool:
    """擬似端末と端末の設定を使えるか（使えない OS ではラッパーを始めない）。"""
    try:
        import pty  # noqa: F401
        import termios  # noqa: F401
        import tty  # noqa: F401
    except ImportError:
        return False
    return True


def wait_exit_code(status: int) -> int:
    """`waitpid` の状態をシェルと同じ終了コードへ（シグナルなら 128 + 番号）。"""
    code = os.waitstatus_to_exitcode(status)
    return 128 - code if code < 0 else code


class Terminal:
    """端末と子の擬似端末のあいだで入出力を中継し、子の終わりを拾う。"""

    def __init__(self):
        self.pid = 0
        self.fd = -1
        self.child: PtyProcess | None = None
        self.last_input = 0.0
        self.last_tick = 0.0
        self.stdin_open = True
        self.poll = env_num("NDF_RELAY_POLL", 2)
        self.saved = None

    # -- 端末の設定

    def set_raw(self) -> None:
        """今の設定を控えてから raw にする。戻すのは `restore`。"""
        import termios
        import tty
        self.saved = termios.tcgetattr(0)
        tty.setraw(0)

    def restore(self) -> None:
        import termios
        if self.saved is None:
            return
        try:
            termios.tcsetattr(0, termios.TCSAFLUSH, self.saved)
        except (OSError, termios.error):
            pass

    @staticmethod
    def winsize() -> tuple[int, int]:
        """端末の (行, 列)。端末でなければ ptyprocess の既定の 24 × 80。"""
        try:
            size = os.get_terminal_size(0)
        except OSError:
            return 24, 80
        return size.lines, size.columns

    def copy_winsize(self) -> None:
        """端末の大きさを子の擬似端末へ写す（SIGWINCH のたび）。"""
        if self.child is None or self.fd < 0:
            return
        try:
            self.child.setwinsize(*self.winsize())
        except OSError:
            pass

    @staticmethod
    def screen(line: str) -> None:
        """端末は raw なので行の頭へ戻してから書く。"""
        try:
            os.write(1, ("\r\n" + line + "\r\n").encode())
        except OSError:
            pass

    # -- 子の起動と停止

    def spawn(self, claude: str, args: list[str], cwd: str, env: dict, child_file: str) -> float:
        """claude を擬似端末の子として起動し、`child_file` へ pid を書く。起動の時刻を返す。

        pid は子が exec する前に子自身が書く（子の hook が `child.pid` を読むより先に在る）。
        起動できなければ errno を持つ StartFailed。"""
        def write_pid() -> None:
            with open(child_file, "w") as f:
                f.write(str(os.getpid()))

        at = time.time()
        try:
            child = PtyProcess.spawn([claude, *args], cwd=cwd, env=env, preexec_fn=write_pid,
                                     dimensions=self.winsize())
        except OSError as e:
            # ptyprocess は実行できないコマンドを exec の前に errno なしで断る
            raise StartFailed(e.errno or (errno.EACCES if os.path.exists(claude) else errno.ENOENT)) from None
        child.delayafterclose = 0
        self.child, self.pid, self.fd = child, child.pid, child.fd
        return at

    def stop_child(self) -> tuple[str, int]:
        """SIGTERM、SIGKILL の順に子を停止し、終了理由と wait 状態を返す。"""
        for sig, how, wait in ((signal.SIGTERM, "sigterm", env_num("NDF_RELAY_TERM_WAIT", 10)),
                               (signal.SIGKILL, "sigkill", None)):
            try:
                os.kill(self.pid, sig)
            except ProcessLookupError:
                pass
            res = self.pump(until=None if wait is None else time.time() + wait)
            if res:
                return how, res[1]
        return "sigkill", 0

    # -- 中継

    def pump(self, until: float | None = None, tick=None):
        """入出力を中継する。子が終われば ("exit", status)、tick が値を返せば ("tick", 値)、
        期限に達すれば None を返す。"""
        master_open = True
        while True:
            status = self._reap()
            if status is not None:
                return ("exit", status)
            if until is not None and time.time() >= until:
                return None
            fds = [self.fd] if master_open else []
            if self.stdin_open:
                fds.append(0)
            try:
                r, _, _ = select.select(fds, [], [], 0.1)
            except InterruptedError:
                r = []
            master_open = self._relay_ready(r, master_open)
            result = self._due_tick(tick)
            if result is not None:
                return result

    def _reap(self) -> int | None:
        """子が終わっていれば残りの出力を流して fd を閉じ、終了状態を返す。"""
        try:
            wpid, status = os.waitpid(self.pid, os.WNOHANG)
        except ChildProcessError:
            wpid, status = self.pid, 0
        if not wpid:
            return None
        self.drain()
        self._close_child()
        return status

    def _close_child(self) -> None:
        """刈り取った子の擬似端末を閉じる。子は刈り取り済みなので、ptyprocess に止めさせない。"""
        if self.child is not None:
            self.child.terminated = True
            try:
                self.child.close()
            except OSError:
                pass
        self.child, self.fd = None, -1

    def _relay_ready(self, r: list[int], master_open: bool) -> bool:
        """読める fd の入出力を転送し、master が開いたままかを返す。"""
        if self.fd in r:
            try:
                data = os.read(self.fd, 65536)
            except OSError:
                data = b""
            if data:
                self.write_out(data)
            else:
                master_open = False
        if 0 in r:
            try:
                data = os.read(0, 4096)
            except OSError:
                data = b""
            if data:
                self.last_input = time.time()
                try:
                    os.write(self.fd, data)
                except OSError:
                    pass
            else:
                self.stdin_open = False
        return master_open

    def _due_tick(self, tick):
        """期限の来た tick を実行し、値を返せば ("tick", 値) を返す。"""
        if tick is not None and time.time() - self.last_tick >= self.poll:
            self.last_tick = time.time()
            v = tick()
            if v is not None:
                return ("tick", v)
        return None

    def drain(self) -> None:
        while True:
            try:
                r, _, _ = select.select([self.fd], [], [], 0)
                if not r:
                    return
                data = os.read(self.fd, 65536)
            except OSError:
                return
            if not data:
                return
            self.write_out(data)

    @staticmethod
    def write_out(data: bytes) -> None:
        while data:
            try:
                n = os.write(1, data)
            except InterruptedError:
                continue
            except OSError:
                return
            data = data[n:]
