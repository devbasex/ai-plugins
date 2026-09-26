"""ラッパーの記録: 作業ディレクトリの `log.jsonl` と合図 `next.json`、起動の上限（#895・#1142 の C6）。

`log.jsonl` と `next.json` を書くのは `RelayRecord` だけである（I12）。`run` の `Relay` も Stop hook の `mark` も
これを通して書くため、行とキーの形の定義は 1 か所に残る。形は版をまたいで変えない（I11）。
"""
from __future__ import annotations

import errno
import json
import os
import random
import time

from .common import (COUNT_LOCK, LOG_FILE, MARK_FILE, _lock, _unlock, env_num, load_json, parse_iso, remove,
                     stamp, state_root, write_json_atomic)


class RelayRecord:
    """1 つのラッパーの作業ディレクトリ（`NDF_RELAY_DIR`）の記録。"""

    def __init__(self, d: str):
        self.dir = d

    def path(self, name: str) -> str:
        return os.path.join(self.dir, name)

    def log(self, **row) -> None:
        """`log.jsonl` へ 1 行を足す。先頭は `event` と `at`（無ければ今）。"""
        row = {"event": row.pop("event"), "at": row.pop("at", None) or stamp(), **row}
        with open(self.path(LOG_FILE), "a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def mark_skipped(self, section: int | None, reason: str, tasks: list[dict], held: bool) -> None:
        self.log(event="mark_skipped", section=section, reason=reason, tasks=tasks, held=held)

    def read_mark(self):
        """合図。`command` の無いものや読めないものは None。"""
        m = load_json(self.path(MARK_FILE))
        if not isinstance(m, dict) or not isinstance(m.get("command"), str) or not m["command"]:
            return None
        return m

    def write_mark(self, command: str, data: dict) -> None:
        write_json_atomic(self.path(MARK_FILE), {
            "command": command,
            "cwd": data.get("cwd") or "",
            "session_id": data.get("session_id") or "",
            "transcript_path": data.get("transcript_path") or "",
            "written_at": stamp(),
        })

    def drop_mark(self) -> None:
        remove(self.path(MARK_FILE))


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


def make_relay_dir() -> str:
    root = state_root()
    os.makedirs(root, mode=0o700, exist_ok=True)
    stamp_ = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    for _ in range(20):
        d = os.path.join(root, f"{stamp_}-{os.getpid()}-{random.randint(0, 99999999):08d}")
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
        self.lock = None  # 取った `count.lock`（common._lock の戻り値）
        self.max_starts = int(env_num("NDF_RELAY_MAX_STARTS", 20))
        self.spin = env_num("NDF_RELAY_SPIN", 120)

    def take(self) -> bool:
        if self.lock is not None:
            return True
        held = _lock(os.path.join(state_root(), COUNT_LOCK), 0)
        if held is None:
            return False
        self.lock = held
        return True

    def release(self) -> None:
        _unlock(self.lock)
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
