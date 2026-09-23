#!/usr/bin/env python3
"""NDF の中継: 区間の切れ目で claude を起動し直す（#895）。

副命令:

| 副命令 | 役割 |
| --- | --- |
| `run [claude の引数 ...]` | 端末の前景に常駐し、claude を擬似端末の子として起動する。印を受けたら子へ `/exit` を入力し、プラグインを更新して次の区間を起動する。中継が要らない起動は本物の claude をそのまま exec する（素通し） |
| `stop` | 動いている中継すべてに停止の印を置く |
| `mark` | Stop hook の本体。最後の応答の `ndf-next` のブロックを印 `next.json` へ写す |
| `install` | SessionStart hook の本体。中継を安定した場所へ置き直し、bash / zsh の設定へ alias を 1 度だけ足す |

**標準ライブラリだけで書く。** 印と作業ディレクトリの形（`next.json` のキーと
`NDF_RELAY_DIR` のファイル）は版をまたいで変えない。hook は区間ごとに新しい版で動き、
動いている中継は古い版のままでありうるためである。

規約は skills/development-workflow/references/relay.md にある。
"""
from __future__ import annotations

import datetime as _dt
import errno
import fcntl
import json
import os
import random
import re
import select
import shlex
import signal
import subprocess
import sys
import time

MARK_FILE = "next.json"
STOP_FILE = "stop"
LOCK_FILE = "relay.lock"
PID_FILE = "relay.pid"
CHILD_FILE = "child.pid"
LOG_FILE = "log.jsonl"
COUNT_LOCK = "count.lock"
INSTALL_LOCK = "install.lock"

BLOCK_OPEN = "# >>> ndf relay >>>"
BLOCK_CLOSE = "# <<< ndf relay <<<"
ALIAS_LINE = """alias claude='python3 "${XDG_DATA_HOME:-$HOME/.local/share}/ndf/relay.py" run'"""
RC_BLOCK = (f"{BLOCK_OPEN}\n"
            "# ndf の中継（区間の切れ目で claude を自動で起動し直す）。消せば元に戻る。\n"
            f"{ALIAS_LINE}\n"
            f"{BLOCK_CLOSE}\n")

# 素通しにする引数と副命令（Claude Code 2.1.280 の `claude --help` から写す）
PASS_FLAGS = {"-p", "--print", "-h", "--help", "-v", "--version"}
SUBCOMMANDS = {
    "agents", "attach", "auth", "auto-mode", "doctor", "gateway", "import", "install",
    "logs", "mcp", "plugin", "plugins", "project", "respawn", "rm", "setup-token",
    "stop", "kill", "ultrareview", "update", "upgrade",
}
# 子へ継がせない Claude Code の環境変数（中から起こしたプロセスが継ぐもの）
DROP_ENV = ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT")


def _num(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


# ---------------------------------------------------------------- 共通


def now_iso(t: float | None = None) -> str:
    t = time.time() if t is None else t
    d = _dt.datetime.fromtimestamp(t, _dt.timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 1000:03d}Z"


def parse_iso(s) -> float | None:
    if not isinstance(s, str):
        return None
    try:
        return _dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=_dt.timezone.utc).timestamp()
    except ValueError:
        return None


def state_root() -> str:
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "ndf", "relay")


def data_dir() -> str:
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "ndf")


