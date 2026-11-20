"""hook と中継の間のシグナルファイル。標準ライブラリだけで書く（hook の経路から同じプロセスで呼ぶ。決定 111）。

中継のラッパーは子のセッションへセグメントのやり取りの保存先（環境変数 `NDF_RELAY_DIR`）を渡す。hook はそこへ
書いて返り、読むのも待つのも中継である（#3345 の決定 93）。

| ファイル | 書き手 | 中身 |
| --- | --- | --- |
| `next.json` | Stop の hook（`relay-marker`） | 次のセグメントのブロック。中継が読んで次のセグメントを始める |
| `question.json` | PreToolUse の hook（`ask-forward`） | AskUserQuestion の質問。中継が人かオーガナイザーへ回す |
| `launch.json` | 中継のラッパー | 今の子の起動の ID（子へは `NDF_RELAY_LAUNCH` で渡す）。止めた子の hook に質問を書かせない（#3814） |

オーガナイザーのレーン（`NDF_ORGANIZER_DIR` と `NDF_ORGANIZER_LANE`）では、質問を `questions/<レーン>.json` へも書く
（#3035）。どれも一時の名前で書いて名前を変える。

`next.json` は Stop のたびに書き直されても、最初に書いた時刻 `first_written_at` を保つ。`/goal` の目標が未達で
Stop が続いても、中継は最初のマーカーから数えた静かな時間で切り替える（#2438）。

中継と `ndf relay stop` の間の停止シグナルファイルは `stop_signal` が持つ（hook は読まない。事実の表を引くため、
hook の経路へ入れない）。
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
from typing import Any, Callable, Iterator, Mapping, Optional, Union

NEXT_FILE = "next.json"
QUESTION_FILE = "question.json"
LAUNCH_FILE = "launch.json"
LAUNCH_ENV = "NDF_RELAY_LAUNCH"
# レーン・ラッパー・アカウントの名前の形（中継の名前はどれもこの形）
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class SignalError(ValueError):
    """シグナルファイルが壊れている（形が違う・時刻が無い）。"""


def relay_dir(env: Mapping[str, str]) -> Optional[Path]:
    """中継の下ならセグメントのやり取りの保存先。外か保存先が無ければ None。"""
    value = env.get("NDF_RELAY_DIR")
    path = Path(value) if value else None
    return path if path is not None and path.is_dir() else None


def launch_env(directory: Path, launch: str) -> dict[str, str]:
    """子のセッションへ渡す、セグメントのやり取りの保存先と起動の ID の環境変数。"""
    return {"NDF_RELAY_DIR": str(directory), LAUNCH_ENV: launch}


def relay_launch(env: Mapping[str, str]) -> Optional[str]:
    """hook の環境の起動の ID。無ければ None。"""
    return env.get(LAUNCH_ENV) or None


def set_launch(directory: Path, launch: Optional[str]) -> None:
    """今の起動の ID を記録する。None は「今の起動が無い」で、止めた子の hook が書く前に外す（`question.json.lock` を持って呼ぶ）。"""
    replace_json(directory / LAUNCH_FILE, {"launch": launch})


def current_launch(directory: Path) -> Optional[str]:
    """今の起動の ID。無い・壊れている・読めないなら None（書く側は書かない側へ倒れる）。"""
    try:
        data = _read(directory / LAUNCH_FILE)
    except (SignalError, OSError, UnicodeDecodeError):  # noqa: NDF401 ファイル: 読めない記録は今の起動が無いとして書かない
        return None
    value = data.get("launch") if data else None
    return value if isinstance(value, str) else None


def organizer_question_path(env: Mapping[str, str]) -> Optional[Path]:
    """オーガナイザーのレーンなら、質問を書く保存先。"""
    root, lane = env.get("NDF_ORGANIZER_DIR"), env.get("NDF_ORGANIZER_LANE")
    if not root or not lane or not NAME_RE.match(lane):
        return None
    return Path(root) / "questions" / f"{lane}.json"


def claude_config_root(environ: Mapping[str, str]) -> Path:
    """Claude Code の設定の保存先。`CLAUDE_CONFIG_DIR` があればそれ、無ければ `~/.claude`。"""
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
    """親の保存先を作り、同じ保存先の一時の名前（`.<名前>.<pid>.tmp`）へ書いてから名前を変える。

    `mode` を渡すと、一時のファイルを 0600 で作って書く前にその権限にする（秘密を持つ中身が一時でも広い権限で置かれない）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    if mode is None:
        tmp.write_text(text, encoding="utf-8")
    else:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            os.fchmod(f.fileno(), mode)
            f.write(text)
    os.replace(tmp, path)


