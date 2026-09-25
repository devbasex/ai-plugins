#!/usr/bin/env python3
"""NDF のラッパー: カットポイントで claude を起動し直す（#895）。

副命令:

| 副命令 | 役割 |
| --- | --- |
| `run [claude の引数 ...]` | 端末の前景に常駐し、claude を擬似端末の子として起動する。合図を受けたら子へ `/exit` を入力し、プラグインを更新して次の区間を起動する。ラッパーが要らない起動は本物の claude をそのまま exec する（素通し） |
| `stop` | 動いているラッパーすべてに停止の合図を置く |
| `mark` | Stop hook の本体。最後の応答の `ndf-next` のブロックを合図 `next.json` へ写す |
| `install` / `uninstall` / `status` | `/ndf:install-wrapper` の本体。複製とラッパーの rc を `${CLAUDE_CONFIG_DIR:-~/.claude}/ndf/` に置き、シェルの設定へ読み込みの 1 行を足す・外す・状態を示す（#928） |
| `startup` | SessionStart hook の本体。在る複製を今の版で置き直し（版は後退させない）、10.17.4〜10.17.6 が自動で足した囲みを 1 度だけ知らせる。シェルの設定は書かない |
| `question open` / `question close` | `AskUserQuestion` の `PreToolUse` / `PostToolUse` hook の本体。質問の表示中の合図を作る・消す（関門を越えない守り） |
| `is-child` | ラッパーの直接の子の claude から呼ばれていれば 0 |
| `notice` | カットポイントの告知。1 行目に `relay` か `outside`（`is-child` と同じ判定）、2 行目に告知の 1 文を出す（#980）。外のときの 2 行目は理由（ラッパーが無い・終わっている・直接の子でない）で変わる（#1016） |

**標準ライブラリだけで書く。** 合図と作業ディレクトリの形（`next.json` のキーと
`NDF_RELAY_DIR` のファイル）は版をまたいで変えない。hook は区間ごとに新しい版で動き、
動いているラッパーは古い版のままでありうるためである。

規約は skills/development-workflow/references/relay.md にある。
"""
from __future__ import annotations

import datetime as _dt
import errno
import fcntl
import hashlib
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
COPY_LOCK = "copy.lock"
QUESTION_FILE = "question"
QUESTION_LOCK = "question.lock"
ASKED_FILE = "asked"
# 背景の作業が残っていて Stop を止めた合図の候補（区間とハッシュ）。同じ候補では 2 度止めない
HELD_FILE = "mark-held.json"

BLOCK_OPEN = "# >>> ndf relay >>>"
BLOCK_CLOSE = "# <<< ndf relay <<<"

# 素通しにする引数と副命令（Claude Code 2.1.280 の `claude --help` から写す）
PASS_FLAGS = {"-p", "--print", "-h", "--help", "-v", "--version"}
SUBCOMMANDS = {
    "agents", "attach", "auth", "auto-mode", "doctor", "gateway", "import", "install",
    "logs", "mcp", "plugin", "plugins", "project", "respawn", "rm", "setup-token",
    "stop", "kill", "ultrareview", "update", "upgrade",
}
# 2 つ目以降の区間へ引き継ぐ引数の解析（#936・決定 22）。Claude Code 2.1.281 の `claude --help` から写す。
# 値を取らない選択肢。これ以外の `-` で始まる選択肢は、次の語が `-` で始まらなければ値として取る
BOOL_FLAGS = {
    "--allow-dangerously-skip-permissions", "--ax-screen-reader", "--bg", "--background",
    "--bare", "--brief", "--chrome", "--no-chrome", "-c", "--continue",
    "--dangerously-skip-permissions", "--disable-slash-commands",
    "--exclude-dynamic-system-prompt-sections", "--fork-session", "--forward-subagent-text",
    "--ide", "--include-hook-events", "--include-partial-messages",
    "--no-session-persistence", "--replay-user-messages", "--restricted", "--safe-mode",
    "--strict-mcp-config", "--tmux", "--verbose",
}
# 可変長（`<x...>`）の選択肢。後続の `-` で始まらない語をすべて取る
VARIADIC_FLAGS = {
    "--add-dir", "--allowedTools", "--allowed-tools", "--disallowedTools",
    "--disallowed-tools", "--mcp-config", "--betas", "--tools", "--file",
}
# 必須の値（`<x>`）を取る選択肢。次の語が `-` で始まっても値として取る（commander と同じ）
REQUIRED_FLAGS = {
    "--agent", "--agents", "--append-system-prompt", "--autocompact", "--debug-file", "--effort",
    "--environment", "--fallback-model", "--input-format", "--json-schema", "--max-budget-usd",
    "--model", "-n", "--name", "--output-format", "--permission-mode", "--permission-prompts",
    "--plugin-dir", "--plugin-url", "--remote-control-session-name-prefix", "--session-id",
    "--setting-sources", "--settings", "--system-prompt", "--system-prompt-snapshot",
}
# 会話ごと・区間ごとに変わるもの。次の区間は新しい会話を始めるので引き継がない
SECTION_FLAGS = {
    "-c", "--continue", "-r", "--resume", "--session-id", "--fork-session", "--from-pr",
    "--teleport", "--cloud", "-n", "--name", "--bg", "--background", "--tmux",
    "-w", "--worktree",
}
# 子へ継がせない Claude Code の環境変数（中から起こしたプロセスが継ぐもの）
DROP_ENV = ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT")