def relay_running(d: str) -> bool:
    """`relay.lock` の排他が取れなければ中継が動いている。pid の生死では見ない。"""
    try:
        fd = os.open(os.path.join(d, LOCK_FILE), os.O_RDWR)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def write_json_atomic(path: str, data) -> None:
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def read_json(path: str):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def remove(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


# ---------------------------------------------------------------- 親のたどり


def proc_info(pid: int) -> tuple[int, str] | None:
    """(親の pid, 名前)。Linux は /proc、それ以外は ps で読む。"""
    try:
        with open(f"/proc/{pid}/stat") as f:
            s = f.read()
        name = s[s.index("(") + 1:s.rindex(")")]
        ppid = int(s[s.rindex(")") + 2:].split()[1])
        return ppid, name
    except (OSError, ValueError, IndexError):
        pass
    try:
        out = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)], capture_output=True,
                             text=True, timeout=2).stdout.strip()
        ppid, name = out.split(None, 1)
        return int(ppid), os.path.basename(name)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def is_direct_child(d: str, start: int | None = None) -> bool:
    """hook の親をたどって最初に当たる claude が、中継の起動した子（`child.pid`）かを見る。

    claude は名前（`claude`）か、子と同じ名前か、pid そのもので見分ける。
    conductor が Bash から起こした `claude -p` は子の claude の孫に当たり、先にそちらに当たる。
    """
    try:
        with open(os.path.join(d, CHILD_FILE)) as f:
            child = int(f.read().strip())
    except (OSError, ValueError):
        return False
    info = proc_info(child)
    child_name = info[1] if info else None
    pid = os.getppid() if start is None else start
    for _ in range(64):
        if pid <= 1:
            return False
        if pid == child:
            return True
        info = proc_info(pid)
        if info is None:
            return False
        ppid, name = info
        if name == "claude" or (child_name is not None and name == child_name):
            return False
        pid = ppid
    return False


# ---------------------------------------------------------------- mark


FENCE_RE = re.compile(r"^(`{3,})(.*)$")


def next_blocks(text: str) -> list[str]:
    """外側の囲みの中を除き、情報文字列が `ndf-next` の 3 つのバッククォートの囲みの中身を返す。"""
    blocks: list[str] = []
    open_len = 0
    cur: list[str] | None = None
    for line in text.split("\n"):
        m = FENCE_RE.match(line.rstrip("\r"))
        if not open_len:
            if m and "`" not in m.group(2):
                open_len = len(m.group(1))
                cur = [] if open_len == 3 and m.group(2).strip() == "ndf-next" else None
            continue
        if m and len(m.group(1)) >= open_len and not m.group(2).strip():
            if cur is not None:
                blocks.append("\n".join(cur).strip("\n"))
            open_len, cur = 0, None
        elif cur is not None:
            cur.append(line)
    return blocks


def background_running(tasks) -> bool:
    if not isinstance(tasks, list):
        return False
    return any(isinstance(t, dict) and t.get("status") == "running" for t in tasks)


def cmd_mark() -> int:
    d = os.environ.get("NDF_RELAY_DIR")
    if not d or not relay_running(d):
        return 0
    try:
        data = json.loads(sys.stdin.read())
    except ValueError:
        return 0
    if not isinstance(data, dict) or not is_direct_child(d):
        return 0
    path = os.path.join(d, MARK_FILE)
    blocks = next_blocks(str(data.get("last_assistant_message") or ""))
    if len(blocks) != 1 or background_running(data.get("background_tasks")):
        remove(path)
        return 0
    write_json_atomic(path, {
        "command": blocks[0],
        "cwd": data.get("cwd") or "",
        "session_id": data.get("session_id") or "",
        "transcript_path": data.get("transcript_path") or "",
        "written_at": now_iso(),
    })
    return 0


# ---------------------------------------------------------------- 本物の claude


def _is_self(path: str) -> bool:
    real = os.path.realpath(path)
    mine = {os.path.realpath(__file__), os.path.realpath(os.path.join(data_dir(), "relay.py"))}
    if real in mine:
        return True
    try:
        with open(real, "rb") as f:
            return b"relay.py" in f.read(4096)
    except OSError:
        # 読めないものは中継と見なさない。飛ばし損ねた繰り返しは NDF_RELAY_DEPTH が止める
        return False


def resolve_claude() -> str | None:
    """alias は子に効かないため実体を探す。`claude` という名前で中継を呼ぶラッパーを飛ばす。"""
    forced = os.environ.get("NDF_RELAY_CLAUDE")
    if forced:
        return forced
    for d in os.environ.get("PATH", "").split(os.pathsep):
        p = os.path.join(d or ".", "claude")
        if os.path.isfile(p) and os.access(p, os.X_OK) and not _is_self(p):
            return os.path.abspath(p)
    return None


def needs_no_relay(args: list[str]) -> bool:
    if any(a in PASS_FLAGS or a.startswith("--print=") for a in args):
        return True
    return bool(args) and args[0] in SUBCOMMANDS


def say(msg: str) -> None:
    """`ndf-relay:` の 1 行を標準エラーへ出す。"""
    sys.stderr.write(f"ndf-relay: {msg}\n")
    sys.stderr.flush()


