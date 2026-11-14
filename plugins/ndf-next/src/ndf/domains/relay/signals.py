"""hook と中継の間のシグナルファイル。標準ライブラリだけで書く（hook の経路から同じプロセスで呼ぶ。決定 111）。

中継のラッパーは子のセッションへ区間のやり取りの置き場（環境変数 `NDF_RELAY_DIR`）を渡す。hook はそこへ
書いて返り、読むのも待つのも中継である（#3345 の決定 93）。

| ファイル | 書き手 | 中身 |
| --- | --- | --- |
| `next.json` | Stop の hook（`relay-mark`） | 次の区間のブロック。中継が読んで次の区間を始める |
| `question.json` | PreToolUse の hook（`ask-forward`） | AskUserQuestion の問い。中継が人かオーガナイザーへ回す |

オーガナイザーのレーン（`NDF_ORGANIZER_DIR` と `NDF_ORGANIZER_LANE`）では、問いを `questions/<レーン>.json` へも書く
（#3035）。どれも一時の名前で書いて名前を変える。

`next.json` は Stop のたびに書き直されても、最初に書いた時刻 `first_written_at` を保つ。`/goal` の目標が未達で
Stop が続いても、中継は最初の印から数えた静かな時間で切り替える（#2438）。
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional

NEXT_FILE = "next.json"
QUESTION_FILE = "question.json"
# レーン・ラッパー・アカウントの名前の形（中継の名前はどれもこの形）
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class SignalError(ValueError):
    """シグナルファイルが壊れている（形が違う・時刻が無い）。"""


def relay_dir(env: Mapping[str, str]) -> Optional[Path]:
    """中継の下なら区間のやり取りの置き場。外か置き場が無ければ None。"""
    value = env.get("NDF_RELAY_DIR")
    path = Path(value) if value else None
    return path if path is not None and path.is_dir() else None


def dir_env(directory: Path) -> dict[str, str]:
    """子のセッションへ渡す、区間のやり取りの置き場の環境変数。"""
    return {"NDF_RELAY_DIR": str(directory)}


def organizer_question_path(env: Mapping[str, str]) -> Optional[Path]:
    """オーガナイザーのレーンなら、問いを書く置き場。"""
    root, lane = env.get("NDF_ORGANIZER_DIR"), env.get("NDF_ORGANIZER_LANE")
    if not root or not lane or not NAME_RE.match(lane):
        return None
    return Path(root) / "questions" / f"{lane}.json"


def claude_config_root(environ: Mapping[str, str]) -> Path:
    """Claude Code の設定の置き場。`CLAUDE_CONFIG_DIR` があればそれ、無ければ `~/.claude`。"""
    value = environ.get("CLAUDE_CONFIG_DIR")
    return Path(value) if value else Path.home() / ".claude"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


@contextmanager
def held(path: Path, wait: float) -> Iterator[bool]:
    """錠のファイル `path`（無ければ 0o600 で作る）の排他を `wait` 秒まで取りにいく。取れたかを渡し、終わりに解いて閉じる。"""
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    got = False
    try:
        got = lock(fd, wait)
        yield got
    finally:
        if got:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def replace_text(path: Path, text: str, mode: Optional[int] = None) -> None:
    """親の置き場を作り、同じ置き場の一時の名前（`.<名前>.<pid>.tmp`）へ書いてから名前を変える。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    if mode is not None:
        tmp.chmod(mode)
    os.replace(tmp, path)


def replace_json(path: Path, data: Mapping[str, Any]) -> None:
    replace_text(path, json.dumps(dict(data), ensure_ascii=False))


def append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    """親の置き場を作り、先頭に時刻 `at` を付けた 1 行の JSON を追記する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": now_iso(), **dict(row)}, ensure_ascii=False) + "\n")


def write_json(path: Path, data: Mapping[str, Any], wait: float, keep: tuple[str, ...] = ()) -> bool:
    """排他（`<path>.lock`）を `wait` 秒まで待って書く。`keep` の項目は前の中身の値を保つ。取れなければ偽。

    hook は書けなくても通す（偽を返すだけ）。書けない理由の `OSError` は呼び出し元へ送出する。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with held(Path(f"{path}.lock"), wait) as got:
        if not got:
            return False
        before = _read(path) if keep else None
        merged = dict(data)
        for key in keep:
            if before and key in before:
                merged[key] = before[key]
        replace_json(path, merged)
        return True


def _read(path: Path) -> Optional[dict[str, Any]]:
    """シグナルファイルの中身。無ければ None、壊れていれば `SignalError`。"""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:  # noqa: NDF401 ファイル: 印が無ければ切り替えない
        return None
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise SignalError(f"シグナルファイルが壊れている（{path}）: {exc}") from exc
    if not isinstance(data, dict):
        raise SignalError(f"シグナルファイルの形が違う（{path}）")
    return data


def write_next(directory: Path, session_id: str, block: str, goal_active: bool, wait: float) -> bool:
    """次の区間の印。`/goal` の続き（`stop_hook_active`）でも同じに書き、最初に書いた時刻を保つ（#2438）。"""
    at = now_iso()
    data = {"session_id": session_id, "block": block, "goal_active": goal_active, "first_written_at": at, "written_at": at}
    return write_json(directory / NEXT_FILE, data, wait, keep=("first_written_at",))


def read_next(directory: Path) -> Optional[dict[str, Any]]:
    """中継が読む次の区間の印。無ければ None。時刻を持たない印は `SignalError`（今の時刻へ倒さない）。"""
    data = _read(directory / NEXT_FILE)
    if data is None:
        return None
    if not isinstance(data.get("first_written_at"), str) or not isinstance(data.get("block"), str):
        raise SignalError(f"次の区間の印に first_written_at か block が無い（{directory / NEXT_FILE}）")
    return data


def write_question(path: Path, session_id: str, payload: Mapping[str, Any], wait: float) -> bool:
    """AskUserQuestion の問い。転送は中継が待つ。"""
    tool_input = payload.get("tool_input")
    data = {
        "session_id": session_id,
        "tool_use_id": payload.get("tool_use_id"),
        "questions": tool_input.get("questions") if isinstance(tool_input, dict) else None,
        "cwd": payload.get("cwd"),
        "at": now_iso(),
    }
    return write_json(path, data, wait)


def has_question(directory: Path) -> bool:
    return (directory / QUESTION_FILE).is_file()


def drop(directory: Path, name: str) -> None:
    """読み終えたシグナルファイルを消す。"""
    (directory / name).unlink(missing_ok=True)