def _num(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def quiet_seconds() -> float:
    """静止の秒数（`NDF_RELAY_QUIET`）。切り替えの実際の待ちと、カットポイントの告知の両方が読む。"""
    return _num("NDF_RELAY_QUIET", 5)


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
    """`relay.lock` の排他が取れなければラッパーが動いている。pid の生死では見ない。"""
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
    _write_file(path, json.dumps(data, ensure_ascii=False).encode(), 0o600)


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
    """hook の親をたどって最初に当たる claude が、ラッパーの起動した子（`child.pid`）かを見る。

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


def relay_position() -> str:
    """ラッパーとの位置を返す。`relay`（直接の子）か、外である理由（`no-dir` / `not-running` / `not-child`）。

    `not-child` はラッパーが動いているのに hook を呼んだ claude が `child.pid` でないとき
    （fork したセッション・bg-pty-host の下・別の入口。#1016）。
    """
    d = os.environ.get("NDF_RELAY_DIR")
    if not d or not os.path.isdir(d):
        return "no-dir"
    if not relay_running(d):
        return "not-running"
    if not is_direct_child(d):
        return "not-child"
    return "relay"


def under_relay() -> str | None:
    """`NDF_RELAY_DIR` があり、ラッパーが動いていて、hook を呼んだ claude がラッパーの直接の子ならその場所を返す。"""
    if relay_position() == "relay":
        return os.environ.get("NDF_RELAY_DIR")
    return None


def relay_child_pid() -> int | None:
    """ラッパーが起動した子の pid（`child.pid`）。読めなければ None。"""
    d = os.environ.get("NDF_RELAY_DIR")
    if not d:
        return None
    try:
        with open(os.path.join(d, CHILD_FILE)) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


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


def running_tasks(tasks) -> list[dict]:
    """Stop hook の入力の `background_tasks` のうち動いているもの。

    Claude Code（2.1.282 で実測）の要素は `id`・`type`・`status`・`description`・`command` を持つ。"""
    if not isinstance(tasks, list):
        return []
    return [{"id": str(t.get("id") or ""), "type": str(t.get("type") or ""),
             "command": str(t.get("command") or t.get("description") or "")[:80]}
            for t in tasks if isinstance(t, dict) and t.get("status") == "running"]


def background_running(tasks) -> bool:
    return bool(running_tasks(tasks))


def current_section(d: str) -> int | None:
    """ラッパーの log.jsonl の最後の start の区間の番号。"""
    try:
        with open(os.path.join(d, LOG_FILE)) as f:
            lines = f.readlines()
    except OSError:
        return None
    for raw in reversed(lines):
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("event") == "start" and isinstance(row.get("section"), int):
            return row["section"]
    return None


def log_mark_skipped(d: str, section: int | None, reason: str, tasks: list[dict], held: bool) -> None:
    row = {"event": "mark_skipped", "at": now_iso(), "section": section, "reason": reason,
           "tasks": tasks, "held": held}
    with open(os.path.join(d, LOG_FILE), "a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def hold_reason(tasks: list[dict]) -> str:
    """背景の作業が残っていて合図を書けないときに、Stop を止めて conductor へ渡す文。"""
    rows = [f"- {t['id'] or '(id 不明)'}: {t['command'] or '(コマンド不明)'}" for t in tasks]
    return "\n".join([
        f"ndf-relay: 背景の作業が {len(tasks)} 件動いているので、ndf-next の合図を書かなかった（ラッパーは切り替わらない）。",
        *rows,
        "背景の作業を止めるのは TaskStop <id>。pkill -f / pgrep -f で止めたり確かめたりしない"
        "（Claude Code が包んだコマンド行に一致しない）。止まったかは完了通知（failed / killed）で確かめる。",
        "止めてから ndf-next を出し直す。supervisor や supervise.py queue のように止めてはいけない作業なら、"
        "止めずに終わりを待ってから ndf-next を出し直す。",
    ])


def hold_once(d: str, section: int | None, block: str, active: bool) -> bool:
    """同じ区間・同じ候補で 1 度だけ真を返す（Stop を止める）。stop_hook_active が真なら止めない。"""
    if active:
        return False
    key = {"section": section, "hash": hashlib.sha256(block.encode()).hexdigest()}
    held = os.path.join(d, HELD_FILE)
    if read_json(held) == key:
        return False
    write_json_atomic(held, key)
    return True


def asked_after(d: str, mark_path: str) -> bool:
    """合図を書いた後に質問が出ていれば真。"""
    m = read_json(mark_path)
    asked = read_json(os.path.join(d, ASKED_FILE))
    if not isinstance(m, dict) or not isinstance(asked, dict):
        return False
    w, a = parse_iso(m.get("written_at")), parse_iso(asked.get("at"))
    return w is not None and a is not None and a >= w


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
    # Stop が起きたなら質問は表示されていない（Esc で取り消した合図もここで消える）
    remove(os.path.join(d, QUESTION_FILE))
    path = os.path.join(d, MARK_FILE)
    blocks = next_blocks(str(data.get("last_assistant_message") or ""))
    tasks = running_tasks(data.get("background_tasks"))
    if tasks or len(blocks) > 1:
        remove(path)
        if blocks:
            section = current_section(d)
            reason = "blocks" if len(blocks) > 1 else "background"
            held = (reason == "background"
                    and hold_once(d, section, blocks[0], bool(data.get("stop_hook_active"))))
            log_mark_skipped(d, section, reason, tasks, held)
            if held:
                print(json.dumps({"decision": "block", "reason": hold_reason(tasks)}, ensure_ascii=False))
        return 0
    if not blocks:
        # 合図の後に応答が続いた（目標が未達など）。質問が出ていなければ前の合図を残す
        if asked_after(d, path):
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
    mine = {os.path.realpath(__file__), os.path.realpath(os.path.join(data_dir(), "relay.py")),
            os.path.realpath(os.path.join(os.environ.get("CLAUDE_CONFIG_DIR")
                                          or os.path.join(os.path.expanduser("~"), ".claude"),
                                          "ndf", "relay.py"))}
    if real in mine:
        return True
    try:
        with open(real, "rb") as f:
            return b"relay.py" in f.read(4096)
    except OSError:
        # 読めないものはラッパーと見なさない。飛ばし損ねた繰り返しは NDF_RELAY_DEPTH が止める
        return False


def resolve_claude() -> str | None:
    """alias は子に効かないため実体を探す。`claude` という名前でラッパーを呼ぶ別のスクリプトを飛ばす。"""
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


def carried_args(args: list[str]) -> list[str]:
    """最初の区間の引数から、2 つ目以降の区間の先頭に付ける起動の方針を選ぶ（#936）。

    commander と同じ規則で選択肢と値を区切り、位置引数・`--` 以後・`SECTION_FLAGS` を
    値ごと落とす。未知の選択肢は値を取る規則のまま引き継ぐ。"""
    out, i = [], 0
    while i < len(args):
        a = args[i]
        i += 1
        if a == "--":
            break
        if not a.startswith("-") or a == "-":
            continue  # 位置引数（最初のプロンプト）
        long = a.startswith("--")
        name = a.split("=", 1)[0] if long else a[:2]  # `-nfoo` は `-n` に値が付いた 1 語
        group = [a]
        one_word = "=" in a if long else len(a) > 2
        if not one_word and name in REQUIRED_FLAGS:
            group += args[i:i + 1]
            i += 1
        elif not one_word and name not in BOOL_FLAGS:
            take_all = name in VARIADIC_FLAGS
            while i < len(args) and not args[i].startswith("-"):
                group.append(args[i])
                i += 1
                if not take_all:
                    break
        if name not in SECTION_FLAGS:
            out += group
    return out


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
    """合図の作業ディレクトリが消えていたら、主ディレクトリか在る最も近い親を返す。"""
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


class StartLimit:
    """次の区間を起動してよいかを、1 日の起動数と空回りで決める。`count.lock` を持つ。"""

    def __init__(self, log_path: str):
        self.log_path = log_path
        self.lock: int | None = None
        self.max_starts = int(_num("NDF_RELAY_MAX_STARTS", 20))
        self.spin = _num("NDF_RELAY_SPIN", 120)

    def take(self) -> bool:
        if self.lock is not None:
            return True
        fd = _lock(os.path.join(state_root(), COUNT_LOCK), 0)
        if fd is None:
            return False
        self.lock = fd
        return True

    def release(self) -> None:
        if self.lock is not None:
            fcntl.flock(self.lock, fcntl.LOCK_UN)
            os.close(self.lock)
            self.lock = None

    def refusal(self, written: float, started_at: float) -> tuple[str, str] | None:
        if self.count_today() >= self.max_starts:
            return "max-starts", f"1 日の起動回数が上限 {self.max_starts} に達した"
        if self.spinning(written, started_at):
            return "spin", f"区間が 3 つ続けて {int(self.spin)} 秒未満でカットポイントに達した"
        return None

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

    def spinning(self, written: float, started_at: float) -> bool:
        ends = []
        try:
            with open(self.log_path) as f:
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
        return written - started_at < self.spin


class Terminal:
    """端末と子の擬似端末のあいだで入出力を中継し、子の終わりを拾う。"""

    def __init__(self):
        self.pid = 0
        self.fd = -1
        self.last_input = 0.0
        self.last_tick = 0.0
        self.stdin_open = True
        self.poll = _num("NDF_RELAY_POLL", 2)

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


class Relay:
    """前景に常駐し、区間ごとの claude を擬似端末の子として起動する。"""

    def __init__(self, claude: str, relay_dir: str, marketplace: str, version: str,
                 term: Terminal, limit: StartLimit):
        self.claude = claude
        self.dir = relay_dir
        self.marketplace = marketplace
        self.version = version
        self.env = child_env(relay_dir)
        self.term = term
        self.limit = limit
        self.section = 0
        self.started_at = 0.0
        self.session_id = ""
        self.halted = False
        self.exited = None
        self.saw_question = False
        self.quiet = quiet_seconds()
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

    def _child_exec(self, sync_r: int, sync_w: int, res_r: int, res_w: int,
                    args: list[str], cwd: str) -> None:
        """子側: 親だけが使う fd を閉じ、同期を待って chdir と execve を行う。
        失敗したら errno を結果 pipe へ書き、127 で終わる。"""
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

    def spawn(self, args: list[str], cwd: str) -> float:
        import pty
        sync_r, sync_w = os.pipe()
        res_r, res_w = os.pipe()
        pid, fd = pty.fork()
        if pid == 0:
            self._child_exec(sync_r, sync_w, res_r, res_w, args, cwd)
        os.close(sync_r)
        os.close(res_w)
        self.term.copy_winsize(fd)
        with open(self.path(CHILD_FILE), "w") as f:
            f.write(str(pid))
        at = time.time()
        os.close(sync_w)
        data = self._read_errno(res_r)
        if data:
            os.waitpid(pid, 0)
            os.close(fd)
            raise StartFailed(int(data or errno.EIO))
        self.term.pid, self.term.fd = pid, fd
        return at

    def start_section(self, args: list[str], cwd: str, command: str, from_session: str,
                      cwd_fallback: str | None = None, carried: list[str] | None = None) -> None:
        remove(self.path(MARK_FILE))
        remove(self.path(QUESTION_FILE))
        at = self.spawn([*(carried or []), *args], cwd)
        self.section += 1
        self.started_at = at
        row = dict(event="start", at=now_iso(at), section=self.section, pid=self.term.pid,
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
            self.halt("error", f"ラッパーの中で例外が起きた（{type(e).__name__}）")
            return None

    def _tick(self):
        m = self.read_mark()
        if m is None:
            return None
        written = parse_iso(m.get("written_at")) or time.time()
        latest = max(written, self.term.last_input)
        tp = m.get("transcript_path") or ""
        unmet, cancel = after_mark(tp, written)
        # 目標が未達で応答が続くあいだは、会話の記録の更新を静止に数えない
        snap = None if unmet else file_snap(tp)
        if snap is not None:
            latest = max(latest, snap[1] / 1e9)
        if time.time() - latest < self.quiet:
            return None
        # (3) 質問の表示中と、合図の後に質問が出たときは書かない（G1）。(4) 合図の後に利用者の入力か背景の処理の起動があれば
        # 書かない。目標が未達の判定が無ければ、合図の後の応答の再開でも書かない（G2）
        if (os.path.exists(self.path(QUESTION_FILE)) or cancel
                or asked_after(self.dir, self.path(MARK_FILE))):
            return None
        if not unmet and replied_after(tp, written):
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
        remove(self.path(MARK_FILE))
        self.screen(f"ndf-relay: 次の区間を起動しない（{why}）。このまま続けるか、"
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
            return not after_mark(tp, parse_iso(m.get("written_at")) or 0)[1]
        return file_snap(tp) == m.get("_snap")

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
                end = esc_at + _num("NDF_RELAY_ESC_WAIT", 1)
                while time.time() < end:
                    res = self.term.pump(until=min(end, time.time() + 0.1))
                    if res:
                        self.exited = res
                        return True
                if self.term.last_input > esc_at or not self._still_due(m):
                    self.limit.release()
                    return False
            try:
                os.write(self.term.fd, b"/exit\r")
            except OSError:
                pass
            # TUI が書いた入力を読み終える前に質問が描かれないよう、1 秒ロックを持つ
            end = time.time() + _num("NDF_RELAY_EXIT_HOLD", 1)
            while time.time() < end:
                res = self.term.pump(until=min(end, time.time() + 0.1))
                self.saw_question |= os.path.exists(self.path(QUESTION_FILE))
                if res:
                    self.exited = res
                    break
            return True
        finally:
            os.close(fd)

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

    def stop_child(self) -> tuple[str, int]:
        """SIGTERM、SIGKILL の順に子を停止し、終了理由と wait 状態を返す。"""
        for sig, how, wait in ((signal.SIGTERM, "sigterm", _num("NDF_RELAY_TERM_WAIT", 10)),
                               (signal.SIGKILL, "sigkill", None)):
            try:
                os.kill(self.term.pid, sig)
            except ProcessLookupError:
                pass
            res = self.term.pump(until=None if wait is None else time.time() + wait)
            if res:
                return how, res[1]
        return "sigkill", 0

    def end_child(self) -> tuple[str, int, bool]:
        """`/exit` の後の待ち。(終わり方, 子の wait の状態, 待ちのあいだに質問が出たか) を返す。
        30 秒で SIGTERM、さらに 10 秒で SIGKILL。質問の合図がある間は秒を数えず、`count.lock` を放す。"""
        questioned, self.saw_question = self.saw_question, False
        if self.exited:
            res, self.exited = self.exited, None
            return "mark", res[1], questioned
        res, _, questioned = self.wait_for_normal_exit(_num("NDF_RELAY_EXIT_WAIT", 30), questioned)
        if res:
            return "mark", res[1], questioned
        ended_by, status = self.stop_child()
        return ended_by, status, questioned

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
        self.limit.release()
        self.log(event="stop", section=self.section, reason=reason, **extra)
        self.screen(f"ndf-relay: 次の区間を起動できない（{why}）。次のコマンド:\r\n{command}")
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
                return None, exit_code(status)
        self.log_end(written, ended_by)
        if requeued:
            why = self.recheck(again)
            if why:
                return None, self.give_up(why[0], why[1], again["command"])
            return again, None
        return m, None

    def prepare_next(self, m) -> tuple[str, str | None] | None:
        """プラグインを更新し、合図から次の区間の (cwd, 退避前の cwd) を決める。更新に失敗したら None。"""
        version = self.update()
        if version is None:
            return None
        self.version = version
        cwd = m.get("cwd") or os.getcwd()
        fb = None
        if not os.path.isdir(cwd):
            fb, cwd = cwd, fallback_cwd(cwd)
        return cwd, fb

    def loop(self, first_args: list[str]) -> int:
        carried = carried_args(first_args)
        try:
            self.start_section(first_args, os.getcwd(), shlex.join(first_args), "")
        except StartFailed as e:
            self.log(event="stop", section=self.section + 1, reason="start-failed", errno=e.err)
            say(f"claude を起動できない（{os.strerror(e.err)}）")
            return 127
        while True:
            res = self.term.pump(tick=self.tick)
            if res[0] == "exit":
                self.log_end(time.time(), "no-mark")
                return exit_code(res[1])
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
            self.screen(f"── ndf-relay: 区間 {self.section + 1} ──")
            try:
                self.start_section([command], cwd, command, m.get("session_id") or "", fb, carried)
            except StartFailed as e:
                return self.give_up("start-failed", f"claude を起動できない（{os.strerror(e.err)}）",
                                    command, errno=e.err)

    def close(self) -> None:
        self.limit.release()
        remove(self.path(PID_FILE))
        try:
            fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
            os.close(self.lock_fd)
        except OSError:
            pass


def file_snap(path: str) -> tuple[int, int] | None:
    """(大きさ, 更新時刻の ns)。無ければ None。"""
    try:
        st = os.stat(path)
    except (OSError, ValueError):
        return None
    return st.st_size, st.st_mtime_ns


def _iter_transcript_rows(transcript_path: str):
    """会話の記録から JSON object の行だけを返す。"""
    if not transcript_path:
        return
    try:
        with open(transcript_path, errors="replace") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    yield row
    except OSError:
        return


def replied_after(transcript_path: str, written: float) -> bool:
    """会話の記録に、合図より後の `assistant` か `user` の行があれば真（G2）。"""
    for row in _iter_transcript_rows(transcript_path):
        if row.get("type") not in ("assistant", "user"):
            continue
        t = parse_iso(row.get("timestamp"))
        if t is not None and t > written:
            return True
    return False


def _is_user_prompt(row: dict) -> bool:
    """利用者が入力した行か。hook の差し戻し（`isMeta`）と Tool の結果は除く。"""
    if row.get("type") != "user" or row.get("isMeta"):
        return False
    content = (row.get("message") or {}).get("content") if isinstance(row.get("message"), dict) else None
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        items = [c for c in content if isinstance(c, dict)]
        if not items or any(c.get("type") == "tool_result" for c in items):
            return False
        # Esc で応答を止めた記録（ラッパーが書いた Esc でも出る）は入力に数えない
        return not all(str(c.get("text") or "").startswith("[Request interrupted by user")
                       for c in items)
    return False


def _starts_background(row: dict) -> bool:
    """背景の処理を起動する Tool の呼び出し（`run_in_background` が真）を含む `assistant` の行か。"""
    if row.get("type") != "assistant" or not isinstance(row.get("message"), dict):
        return False
    content = row["message"].get("content")
    if not isinstance(content, list):
        return False
    return any(isinstance(c, dict) and c.get("type") == "tool_use"
               and isinstance(c.get("input"), dict) and c["input"].get("run_in_background")
               for c in content)


def after_mark(transcript_path: str, written: float) -> tuple[bool, bool]:
    """合図より後の会話の記録を見て (目標が未達と判定された, 切り替えを取りやめる) を返す。
    取りやめるのは、利用者の入力か背景の処理の起動の行があるとき。"""
    unmet = cancel = False
    for row in _iter_transcript_rows(transcript_path):
        t = parse_iso(row.get("timestamp"))
        if t is None or t <= written:
            continue
        a = row.get("attachment")
        if (row.get("type") == "attachment" and isinstance(a, dict)
                and a.get("type") == "goal_status" and a.get("met") is False
                and not a.get("sentinel")):
            unmet = True
        if _is_user_prompt(row) or _starts_background(row):
            cancel = True
    return unmet, cancel


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
        say("ラッパーを始めない（擬似端末を作れない）。カットポイントでは示されたコマンドを手で入力する")
        passthrough(claude, args)
    got = read_plugin(claude)
    if got is None:
        say("ラッパーを始めない（ndf のプラグインの名前と版を読めない）。カットポイントでは示されたコマンドを手で入力する")
        passthrough(claude, args)
    try:
        relay_dir = make_relay_dir()
    except OSError:
        say("ラッパーを始めない（作業ディレクトリを作れない）。カットポイントでは示されたコマンドを手で入力する")
        passthrough(claude, args)
    term = Terminal()
    relay = Relay(claude, relay_dir, got[0], got[1], term,
                  StartLimit(os.path.join(relay_dir, LOG_FILE)))
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
    signal.signal(signal.SIGWINCH, lambda *_: term.copy_winsize())
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


# ---------------------------------------------------------------- 導入（install / uninstall / status / startup）


DEF_RE = re.compile(r"^\s*(alias\s+claude=|function\s+claude\b|claude\s*\(\s*\))")
UNSAFE = set("'\"\\$`!\n")
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-(dev|rc)\.(\d+))?$")


def config_dir() -> str:
    """複製・複製の版・ラッパーの rc の親。devbase では永続化される `~/.claude` の下になる。"""
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(base, "ndf")


def copy_path() -> str:
    return os.path.join(config_dir(), "relay.py")


def copy_version_path() -> str:
    return os.path.join(config_dir(), "relay.version")


def shellrc_path() -> str:
    return os.path.join(config_dir(), "shellrc")


def old_copy_path() -> str:
    """10.17.4〜10.17.6 が置いた複製。10.17.4〜10.17.6 の囲みの alias が指す。"""
    return os.path.join(data_dir(), "relay.py")


def loader_file() -> str | None:
    """devbase が読み込む永続化の場所（devbasex/devbase#253）。無ければ None。"""
    d = os.environ.get("DEVBASE_SHELLRC_DIR")
    return os.path.join(d, "ndf-relay.sh") if d and os.path.isdir(d) else None


def rc_files() -> list[str]:
    """囲みを足しうるファイル（`~/.bashrc`・`~/.bash_profile`・`~/.zshrc`）。uninstall はこの全部から外す。"""
    home = os.path.expanduser("~")
    return [os.path.join(home, ".bashrc"), os.path.join(home, ".bash_profile"),
            os.path.join(os.environ.get("ZDOTDIR") or home, ".zshrc")]


def login_files() -> list[str]:
    """ログインシェルの bash が読む候補。在る最初の 1 つだけを読む（#966）。"""
    home = os.path.expanduser("~")
    return [os.path.join(home, n) for n in (".bash_profile", ".bash_login", ".profile")]


def login_file() -> str | None:
    """ログインシェルの bash が実際に読むファイル。どれも無ければ None。"""
    return next((p for p in login_files() if os.path.exists(p)), None)


def bash_look() -> list[str]:
    """bash で既存の `claude` の定義を探すファイル。ログインシェルの設定も含める（#936・#966）。"""
    home = os.path.expanduser("~")
    return [os.path.join(home, ".bashrc"), os.path.join(home, ".bash_aliases")] + login_files()


def shell_rc() -> tuple[str, str, list[str]] | None:
    """(シェルの名前, 足す先, 既存の定義を探すファイル)。bash と zsh 以外は None。

    macOS の bash は `~/.bash_profile` へ足す。macOS の端末は新しいウィンドウをログインシェルで
    開き、ログインシェルの bash は `~/.bashrc` を読まないためである（#966）。"""
    shell = os.path.basename(os.environ.get("SHELL", ""))
    bashrc, bash_profile, zshrc = rc_files()
    if shell == "bash":
        return shell, (bash_profile if sys.platform == "darwin" else bashrc), bash_look()
    if shell == "zsh":
        return shell, zshrc, [zshrc]
    return None


READS_BASHRC_RE = re.compile(r"(?:^|[\s;&|])(?:\.|source)\s+\S*\.bashrc\b")


def reads_bashrc(path: str) -> bool:
    """囲みの外に `~/.bashrc` を読む行（`. ~/.bashrc`・`source ~/.bashrc` など）があるか。"""
    body = _read(path)
    if body is None:
        return False
    lines = body.split("\n")
    inside = set()
    for a, b in blocks_of(lines)[0]:
        inside.update(range(a, b + 1))
    return any(READS_BASHRC_RE.search(line) for i, line in enumerate(lines)
               if i not in inside and not line.lstrip().startswith("#"))


def login_shadow() -> tuple[str, str] | None:
    """macOS の bash で `~/.bash_profile` が無く、`~/.bash_login` か `~/.profile` をログインシェルが
    読んでいるとき (作る先, 読まれている先)。作ると読まれている先が読まれなくなる。"""
    bash_profile = login_files()[0]
    login = login_file()
    if login is None or login == bash_profile:
        return None
    return bash_profile, login


def login_warning() -> str | None:
    """macOS の bash で、読み込みの行が `~/.bashrc` にしか無く、ログインシェルが読むファイルが
    `~/.bashrc` を読まないときの警告。"""
    sh = shell_rc()
    if sys.platform != "darwin" or not sh or sh[0] != "bash":
        return None
    bashrc, bash_profile = rc_files()[:2]
    if not rc_blocks(bashrc)[0]:
        return None
    login = login_file()
    if login and (rc_blocks(login)[0] or reads_bashrc(login)):
        return None
    return (f"警告: 読み込みの行は {bashrc} にしか無く、ログインシェルが読む {login or bash_profile} は "
            f"{bashrc} を読まない。macOS の端末はログインシェルで開くため、ラッパーが効かない。"
            f"/ndf:install-wrapper を打ち直すと {bash_profile} へ足す")


def sh_quote(path: str) -> str:
    """`$HOME` の下なら `"$HOME/..."`、それ以外は `"<絶対パス>"`。"""
    home = os.path.expanduser("~").rstrip("/")
    if home and path.startswith(home + "/"):
        return f'"$HOME{path[len(home):]}"'
    return f'"{path}"'


def shellrc_body() -> str:
    c = sh_quote(copy_path())
    return ("# ndf のラッパー。/ndf:install-wrapper が書き、/ndf:install-wrapper uninstall が消す\n"
            "function claude {\n"
            f"  if [ -f {c} ]; then python3 {c} run \"$@\"\n"
            "  else command claude \"$@\"; fi\n"
            "}\n")


def loader_line() -> str:
    s = sh_quote(shellrc_path())
    return f"[ -f {s} ] && . {s}"


def loader_inner() -> list[str]:
    return ["# ndf のラッパー（/ndf:install-wrapper uninstall で外れる）", loader_line()]


def loader_body() -> str:
    return "\n".join(loader_inner()) + "\n"


def plugin_version(root: str | None = None) -> str | None:
    """`<プラグインのルート>/.claude-plugin/plugin.json` の版。ルートは relay.py の 2 つ上。"""
    root = root or os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    data = read_json(os.path.join(root, ".claude-plugin", "plugin.json"))
    v = data.get("version") if isinstance(data, dict) else None
    return v.strip() if isinstance(v, str) and v.strip() else None


def version_key(v: str | None):
    """`X.Y.Z` < 同じ `X.Y.Z` では `-dev.N` < `-rc.N` < 接尾辞なし。読めなければ None。"""
    m = VERSION_RE.match((v or "").strip())
    if not m:
        return None
    rank = {"dev": 0, "rc": 1, None: 2}[m.group(4)]
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), rank, int(m.group(5) or 0))


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