def depth() -> int:
    try:
        return int(os.environ.get("NDF_RELAY_DEPTH") or 0)
    except ValueError:
        return 0


def passthrough(claude: str, args: list[str]):
    """環境は入れ子を数える変数だけを足して、本物の claude を exec する。"""
    env = dict(os.environ)
    env["NDF_RELAY_DEPTH"] = str(depth() + 1)
    os.execve(claude, [claude] + args, env)


def child_env(relay_dir: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in DROP_ENV}
    env["NDF_RELAY_DEPTH"] = str(depth() + 1)
    env["NDF_RELAY_DIR"] = relay_dir
    return env


def plugin_cli(claude: str, args: list[str], timeout: float) -> subprocess.CompletedProcess | None:
    env = {k: v for k, v in os.environ.items() if k not in DROP_ENV and k != "NDF_RELAY_DIR"}
    try:
        return subprocess.run([claude, "plugin"] + args, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=timeout, env=env)
    except (OSError, subprocess.SubprocessError):
        return None


def read_plugin(claude: str, marketplace: str | None = None) -> tuple[str, str] | None:
    """`claude plugin list --json` から (マーケットプレイス, 版) を読む。"""
    p = plugin_cli(claude, ["list", "--json"], _num("NDF_RELAY_LIST_TIMEOUT", 15))
    if p is None or p.returncode != 0:
        return None
    try:
        items = json.loads(p.stdout)
    except ValueError:
        return None
    for it in items if isinstance(items, list) else []:
        pid = it.get("id") if isinstance(it, dict) else None
        if not isinstance(pid, str) or not pid.startswith("ndf@"):
            continue
        mk = pid.split("@", 1)[1]
        if marketplace is not None and mk != marketplace:
            continue
        ver = it.get("version")
        if mk and isinstance(ver, str) and ver:
            return mk, ver
    return None


# ---------------------------------------------------------------- run


class StartFailed(Exception):
    def __init__(self, err: int):
        super().__init__(err)
        self.err = err


def exit_code(status: int) -> int:
    code = os.waitstatus_to_exitcode(status)
    return 128 - code if code < 0 else code


def fallback_cwd(cwd: str) -> str:
    """印の作業ディレクトリが消えていたら、主ディレクトリか在る最も近い親を返す。"""
    if "/.worktrees/" in cwd:
        main = cwd.split("/.worktrees/", 1)[0]
        if os.path.isdir(main):
            return main
    p = cwd
    while p and not os.path.isdir(p):
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    return p if p and os.path.isdir(p) else os.path.expanduser("~")


