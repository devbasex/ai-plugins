"""テストの枠の記録（#2711）。重い処理 1 本の終わりに `events.jsonl` へ 1 行を足し、期間で集計する。

枠のロックには依存しない（`slots` がこのモジュールを使い、逆向きの読み込みは無い）。
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path

from filelock import FileLock

# 記録を残す期間と行数（I11）
KEEP_DAYS = 30
KEEP_ROWS = 10_000
SECONDS_PER_DAY = 86400
# 記録と停止の時刻のロックを待つ上限
RECORD_LOCK_SECONDS = 10.0

EVENTS_FILE = "events.jsonl"

# 記録の行の結果
FINISHED = "finished"
GAVE_UP = "gave_up"
MEMORY_STOPPED = "memory_stopped"
INTERRUPTED = "interrupted"


def _lock(path: Path) -> FileLock:
    # ソフトロックへ落とさない（カーネルのロックでないと持ち主の消滅で解けない）。ロックのファイルは消さない
    return FileLock(path, thread_local=False, fallback_to_soft=False, preserve_lock_file=True)


def _replace_text(path: Path, text: str) -> None:
    """一時ファイルへ書いてから置き換える（読む側が書きかけを見ない）。"""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _now_iso(now: float) -> str:
    return datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds")


def _parse_iso(text: object) -> float | None:
    if not isinstance(text, str):
        return None
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def append_event(directory: Path, row: Mapping[str, object], now: float | None = None) -> None:
    """記録へ 1 行を足す。30 日より古い行と 10,000 行を超えた分は、足すときに古い方から消す（I11）。"""
    now = time.time() if now is None else now
    path = directory / EVENTS_FILE
    line = json.dumps(row, ensure_ascii=False)
    with _lock(directory / (EVENTS_FILE + ".lock")).acquire(timeout=RECORD_LOCK_SECONDS):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            lines = []
        kept = _prune(lines, now)
        if len(kept) == len(lines) and len(lines) < KEEP_ROWS:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            return
        kept = (kept + [line])[-KEEP_ROWS:]
        _replace_text(path, "".join(ln + "\n" for ln in kept))


def _prune(lines: list[str], now: float) -> list[str]:
    """30 日より古い行を除いた行（時刻の読めない行は残す）。"""
    cutoff = now - KEEP_DAYS * SECONDS_PER_DAY
    return [ln for ln in lines if (_parse_iso(_loads(ln).get("at")) or now) >= cutoff]


def _loads(line: str) -> dict[str, object]:
    try:
        data = json.loads(line)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def read_events(directory: Path) -> list[dict[str, object]]:
    try:
        lines = (directory / EVENTS_FILE).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    return [row for row in map(_loads, lines) if row]


def report(directory: Path, since_seconds: float, now: float | None = None, current_oom: int | None = None) -> dict[str, int | float]:
    """期間の中で待った・止めた・諦めた回数と秒、OOM Killer の回数の増分を返す。"""
    now = time.time() if now is None else now
    rows = _rows_since(directory, since_seconds, now)
    waited = [float(row.get("waited_seconds") or 0) for row in rows]
    return {
        "runs": len(rows),
        "waited_runs": sum(1 for w in waited if w > 0),
        "waited_seconds": round(sum(waited), 1),
        "memory_stopped": sum(1 for row in rows if row.get("result") == MEMORY_STOPPED),
        "gave_up": sum(1 for row in rows if row.get("result") == GAVE_UP),
        "oom_kill_increase": _oom_increase([row.get("oom_kill") for row in rows] + [current_oom]),
    }


def _rows_since(directory: Path, since_seconds: float, now: float) -> list[dict[str, object]]:
    """期間の中の記録の行を時刻の順に返す。時刻の読めない行は除く。"""
    rows = [(at, row) for row in read_events(directory) if (at := _parse_iso(row.get("at"))) is not None and at >= now - since_seconds]
    rows.sort(key=lambda item: item[0])
    return [row for _, row in rows]


def _oom_increase(counts: Sequence[object]) -> int:
    """OOM Killer の回数の並びの増分。真偽値を除いた整数だけを取る。"""
    values = [c for c in counts if isinstance(c, int) and not isinstance(c, bool)]
    # 隣どうしの差のうち正の値だけを足す（再起動で回数が減っても負にならない）
    return sum(max(0, b - a) for a, b in zip(values, values[1:]))