def _lock(path: str, seconds: float) -> int | None:
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    if _flock_wait(fd, seconds):
        return fd
    os.close(fd)
    return None


def _unlock(fd: int | None) -> None:
    if fd is not None:
        try:
            os.close(fd)
        except OSError:
            pass


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _read_bytes(path: str) -> bytes | None:
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def _records(path: str) -> list[str]:
    return (_read(path) or "").splitlines()


def _add_record(path: str, line: str) -> None:
    if line not in _records(path):
        with open(path, "a") as f:
            f.write(line + "\n")


def _drop_records(path: str, lines: list[str]) -> None:
    rows = _records(path)
    keep = [r for r in rows if r not in lines]
    if keep != rows:
        _write_file(path, "".join(r + "\n" for r in keep).encode(), 0o600)


def _write_file(path: str, body: bytes, mode: int) -> None:
    """一時ファイルに書いてから置き換える。symlink は指す先を置き換える。"""
    real = os.path.realpath(path)
    tmp = f"{real}.{os.getpid()}.tmp"
    with open(tmp, "wb") as f:
        f.write(body)
    os.chmod(tmp, mode)
    os.replace(tmp, real)


def _self_body() -> bytes:
    with open(os.path.realpath(__file__), "rb") as f:
        return f.read()


def _backup(path: str, body: str) -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    bak = f"{path}.ndf-bak-{stamp}"
    with open(bak, "w", encoding="utf-8") as f:
        f.write(body)
    return bak


