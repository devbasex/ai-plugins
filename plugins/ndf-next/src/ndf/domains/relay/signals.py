"""hook と中継の間のシグナルファイル。標準ライブラリだけで書く（hook の経路から呼ぶ）。

中継のラッパーは子のセッションへ区間のやり取りの置き場（環境変数 `NDF_RELAY_DIR`）を渡す。hook はそこへ
書いて返り、読むのも待つのも中継である（#3345 の決定 93）。

| ファイル | 書き手 | 中身 |
| --- | --- | --- |
| `next.json` | Stop の hook（`relay-mark`） | 次の区間のブロック。中継が読んで次の区間を始める |
| `question.json` | PreToolUse の hook（`ask-forward`） | AskUserQuestion の問い。中継が人かオーガナイザーへ回す |

オーガナイザーのレーン（`NDF_ORGANIZER_DIR` と `NDF_ORGANIZER_LANE`）では、問いを `questions/<レーン>.json` へも書く
（#3035）。どれも一時の名前で書いて名前を変える。
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

NEXT_FILE = "next.json"
QUESTION_FILE = "question.json"
LANE_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def relay_dir(env: Mapping[str, str]) -> Optional[Path]:
    """中継の下なら区間のやり取りの置き場。外か置き場が無ければ None。"""
    value = env.get("NDF_RELAY_DIR")
    path = Path(value) if value else None
    return path if path is not None and path.is_dir() else None


def organizer_question_path(env: Mapping[str, str]) -> Optional[Path]:
    """オーガナイザーのレーンなら、問いを書く置き場。"""
    root, lane = env.get("NDF_ORGANIZER_DIR"), env.get("NDF_ORGANIZER_LANE")
    if not root or not lane or not LANE_RE.match(lane):
        return None
    return Path(root) / "questions" / f"{lane}.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, data: Mapping[str, Any], wait: float) -> bool:
    """排他（`<path>.lock`）を `wait` 秒まで待って書く。排他を取れない・書けなければ偽。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(f"{path}.lock", os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:  # noqa: NDF401 ファイル: 置き場に書けなければ書かずに返る（hook は通す）
        return False
    try:
        if not lock(fd, wait):
            return False
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(dict(data), ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError:  # noqa: NDF401 ファイル: 書けなければ書かずに返る（hook は通す）
        return False
    finally:
        os.close(fd)


def lock(fd: int, wait: float) -> bool:
    """`flock` の排他を `wait` 秒まで取りにいく。取れたら真。"""
    deadline = time.monotonic() + wait
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.01)


def write_next(directory: Path, session_id: str, block: str, goal_active: bool, wait: float) -> bool:
    """次の区間の印。`/goal` の続き（`stop_hook_active`）でも同じに書く（#2438）。"""
    data = {"session_id": session_id, "block": block, "goal_active": goal_active, "at": _now()}
    return write_json(directory / NEXT_FILE, data, wait)


def read_next(directory: Path) -> Optional[dict[str, Any]]:
    """中継が読む次の区間の印。無い・壊れていれば None。"""
    try:
        data = json.loads((directory / NEXT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):  # noqa: NDF401 ファイル: 印が無ければ切り替えない
        return None
    return data if isinstance(data, dict) else None


def write_question(path: Path, session_id: str, payload: Mapping[str, Any], wait: float) -> bool:
    """AskUserQuestion の問い。転送は中継が待つ。"""
    data = {
        "session_id": session_id,
        "tool_use_id": payload.get("tool_use_id"),
        "questions": (payload.get("tool_input") or {}).get("questions"),  # noqa: NDF501 利用者の入力: 入力の無い問いは問いの無い印
        "cwd": payload.get("cwd"),
        "at": _now(),
    }
    return write_json(path, data, wait)
