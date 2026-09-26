"""ラッパーのファイル名の定数と、どのモジュールも使う小さな関数（#895・#1142 の C6）。

標準ライブラリと、バージョンディレクトリにも入る `lib/clock.py`・`lib/jsonio.py` だけを import する（I2）。
時刻と JSON の読みはライブラリの 1 つの実装を使う（決定 6）。JSON の書き込みだけは、権限 0600 と
symlink の指す先の置き換えを保つため `_write_file` の上に置く。
"""
from __future__ import annotations

import datetime as _dt
import fcntl
import json
import os
import sys
import time

# relay_lib の親。プラグインでは `scripts/`、複製ではバージョンディレクトリ（`relay-<版>-<digest>/`）
PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LIB = os.path.join(PKG_ROOT, "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)
import clock  # noqa: E402
import jsonio  # noqa: E402

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


def env_num(name: str, default: float) -> float:
    """環境変数の数。無い・読めないときは `default`。"""
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def quiet_seconds() -> float:
    """静止の秒数（`NDF_RELAY_QUIET`）。切り替えの実際の待ちと、カットポイントの告知の両方が読む。"""
    return env_num("NDF_RELAY_QUIET", 5)


def stamp(t: float | None = None) -> str:
    """`log.jsonl`・`next.json` の時刻の形（UTC・ミリ秒・`Z`）。`t` は epoch 秒で、無ければ今。"""
    t = time.time() if t is None else t
    return clock.iso(_dt.datetime.fromtimestamp(t, _dt.timezone.utc), "z-ms")


def parse_iso(s) -> float | None:
    """タイムゾーン付きの ISO 8601 を epoch 秒へ。読めなければ None。"""
    d = clock.parse(s, naive="reject") if isinstance(s, str) else None
    return d.timestamp() if d is not None else None


def load_json(path: str):
    """JSON を読む。無い・壊れているときは None。"""
    return jsonio.read(path, missing=None, broken=None)


def write_json_atomic(path: str, data) -> None:
    _write_file(path, json.dumps(data, ensure_ascii=False).encode(), 0o600)


def state_root() -> str:
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "ndf", "relay")


def data_dir() -> str:
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "ndf")


def config_dir() -> str:
    """複製・複製の版・ラッパーの rc・バージョンディレクトリの親。devbase では永続化される `~/.claude` の下になる。"""
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(base, "ndf")


def copy_path() -> str:
    return os.path.join(config_dir(), "relay.py")


def launcher_path() -> str:
    """この relay_lib を読み込んだランチャー。プラグインでは隣の、複製ではバージョンディレクトリの親の `relay.py`。"""
    own = os.path.join(PKG_ROOT, "relay.py")
    return own if os.path.isfile(own) else os.path.join(os.path.dirname(PKG_ROOT), "relay.py")


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


def remove(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


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


class LockBusy(Exception):
    pass


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


def read_text(path: str) -> str | None:
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


def _write_file(path: str, body: bytes, mode: int) -> None:
    """一時ファイルに書いてから置き換える。symlink は指す先を置き換える。"""
    real = os.path.realpath(path)
    tmp = f"{real}.{os.getpid()}.tmp"
    with open(tmp, "wb") as f:
        f.write(body)
    os.chmod(tmp, mode)
    os.replace(tmp, real)