def blocks_of(lines: list[str]) -> tuple[list[tuple[int, int]], bool]:
    """囲みの (開きの行, 閉じの行) と、閉じの無い囲みがあるかを返す。`lines` は改行を落とした行。"""
    found, start = [], None
    for i, line in enumerate(lines):
        s = line.rstrip("\r")
        if start is None and s == BLOCK_OPEN:
            start = i
        elif start is not None and s == BLOCK_CLOSE:
            found.append((start, i))
            start = None
    return found, start is not None


def rc_blocks(path: str) -> tuple[list[tuple[int, int]], bool, str | None]:
    body = _read(path)
    if body is None:
        return [], False, None
    found, unclosed = blocks_of(body.split("\n"))
    return found, unclosed, body


def has_definition(paths: list[str]) -> str | None:
    """囲みの外に行頭の `claude` の定義を持つファイル。"""
    for p in paths:
        body = _read(p)
        if body is None:
            continue
        lines = body.split("\n")
        inside = set()
        for a, b in blocks_of(lines)[0]:
            inside.update(range(a, b + 1))
        if any(DEF_RE.match(line) for i, line in enumerate(lines) if i not in inside):
            return p
    return None


def rewrite_blocks(path: str, body: str, inner: list[str] | None) -> str:
    """囲みの中を `inner` へ置き換える（None なら囲みを行ごと外す）。囲みの外は変えない。
    バックアップのパスを返す。"""
    lines = body.split("\n")
    out, i = [], 0
    for a, b in blocks_of(lines)[0]:
        out += lines[i:a]
        if inner is not None:
            out += [lines[a]] + inner + [lines[b]]
        i = b + 1
    out += lines[i:]
    bak = _backup(path, body)
    _write_file(path, "\n".join(out).encode("utf-8"), os.stat(path).st_mode & 0o7777)
    return bak