def replace_json(path: Path, data: Mapping[str, Any]) -> None:
    replace_text(path, json.dumps(dict(data), ensure_ascii=False))


def append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    """親の保存先を作り、先頭に時刻 `at` を付けた 1 行の JSON を追記する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": now_iso(), **dict(row)}, ensure_ascii=False) + "\n")


def write_json(
    path: Path, data: Mapping[str, Any], wait: float, keep: tuple[str, ...] = (), allow: Optional[Callable[[], bool]] = None
) -> bool:
    """排他（`<path>.lock`）を `wait` 秒まで待って書く。`keep` の項目は前の中身の値を保つ。取れなければ偽。

    `allow` を渡すと、排他を取った後・書く前に呼び、偽なら書かずに偽を返す。
    書けない理由の `OSError` は呼び出し元へ送出する。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with held(Path(f"{path}.lock"), wait) as got:
        if not got or (allow is not None and not allow()):
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
    except FileNotFoundError:  # noqa: NDF401 ファイル: マーカーが無ければ切り替えない
        return None
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise SignalError(f"シグナルファイルが壊れている（{path}）: {exc}") from exc
    if not isinstance(data, dict):
        raise SignalError(f"シグナルファイルの形が違う（{path}）")
    return data


def write_next(directory: Path, session_id: str, block: str, goal_active: bool, wait: float) -> bool:
    """次のセグメントのマーカー。`/goal` の続き（`stop_hook_active`）でも同じに書き、最初に書いた時刻を保つ（#2438）。"""
    at = now_iso()
    data = {"session_id": session_id, "block": block, "goal_active": goal_active, "first_written_at": at, "written_at": at}
    return write_json(directory / NEXT_FILE, data, wait, keep=("first_written_at",))


def read_next(directory: Path) -> Optional[dict[str, Any]]:
    """中継が読む次のセグメントのマーカー。無ければ None。時刻を持たないマーカーは `SignalError`（今の時刻へ倒さない）。"""
    data = _read(directory / NEXT_FILE)
    if data is None:
        return None
    if not isinstance(data.get("first_written_at"), str) or not isinstance(data.get("block"), str):
        raise SignalError(f"次のセグメントのマーカーに first_written_at か block が無い（{directory / NEXT_FILE}）")
    return data


class _Unchecked:
    """`write_question` の `launch` を渡さないこと（起動の ID を照らさない書き手。オーガナイザーの質問の保存先）。"""


UNCHECKED = _Unchecked()


def write_question(
    path: Path, session_id: str, payload: Mapping[str, Any], wait: float, launch: Union[str, None, _Unchecked] = UNCHECKED
) -> bool:
    """AskUserQuestion の質問。転送は中継が待つ。

    `launch` を渡すと、排他の中で同じ保存先の今の起動の記録（`current_launch`）と照らし、同じ文字列のときだけ
    `launch` の項目を足して書く。違う・None・今の起動が無いなら書かずに偽（止めた子の hook が次の子の質問を上書きしない。#3814）。
    """
    tool_input = payload.get("tool_input")
    data = {
        "session_id": session_id,
        "tool_use_id": payload.get("tool_use_id"),
        "questions": tool_input.get("questions") if isinstance(tool_input, dict) else None,
        "cwd": payload.get("cwd"),
        "at": now_iso(),
    }
    if isinstance(launch, _Unchecked):
        return write_json(path, data, wait)
    if launch is None:
        return False
    return write_json(path, {**data, "launch": launch}, wait, allow=lambda: current_launch(path.parent) == launch)


def has_question(directory: Path) -> bool:
    return (directory / QUESTION_FILE).is_file()


def drop(directory: Path, name: str) -> None:
    """読み終えたシグナルファイルを消す。"""
    (directory / name).unlink(missing_ok=True)
