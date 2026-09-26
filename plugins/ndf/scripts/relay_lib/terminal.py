"""端末と、擬似端末の子の claude（#895・#1142 の C6）。

入出力の中継・子の起動と停止・端末の raw の設定と戻しを持つ。区間の切り替えの判断は `run.Relay` が持つ。
"""
from __future__ import annotations

import errno
import fcntl
import os
import select
import signal
import time

from .common import env_num


class StartFailed(Exception):
    def __init__(self, err: int):
        super().__init__(err)
        self.err = err


def wait_exit_code(status: int) -> int:
    """`waitpid` の状態をシェルと同じ終了コードへ（シグナルなら 128 + 番号）。"""
    code = os.waitstatus_to_exitcode(status)
    return 128 - code if code < 0 else code


class Terminal:
    """端末と子の擬似端末のあいだで入出力を中継し、子の終わりを拾う。"""

    def __init__(self):
        self.pid = 0
        self.fd = -1
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

    def copy_winsize(self, fd: int | None = None) -> None:
        import termios
        fd = self.fd if fd is None else fd
        if fd < 0:
            return
        try:
            ws = fcntl.ioctl(0, termios.TIOCGWINSZ, b"\0" * 8)
            fcntl.ioctl(fd, termios.TIOCSWINSZ, ws)
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

    @staticmethod
    def _child_exec(sync_r: int, sync_w: int, res_r: int, res_w: int,
                    claude: str, args: list[str], cwd: str, env: dict) -> None:
        """子側: 親だけが使う fd を閉じ、同期を待って chdir と execve を行う。
        失敗したら errno を結果 pipe へ書き、127 で終わる。"""
        try:
            os.close(sync_w)
            os.close(res_r)
            os.read(sync_r, 1)
            os.chdir(cwd)
            os.execve(claude, [claude] + args, env)
        except OSError as e:
            os.write(res_w, str(e.errno or errno.EIO).encode())
        finally:
            os._exit(127)

    @staticmethod
    def _read_errno(res_r: int) -> bytes:
        """親側: 結果 pipe を最後まで読む。InterruptedError は読み直す。"""
        data = b""
        while True:
            try:
                chunk = os.read(res_r, 64)
            except InterruptedError:
                continue
            if not chunk:
                break
            data += chunk
        os.close(res_r)
        return data

    def spawn(self, claude: str, args: list[str], cwd: str, env: dict, child_file: str) -> float:
        """claude を擬似端末の子として起動し、`child_file` へ pid を書く。起動の時刻を返す。"""
        import pty
        sync_r, sync_w = os.pipe()
        res_r, res_w = os.pipe()
        pid, fd = pty.fork()
        if pid == 0:
            self._child_exec(sync_r, sync_w, res_r, res_w, claude, args, cwd, env)
        os.close(sync_r)
        os.close(res_w)
        self.copy_winsize(fd)
        with open(child_file, "w") as f:
            f.write(str(pid))
        at = time.time()
        os.close(sync_w)
        data = self._read_errno(res_r)
        if data:
            os.waitpid(pid, 0)
            os.close(fd)
            raise StartFailed(int(data or errno.EIO))
        self.pid, self.fd = pid, fd
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
        os.close(self.fd)
        self.fd = -1
        return status

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