def block_inner(body: str, a: int, b: int) -> list[str]:
    return [x.rstrip("\r") for x in body.split("\n")[a + 1:b]]


def has_direct_alias(text: str, found: list[tuple[int, int]]) -> bool:
    return any(any(x.startswith("alias claude=") for x in block_inner(text, a, b))
               for a, b in found)


def place_copy_to(dst: str, body: bytes, mode: int = 0o755) -> bool:
    if _read_bytes(dst) == body:
        return False
    _write_file(dst, body, mode)
    return True


def out(line: str) -> None:
    print(f"ndf-relay: {line}")


class LockBusy(Exception):
    pass


def _take_both(copy_needed: bool) -> tuple[int, int | None]:
    """`install.lock` → `copy.lock` の順に 2 秒まで待つ。どちらかが取れなければ LockBusy。"""
    root = state_root()
    os.makedirs(root, mode=0o700, exist_ok=True)
    fd = _lock(os.path.join(root, INSTALL_LOCK), 2)
    if fd is None:
        raise LockBusy()
    if not copy_needed:
        return fd, None
    cfd = _lock(os.path.join(config_dir(), COPY_LOCK), 2)
    if cfd is None:
        _unlock(fd)
        raise LockBusy()
    return fd, cfd


def cmd_install() -> int:
    """利用者が明示に打つ導入（E0〜E6）。版は比べずに今の版を置く。"""
    loader = loader_file()
    for p in [copy_path(), shellrc_path()] + ([loader] if loader else []):
        if UNSAFE & set(p):
            out(f"{p} は引用できない文字を含むため置かない")
            return 1
    sh = shell_rc()
    if loader is None and sh is None:
        shell = os.path.basename(os.environ.get("SHELL", "")) or "このシェル"
        out(f"{shell} には足さない。使うなら次の 1 行を設定へ置く: {loader_line()}")
        return 1
    look = sh[2] if sh else list(dict.fromkeys(rc_files() + bash_look()))
    found = has_definition(look)
    if found:
        out(f"{found} に claude の定義があるため足さない。使うなら次の 1 行を自分で置く: {loader_line()}")
        return 1
    for rc in rc_files():
        if rc_blocks(rc)[1]:
            out(f"{rc} の囲みに閉じが無い。直してから打ち直す")
            return 1
    shadow = login_shadow() if loader is None and sh and sh[0] == "bash" and sys.platform == "darwin" \
        else None
    if shadow:
        out(f"{shadow[0]} が無く、ログインシェルは {shadow[1]} を読んでいる。{shadow[0]} を作ると "
            f"{shadow[1]} が読まれなくなるため足さない。使うなら {shadow[0]} を作り、{shadow[1]} を読む行と"
            f"次の 1 行を置く: {loader_line()}")
        return 1
    try:
        os.makedirs(config_dir(), mode=0o700, exist_ok=True)
        fd, cfd = _take_both(True)
    except LockBusy:
        out("ほかの導入が動いている。少し待ってから打ち直す")
        return 3
    except OSError as e:
        out(f"書けない（{e}）")
        return 3
    try:
        return _install_locked(loader, sh)
    except OSError as e:
        out(f"書けない（{e}）")
        return 3
    finally:
        _unlock(cfd)
        _unlock(fd)