def make_relay_dir() -> str:
    root = state_root()
    os.makedirs(root, mode=0o700, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    for _ in range(20):
        d = os.path.join(root, f"{stamp}-{os.getpid()}-{random.randint(0, 99999999):08d}")
        try:
            os.mkdir(d, 0o700)
            return d
        except FileExistsError:
            continue
    raise OSError(errno.EEXIST, "relay dir")


class Relay:
    """前景に常駐し、区間ごとの claude を擬似端末の子として起動する。"""

    def __init__(self, claude: str, relay_dir: str, marketplace: str, version: str):
        self.claude = claude
        self.dir = relay_dir
        self.marketplace = marketplace
        self.version = version
        self.env = child_env(relay_dir)
        self.section = 0
        self.pid = 0
        self.fd = -1
        self.started_at = 0.0
        self.session_id = ""
        self.halted = False
        self.last_input = 0.0
        self.last_tick = 0.0
        self.stdin_open = True
        self.count_lock: int | None = None
        self.quiet = _num("NDF_RELAY_QUIET", 15)
        self.poll = _num("NDF_RELAY_POLL", 2)
        self.max_starts = int(_num("NDF_RELAY_MAX_STARTS", 20))
        self.spin = _num("NDF_RELAY_SPIN", 120)
        self.lock_fd = os.open(self.path(LOCK_FILE), os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self.lock_fd, fcntl.LOCK_EX)
        with open(self.path(PID_FILE), "w") as f:
            f.write(str(os.getpid()))

    def path(self, name: str) -> str:
        return os.path.join(self.dir, name)

    # -- 記録

    def log(self, **row) -> None:
        row = {"event": row.pop("event"), "at": row.pop("at", None) or now_iso(), **row}
        with open(self.path(LOG_FILE), "a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def screen(self, line: str) -> None:
        """端末は raw なので行の頭へ戻してから書く。"""
        try:
            os.write(1, ("\r\n" + line + "\r\n").encode())
        except OSError:
            pass

    # -- 子の起動

    def spawn(self, args: list[str], cwd: str) -> float:
        import pty
        sync_r, sync_w = os.pipe()
        res_r, res_w = os.pipe()
        pid, fd = pty.fork()
        if pid == 0:
            try:
                os.close(sync_w)
                os.close(res_r)
                os.read(sync_r, 1)
                os.chdir(cwd)
                os.execve(self.claude, [self.claude] + args, self.env)
            except OSError as e:
                os.write(res_w, str(e.errno or errno.EIO).encode())
            finally:
                os._exit(127)
        os.close(sync_r)
        os.close(res_w)
        self.copy_winsize(fd)
        with open(self.path(CHILD_FILE), "w") as f:
            f.write(str(pid))
        at = time.time()
        os.close(sync_w)
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
        if data:
            os.waitpid(pid, 0)
            os.close(fd)
            raise StartFailed(int(data or errno.EIO))
        self.pid, self.fd = pid, fd
        return at

    def start_section(self, args: list[str], cwd: str, command: str, from_session: str,
                      cwd_fallback: str | None = None) -> None:
        remove(self.path(MARK_FILE))
        at = self.spawn(args, cwd)
        self.section += 1
        self.started_at = at
        row = dict(event="start", at=now_iso(at), section=self.section, pid=self.pid,
                   command=command, from_session=from_session,
                   plugin_version=self.version, cwd=cwd)
        if cwd_fallback is not None:
            row["cwd_fallback"] = cwd_fallback
        self.log(**row)
        self.release_count()

    # -- 端末

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

    def pump(self, until: float | None = None, tick=None):
        """入出力を中継する。子が終われば ("exit", status)、tick が値を返せば ("tick", 値)、
        期限に達すれば None を返す。"""
        master_open = True
        while True:
            try:
                wpid, status = os.waitpid(self.pid, os.WNOHANG)
            except ChildProcessError:
                wpid, status = self.pid, 0
            if wpid:
                self.drain()
                os.close(self.fd)
                self.fd = -1
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
            if tick is not None and time.time() - self.last_tick >= self.poll:
                self.last_tick = time.time()
                v = tick()
                if v is not None:
                    return ("tick", v)

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

    # -- 印の判定

    def read_mark(self):
        m = read_json(self.path(MARK_FILE))
        if not isinstance(m, dict) or not isinstance(m.get("command"), str) or not m["command"]:
            return None
        return m

    def tick(self):
        if self.halted:
            return None
        try:
            return self._tick()
        except Exception as e:  # 本体の例外で子を巻き込まない
            self.halt("error", f"中継の中で例外が起きた（{type(e).__name__}）")
            return None

    def _tick(self):
        m = self.read_mark()
        if m is None:
            return None
        written = parse_iso(m.get("written_at")) or time.time()
        latest = max(written, self.last_input)
        tp = m.get("transcript_path") or ""
        try:
            latest = max(latest, os.stat(tp).st_mtime)
        except OSError:
            pass
        if time.time() - latest < self.quiet:
            return None
        if goal_pending(tp, written):
            return None
        if os.path.exists(self.path(STOP_FILE)):
            self.halt("stop-file", "停止の印がある")
            return None
        if not self.take_count():
            return None
        if self.count_today() >= self.max_starts:
            self.release_count()
            self.halt("max-starts", f"1 日の起動回数が上限 {self.max_starts} に達した")
            return None
        if self.spinning(written):
            self.release_count()
            self.halt("spin", f"区間が 3 つ続けて {int(self.spin)} 秒未満で切れ目に達した")
            return None
        return m

    def halt(self, reason: str, why: str) -> None:
        self.halted = True
        self.log(event="stop", section=self.section, reason=reason)
        remove(self.path(MARK_FILE))
        self.screen(f"ndf-relay: 次の区間を起動しない（{why}）。このまま続けるか、"
                    "/exit して示されたコマンドを手で入力する")

    def take_count(self) -> bool:
        if self.count_lock is not None:
            return True
        fd = os.open(os.path.join(state_root(), COUNT_LOCK), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self.count_lock = fd
        return True

    def release_count(self) -> None:
        if self.count_lock is not None:
            fcntl.flock(self.count_lock, fcntl.LOCK_UN)
            os.close(self.count_lock)
            self.count_lock = None

    def count_today(self) -> int:
        today = time.localtime().tm_yday, time.localtime().tm_year
        n = 0
        root = state_root()
        for name in os.listdir(root):
            try:
                with open(os.path.join(root, name, LOG_FILE)) as f:
                    for line in f:
                        try:
                            row = json.loads(line)
                        except ValueError:
                            continue
                        if row.get("event") != "start":
                            continue
                        t = parse_iso(row.get("at"))
                        if t is not None:
                            lt = time.localtime(t)
                            n += (lt.tm_yday, lt.tm_year) == today
            except OSError:
                continue
        return n

    def spinning(self, written: float) -> bool:
        ends = []
        try:
            with open(self.path(LOG_FILE)) as f:
                for line in f:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if row.get("event") == "end":
                        ends.append(row.get("seconds"))
        except OSError:
            return False
        last = ends[-2:]
        if len(last) < 2 or not all(isinstance(s, (int, float)) and s < self.spin for s in last):
            return False
        return written - self.started_at < self.spin

    # -- 切り替え

    def end_child(self) -> str:
        """子へ `/exit` を入力して終わらせる。30 秒で SIGTERM、さらに 10 秒で SIGKILL。"""
        waits = [("/exit", _num("NDF_RELAY_EXIT_GAP", 1)), ("\r", _num("NDF_RELAY_EXIT_WAIT", 30))]
        for keys, wait in waits:
            try:
                os.write(self.fd, keys.encode())
            except OSError:
                pass
            if self.pump(until=time.time() + wait):
                return "mark"
        for sig, how, wait in ((signal.SIGTERM, "sigterm", _num("NDF_RELAY_TERM_WAIT", 10)),
                               (signal.SIGKILL, "sigkill", None)):
            try:
                os.kill(self.pid, sig)
            except ProcessLookupError:
                pass
            if self.pump(until=None if wait is None else time.time() + wait):
                return how
        return "sigkill"

    def update(self) -> str | None:
        t = _num("NDF_RELAY_UPDATE_TIMEOUT", 120)
        for args in (["marketplace", "update", self.marketplace],
                     ["update", f"ndf@{self.marketplace}", "-y"]):
            p = plugin_cli(self.claude, args, t)
            if p is None or p.returncode != 0:
                return None
        got = read_plugin(self.claude, self.marketplace)
        return got[1] if got else None

    def give_up(self, reason: str, why: str, command: str, **extra) -> int:
        self.release_count()
        self.log(event="stop", section=self.section, reason=reason, **extra)
        self.screen(f"ndf-relay: 次の区間を起動できない（{why}）。次のコマンド:\r\n{command}")
        return 2

    def loop(self, first_args: list[str]) -> int:
        try:
            at = self.spawn(first_args, os.getcwd())
        except StartFailed as e:
            self.log(event="stop", section=1, reason="start-failed", errno=e.err)
            say(f"claude を起動できない（{os.strerror(e.err)}）")
            return 127
        self.section = 1
        self.started_at = at
        self.log(event="start", at=now_iso(at), section=1, pid=self.pid,
                 command=shlex.join(first_args), from_session="",
                 plugin_version=self.version, cwd=os.getcwd())
        while True:
            res = self.pump(tick=self.tick)
            if res[0] == "exit":
                self.log(event="end", section=self.section, pid=self.pid,
                         seconds=round(time.time() - self.started_at, 3), ended_by="no-mark")
                return exit_code(res[1])
            m = res[1]
            written = parse_iso(m.get("written_at")) or time.time()
            ended_by = self.end_child()
            self.log(event="end", section=self.section, pid=self.pid,
                     seconds=round(written - self.started_at, 3), ended_by=ended_by)
            command = m["command"]
            version = self.update()
            if version is None:
                return self.give_up("update-failed", "プラグインの更新か版の読み取りに失敗した", command)
            self.version = version
            cwd = m.get("cwd") or os.getcwd()
            fb = None
            if not os.path.isdir(cwd):
                fb, cwd = cwd, fallback_cwd(cwd)
            self.screen(f"── ndf-relay: 区間 {self.section + 1} ──")
            try:
                self.start_section([command], cwd, command, m.get("session_id") or "", fb)
            except StartFailed as e:
                return self.give_up("start-failed", f"claude を起動できない（{os.strerror(e.err)}）",
                                    command, errno=e.err)

    def close(self) -> None:
        self.release_count()
        remove(self.path(PID_FILE))
        try:
            fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
            os.close(self.lock_fd)
        except OSError:
            pass


def goal_pending(transcript_path: str, written: float) -> bool:
    """会話の記録に `/goal` の目標があり、印より後の判定の記録がまだ無ければ真。

    目標の設定と判定は `type: attachment` の行の `attachment.type: goal_status` に書かれる
    （Claude Code 2.1.280 で実測）。設定は `sentinel: true`、判定は `met` の真偽を持つ。
    `met: true` で目標は終わる。判定は command hook（`mark`）より後に書かれる。
    """
    if not transcript_path:
        return False
    has_goal = False
    judged_at = 0.0
    try:
        with open(transcript_path, errors="replace") as f:
            for line in f:
                if "goal_status" not in line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                a = row.get("attachment") if isinstance(row, dict) else None
                if row.get("type") != "attachment" or not isinstance(a, dict) \
                        or a.get("type") != "goal_status":
                    continue
                if a.get("sentinel"):
                    has_goal, judged_at = True, 0.0
                    continue
                judged_at = parse_iso(row.get("timestamp")) or judged_at
                if a.get("met") is True:
                    has_goal = False
    except OSError:
        return False
    return has_goal and judged_at <= written


def cmd_run(args: list[str]) -> int:
    if os.environ.get("NDF_RELAY") == "0":
        claude = resolve_claude()
        if claude is None:
            say("本物の claude が見つからない")
            return 127
        passthrough(claude, args)
    claude = resolve_claude()
    if depth() >= 2:
        say(f"起動が入れ子になっている（{claude or '本物の claude が見つからない'}）")
        return 127
    if claude is None:
        say("本物の claude が見つからない")
        return 127
    if os.environ.get("NDF_RELAY_DIR") or needs_no_relay(args):
        passthrough(claude, args)
    if not (os.isatty(0) and os.isatty(1)):
        passthrough(claude, args)
    try:
        import pty  # noqa: F401
        import termios
        import tty
    except ImportError:
        say("中継を始めない（擬似端末を作れない）。切れ目では示されたコマンドを手で入力する")
        passthrough(claude, args)
    got = read_plugin(claude)
    if got is None:
        say("中継を始めない（ndf のプラグインの名前と版を読めない）。切れ目では示されたコマンドを手で入力する")
        passthrough(claude, args)
    try:
        relay_dir = make_relay_dir()
    except OSError:
        say("中継を始めない（作業ディレクトリを作れない）。切れ目では示されたコマンドを手で入力する")
        passthrough(claude, args)
    relay = Relay(claude, relay_dir, got[0], got[1])
    saved = termios.tcgetattr(0)

    def restore() -> None:
        try:
            termios.tcsetattr(0, termios.TCSAFLUSH, saved)
        except (OSError, termios.error):
            pass

    def on_signal(signum, _frame):
        restore()
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGHUP, on_signal)
    signal.signal(signal.SIGWINCH, lambda *_: relay.copy_winsize())
    try:
        tty.setraw(0)
        return relay.loop(args)
    finally:
        restore()
        relay.close()


# ---------------------------------------------------------------- stop


def cmd_stop() -> int:
    root = state_root()
    found = 0
    try:
        names = sorted(os.listdir(root))
    except OSError:
        names = []
    for name in names:
        d = os.path.join(root, name)
        if not os.path.isdir(d) or not relay_running(d):
            continue
        with open(os.path.join(d, STOP_FILE), "w"):
            pass
        try:
            with open(os.path.join(d, PID_FILE)) as f:
                print(f.read().strip() or name)
        except OSError:
            print(name)
        found += 1
    return 0 if found else 1


# ---------------------------------------------------------------- install


DEF_RE = re.compile(r"^\s*(alias\s+claude=|function\s+claude\b|claude\s*\(\s*\))", re.M)


def _flock_wait(fd: int, seconds: float) -> bool:
    end = time.time() + seconds
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            if time.time() >= end:
                return False
            time.sleep(0.05)


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _records(path: str) -> list[str]:
    return (_read(path) or "").splitlines()


def _append_record(path: str, line: str) -> None:
    with open(path, "a") as f:
        f.write(line + "\n")


def place_copy() -> None:
    dst = os.path.join(data_dir(), "relay.py")
    src = os.path.realpath(__file__)
    with open(src, "rb") as f:
        body = f.read()
    try:
        with open(dst, "rb") as f:
            if f.read() == body:
                return
    except OSError:
        pass
    tmp = f"{dst}.{os.getpid()}.tmp"
    with open(tmp, "wb") as f:
        f.write(body)
    os.chmod(tmp, 0o755)
    os.replace(tmp, dst)


def rc_target() -> tuple[str, list[str]] | None:
    shell = os.path.basename(os.environ.get("SHELL", ""))
    home = os.path.expanduser("~")
    if shell == "bash":
        return os.path.join(home, ".bashrc"), [os.path.join(home, ".bash_aliases")]
    if shell == "zsh":
        return os.path.join(os.environ.get("ZDOTDIR") or home, ".zshrc"), []
    return None


def install_once() -> str | None:
    """I2〜I7。利用者へ知らせる 1 行を返す（知らせることが無ければ None）。"""
    place_copy()
    target = rc_target()
    if target is None:
        return None
    rc, extra = target
    body = _read(rc)
    if body is not None and BLOCK_OPEN in body.splitlines():
        return None
    root = state_root()
    added, skipped = os.path.join(root, "rc-added"), os.path.join(root, "rc-skipped")
    if rc in _records(added):
        return None
    if any(DEF_RE.search(_read(p) or "") for p in [rc] + extra):
        if rc in _records(skipped):
            return None
        _append_record(skipped, rc)
        return (f"ndf-relay: {rc} に claude の定義があるため alias を足さない。"
                f"中継を使うなら {ALIAS_LINE} を自分で置く")
    if body is not None:
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        with open(f"{rc}.ndf-bak-{stamp}", "w", encoding="utf-8") as f:
            f.write(body)
    with open(rc, "a", encoding="utf-8") as f:
        if body and not body.endswith("\n"):
            f.write("\n")
        f.write(("\n" if body else "") + RC_BLOCK)
    _append_record(added, rc)
    return (f"ndf-relay: {rc} へ alias claude を足した。次に開くシェルから効く"
            f"（今のシェルでは source {rc}）。戻すには囲みを消す")


def cmd_install() -> int:
    if os.environ.get("NDF_RELAY_AUTO") == "0":
        return 0
    try:
        root = state_root()
        os.makedirs(root, mode=0o700, exist_ok=True)
        os.makedirs(data_dir(), mode=0o700, exist_ok=True)
        fd = os.open(os.path.join(root, INSTALL_LOCK), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if not _flock_wait(fd, 2):
                return 0
            msg = install_once()
        finally:
            os.close(fd)
    except Exception:  # SessionStart を止めない
        return 0
    if msg:
        print(json.dumps({"systemMessage": msg}, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------- main


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: relay.py run|stop|mark|install", file=sys.stderr)
        return 2
    sub, rest = argv[0], argv[1:]
    if sub == "mark":
        try:
            return cmd_mark()
        except Exception:  # hook は Stop を止めない
            return 0
    if sub == "run":
        return cmd_run(rest)
    if sub == "stop":
        return cmd_stop()
    if sub == "install":
        return cmd_install()
    if sub == "is-child":
        # 文脈量の hook（token-guard.sh）が使う。中継が動いていて、hook を呼んだ claude が
        # 中継の直接の子なら 0（親のたどりは mark と同じ。間の bash / sh は claude でないので飛ぶ）
        d = os.environ.get("NDF_RELAY_DIR")
        return 0 if d and relay_running(d) and is_direct_child(d) else 1
    print(f"relay.py: 未知の副命令 {sub}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