def _install_locked(loader: str | None, sh) -> int:
    body = _self_body()
    place_copy_to(copy_path(), body)
    ver = plugin_version()
    if ver:
        with open(copy_version_path(), "w") as f:
            f.write(ver + "\n")
    place_copy_to(shellrc_path(), shellrc_body().encode(), 0o644)
    root = state_root()
    added, user = os.path.join(root, "rc-added"), os.path.join(root, "rc-user")
    backups, touched = [], []
    # 残った囲み（10.17.4〜10.17.6 は alias を直に持つ）の中を今の読み込みの行へ置き換える
    for rc in rc_files():
        found, _, text = rc_blocks(rc)
        if found and any(block_inner(text, a, b) != loader_inner() for a, b in found):
            backups.append(rewrite_blocks(rc, text, loader_inner()))
            touched.append(rc)
    if loader:
        target = loader
        place_copy_to(loader, loader_body().encode(), 0o644)
    else:
        target = sh[1]
        found, _, text = rc_blocks(target)
        if not found:
            if text is not None:
                backups.append(_backup(target, text))
            with open(target, "a", encoding="utf-8") as f:
                if text and not text.endswith("\n"):
                    f.write("\n")
                f.write(("\n" if text else "") + BLOCK_OPEN + "\n" + loader_body() + BLOCK_CLOSE + "\n")
        touched.append(target)
    for rc in dict.fromkeys(touched):
        _add_record(added, rc)
        _add_record(user, rc)
    bak = f"（バックアップ {'・'.join(backups)}）" if backups else ""
    out(f"{target} から {shellrc_path()} を読むようにした{bak}。次に開くシェルから効く")
    out(f"ラッパーの本体 {copy_path()}（版 {ver or '不明'}）")
    return 0


def cmd_uninstall() -> int:
    """U1〜U6。10.17.4〜10.17.6 の自動の囲みも同じ手順で外す。"""
    for rc in rc_files():
        if rc_blocks(rc)[1]:
            out(f"{rc} の囲みに閉じが無い。何も変えていない。直してから打ち直す")
            return 1
    try:
        fd, cfd = _take_both(os.path.isdir(config_dir()))
    except LockBusy:
        out("ほかの導入が動いている。少し待ってから打ち直す")
        return 3
    except OSError as e:
        out(f"書けない（{e}）")
        return 3
    try:
        return _uninstall_locked()
    except OSError as e:
        out(f"書けない（{e}）")
        return 3
    finally:
        _unlock(cfd)
        _unlock(fd)


def _uninstall_locked() -> int:
    lines, removed_rc, direct_alias = [], [], False
    for rc in rc_files():
        found, _, text = rc_blocks(rc)
        if not found:
            continue
        direct_alias = direct_alias or has_direct_alias(text, found)
        bak = rewrite_blocks(rc, text, None)
        removed_rc.append(rc)
        lines.append(f"{rc} の囲みを外した（バックアップ {bak}）")
    loader = loader_file()
    for p in ([loader] if loader else []) + [shellrc_path(), copy_version_path(), copy_path(),
                                               old_copy_path()]:
        if os.path.lexists(p):
            os.unlink(p)
            lines.append(f"{p} を消した")
    root = state_root()
    for name in ("rc-skipped", "rc-noticed"):
        _drop_records(os.path.join(root, name), removed_rc)
    for rc in removed_rc:
        _add_record(os.path.join(root, "rc-added"), rc)
        _add_record(os.path.join(root, "rc-user"), rc)
    if not lines:
        out("外すものが無い")
        return 0
    for line in lines:
        out(line)
    out("開いているシェルでは " + ("unalias claude" if direct_alias else "unset -f claude") + " で外れる")
    if os.path.islink(os.path.dirname(config_dir())) or loader:
        out("同じ設定を共有する環境でも、ラッパーを通らなくなる（開いたままのシェルは素の claude へ落ちる）")
    return 0


def _auto_blocks(root: str) -> list[str]:
    """`rc-added` に載り `rc-user` に載らず、今も囲みがあるパス（10.17.4〜10.17.6 の自動の囲み）。"""
    user = _records(os.path.join(root, "rc-user"))
    return [p for p in dict.fromkeys(_records(os.path.join(root, "rc-added")))
            if p not in user and rc_blocks(p)[0]]


def _same(path: str, body: bytes) -> str:
    b = _read_bytes(path)
    return "無し" if b is None else ("今の版と同じ" if b == body else "今の版と違う")


def cmd_status() -> int:
    body = _self_body()
    loader = loader_file()
    root = state_root()
    auto = _auto_blocks(root)
    if loader:
        out(f"読み込み先: {loader}（{'在る' if os.path.exists(loader) else '無い'}）")
    else:
        sh = shell_rc()
        out(f"読み込み先: {sh[1] if sh else '無し（bash と zsh 以外のシェル）'} の囲み")
    for rc in rc_files():
        found, unclosed, text = rc_blocks(rc)
        if unclosed:
            out(f"{rc}: 閉じの無い囲みがある")
        elif not found:
            # `~/.bash_profile` へ足すのは macOS だけ。ほかでは無いときに行を出さない
            if rc != rc_files()[1] or sys.platform == "darwin":
                out(f"{rc}: 囲みは無い")
        else:
            direct = has_direct_alias(text, found)
            kind = "直の alias（10.17.4〜10.17.6 の形）" if direct else "読み込みの行"
            who = "。10.17.4〜10.17.6 が自動で足した" if rc in auto else ""
            out(f"{rc}: 囲みがある（{kind}{who}）")
    out(f"ラッパーの rc {shellrc_path()}: {'在る' if os.path.exists(shellrc_path()) else '無い'}")
    ver = (_read(copy_version_path()) or "").strip() or "不明"
    out(f"複製 {copy_path()}: {_same(copy_path(), body)}（複製の版 {ver}）")
    out(f"旧い複製 {old_copy_path()}: {_same(old_copy_path(), body)}")
    warn = None if loader else login_warning()
    if warn:
        out(warn)
    return 0


def _startup_copy(body: bytes) -> None:
    """判定 0a。`copy.lock` の中で複製の有無と版を読み直し、後退させずに置き直す。"""
    dst = copy_path()
    if not os.path.exists(dst):
        return
    cfd = _lock(os.path.join(config_dir(), COPY_LOCK), 1)
    if cfd is None:
        return
    try:
        cur = _read_bytes(dst)
        if cur is None or cur == body:
            return
        mine = version_key(plugin_version())
        if mine is None:
            return
        theirs = version_key(_read(copy_version_path()))
        if theirs is not None and theirs > mine:
            return
        _write_file(dst, body, 0o755)
        _write_file(copy_version_path(), (plugin_version() + "\n").encode(), 0o644)
    finally:
        _unlock(cfd)


def _startup_refresh_old_copy(body: bytes) -> None:
    """10.17.4〜10.17.6 が置いた複製が在れば今の版で置き直す。失敗は従来どおり局所的に無視する。"""
    old = old_copy_path()
    try:
        if os.path.exists(old) and _read_bytes(old) != body:
            _write_file(old, body, 0o755)
    except OSError:
        pass


def _startup_record_noticed(root: str) -> list[str]:
    """まだ知らせていない自動の囲みのパスを集め、`rc-noticed` に記録して返す。"""
    noticed = _records(os.path.join(root, "rc-noticed"))
    paths = [p for p in _auto_blocks(root) if p not in noticed]
    for p in paths:
        _add_record(os.path.join(root, "rc-noticed"), p)
    return paths


def _startup_notice_message(paths: list[str]) -> str:
    """自動で足した alias を知らせる通知文を、パスの列から組み立てる。"""
    msg = (f"ndf-relay: {'・'.join(paths)} の alias claude は 10.17.4〜10.17.6 が自動で足したもの。"
           "使い続けるなら何もしなくてよい。外すなら /ndf:install-wrapper uninstall。"
           "この alias は別のファイルが定義した alias claude（devbase の "
           "--dangerously-skip-permissions など）を上書きしている。/ndf:install-wrapper で"
           "入れ直せば囲みの中が読み込みの 1 行に替わり、その alias が戻る")
    if os.environ.get("DEVBASE_SHELLRC_DIR"):
        msg += "。コンテナを作り直した後も使うなら /ndf:install-wrapper"
    return msg


def startup_once() -> str | None:
    root = state_root()
    if not (os.path.exists(os.path.join(root, "rc-added")) or os.path.exists(copy_path())
            or os.path.exists(old_copy_path())):
        return None
    os.makedirs(root, mode=0o700, exist_ok=True)
    fd = _lock(os.path.join(root, INSTALL_LOCK), 1)
    if fd is None:
        return None
    try:
        body = _self_body()
        try:
            _startup_copy(body)
        except OSError:
            pass
        _startup_refresh_old_copy(body)
        paths = _startup_record_noticed(root)
        if not paths:
            return None
        return _startup_notice_message(paths)
    finally:
        _unlock(fd)


def cmd_startup() -> int:
    """SessionStart hook の本体。シェルの設定・ラッパーの rc・読み込み先のファイルは書かない。"""
    try:
        msg = startup_once()
    except Exception:  # SessionStart を止めない
        return 0
    if msg:
        print(json.dumps({"systemMessage": msg}, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------- 質問の合図（関門を越えない守り）


DENY_REASON = ("ndf-relay: ラッパーが入力を書いている最中だったため、質問を出さなかった。"
               "利用者へ返さずに、同じ AskUserQuestion を今すぐもう一度呼ぶ")


def cmd_question(action: str) -> int:
    """`open`: 質問の合図を作る（ロックが取れなければ質問を拒否する）。`close`: 消す。"""
    started = time.time()
    try:
        if not sys.stdin.isatty():
            sys.stdin.read()  # hook の JSON は読み捨てる
    except (OSError, ValueError):
        pass
    try:
        d = under_relay()
        if d is None:
            return 0
    except Exception:
        return 0
    if action == "close":
        try:
            remove(os.path.join(d, QUESTION_FILE))
        except Exception:
            pass
        return 0
    if action != "open":
        return 0
    try:
        fd = _lock(os.path.join(d, QUESTION_LOCK),
                   max(0.0, started + _num("NDF_RELAY_QUESTION_WAIT", 3) - time.time()))
        if fd is None:
            raise LockBusy()
        try:
            os.close(os.open(os.path.join(d, QUESTION_FILE), os.O_WRONLY | os.O_CREAT, 0o600))
            # 質問が出た時刻を残す。これより前の合図では切り替えない
            write_json_atomic(os.path.join(d, ASKED_FILE), {"at": now_iso()})
        finally:
            os.close(fd)
    except Exception:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": DENY_REASON}}, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------- カットポイントの告知（#980）


def notice_lines() -> tuple[str, str]:
    """カットポイントの告知。文面と秒数の唯一の定義。

    1 行目は `is-child` と同じ判定（ラッパーの直接の子か）。自動で切り替わるかは 2 行目が表す。
    外のときは 2 行目を理由（`relay_position()`）で変え、原因と対処を書く（#1016）。
    秒数はラッパー本体の静止（`NDF_RELAY_QUIET`）そのもので、切り替えの時間は足さない。
    """
    outside = ("/exit してから claude を起動し、下の中身を最初の入力として貼り付ける"
               "（/ndf:install-wrapper でラッパーを入れると自動になる）")
    try:
        pos = relay_position()
        if pos == "not-running":
            return "outside", ("ラッパーは既に終わっている。"
                               "/exit してから claude を起動し、下の中身を最初の入力として貼り付ける")
        if pos == "not-child":
            child = relay_child_pid()
            who = f"元の会話（子 pid {child}）" if child is not None else "元の会話"
            return "outside", (f"ラッパーは{who}しか見ていないため、この会話で出した ndf-next は自動では拾われない。"
                               "元の会話へ戻って同じ ndf-next を出すか、元の会話を /exit してから"
                               " claude を起動し、下の中身を最初の入力として貼り付ける")
        if pos != "relay":
            return "outside", outside
        quiet = quiet_seconds()
    except Exception:
        return "outside", outside
    if quiet == float("inf"):
        return "relay", ("NDF_RELAY_QUIET が有限でないため、ラッパーは自動で切り替えない。"
                         "/exit してから claude を起動し、下の中身を最初の入力として貼り付ける")
    # ラッパー本体は負・nan・-inf の静止を待たずに通すので、0 として数える
    q = quiet if quiet == quiet and quiet > 0 else 0.0
    n = round(q)
    when = f"約 {n} 秒後に" if n > 0 else "まもなく"
    return "relay", (f"{when}自動で新しい会話へ切り替わる。キー入力やスクロールをせずに、そのまま待つ"
                     "（切り替わらずに ndf-relay: で始まる 1 行が出たら、その案内に従う）")


def cmd_notice() -> int:
    for line in notice_lines():
        print(line)
    return 0


# ---------------------------------------------------------------- main


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: relay.py run|stop|mark|install|uninstall|status|startup|question open|close|is-child|notice",
              file=sys.stderr)
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
    if sub == "uninstall":
        return cmd_uninstall()
    if sub == "status":
        return cmd_status()
    if sub == "startup":
        return cmd_startup()
    if sub == "question":
        return cmd_question(rest[0] if rest else "")
    if sub == "is-child":
        # 文脈量の hook（token-guard.sh）が使う。ラッパーが動いていて、hook を呼んだ claude が
        # ラッパーの直接の子なら 0（親のたどりは mark と同じ。間の bash / sh は claude でないので飛ぶ）
        return 0 if under_relay() else 1
    if sub == "notice":
        return cmd_notice()
    print(f"relay.py: 未知の副命令 {sub}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
