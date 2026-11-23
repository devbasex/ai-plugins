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

from ndf import identity

NEXT_FILE = "next.json"
QUESTION_FILE = "question.json"
LAUNCH_FILE = "launch.json"
LAUNCH_ENV = "NDF_RELAY_LAUNCH"
ACTIVITY_FILE = "activity.json"
# レーン・ラッパー・アカウントの名前の形（中継の名前はどれもこの形）
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class SignalError(ValueError):
    """シグナルファイルが壊れている（形が違う・時刻が無い）。"""


class QuestionUnreadable(SignalError):
    """質問のファイルが在るが読めない（権限・ディレクトリ・I/O の誤り）。中身が壊れていることと見分ける（#3814）。"""


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
    return env.get(LAUNCH_ENV) or None  # noqa: NDF501 利用者の入力: 空の環境変数は起動の ID が無いとして扱う


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
    """Claude Code の設定の保存先（規則は `identity.claude_config_root`）。"""
    return identity.claude_config_root(environ)


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
    `launch` の項目を足して書く。違う・今の起動が無いなら書かずに偽（止めた子の hook が次の子の質問を上書きしない。#3814）。
    `launch` が None（hook の環境に起動の ID が無い）なら、`launch.json` が無いときだけ照らさずに書く。起動の ID を
    渡さない更新前のラッパーは `launch.json` を書かないので、そのラッパーの下で更新後の hook が動いても質問を出せる。
    今のラッパーは起動の前に `launch.json` を書く（起動に失敗しても None を書く）ので、その下では書かずに偽になる。
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
        return write_json(path, data, wait, allow=lambda: not (path.parent / LAUNCH_FILE).exists())
    return write_json(path, {**data, "launch": launch}, wait, allow=lambda: current_launch(path.parent) == launch)


def has_question(directory: Path) -> bool:
    return (directory / QUESTION_FILE).is_file()


def read_question(directory: Path) -> Optional[dict[str, Any]]:
    """質問のファイルの中身。無ければ None。壊れていれば `SignalError`、在るが読めなければ `QuestionUnreadable`。"""
    path = directory / QUESTION_FILE
    try:
        return _read(path)
    except UnicodeDecodeError as exc:
        raise SignalError(f"質問のファイルが壊れている（{path}）: {exc}") from exc
    except OSError as exc:
        raise QuestionUnreadable(f"質問のファイルを読めない（{path}）: {exc}") from exc


def question_present(directory: Path) -> bool:
    """質問のファイルが在るか。確かめられない（権限・I/O の誤り）ときは在る側へ倒す（子を止める確かめに使う）。"""
    try:
        os.lstat(directory / QUESTION_FILE)
    except FileNotFoundError:  # noqa: NDF401 ファイル: 無ければ質問は無い
        return False
    except OSError:  # noqa: NDF401 ファイル: 確かめられなければ在るとみなして子を止めない
        return True
    return True


def question_id(question: Mapping[str, Any]) -> Optional[str]:
    """質問の ID。`tool_use_id` が文字列ならそれ、無ければ `at` が文字列ならそれ、どちらも無ければ None。"""
    for key in ("tool_use_id", "at"):
        value = question.get(key)
        if isinstance(value, str):
            return value
    return None


def drop_question(directory: Path, match: Optional[Mapping[str, Any]], wait: float, launch: Optional[str] = None) -> str:
    """ロックを `wait` 秒まで待って取り、ロックの中で読み直した質問を条件に合うときだけ消す（#3814 の I5）。

    `launch` を渡したら今の起動の記録と同じときだけ進む。`match` が None なら中身を問わず消し、あればキーごとに
    質問の値と等しいときだけ消す。壊れた質問は消し、読み直せない質問は消さない。結果は `"dropped"`（消した・既に無い）・
    `"kept"`（条件に合わない）・`"busy"`（ロックを取れない）・`"failed"`（読み直せない・消せない）。
    """
    path = directory / QUESTION_FILE
    with held(Path(f"{path}.lock"), wait) as got:
        if not got:
            return "busy"
        if launch is not None and current_launch(directory) != launch:
            return "kept"
        if match is not None:
            try:
                question = read_question(directory)
            except QuestionUnreadable:  # noqa: NDF401 ファイル: 読み直せない質問は消さずに残し、呼び出し元は止めない側へ倒れる
                return "failed"
            except SignalError:  # 壊れた質問は対象を問わず消す
                question = None
            if question is not None and any(question.get(key) != value for key, value in match.items()):
                return "kept"
        try:
            path.unlink(missing_ok=True)
        except OSError:  # noqa: NDF401 ファイル: 消せなければ残し、呼び出し元は止めない側へ倒れる
            return "failed"
        return "dropped"


def drop(directory: Path, name: str) -> None:
    """読み終えたシグナルファイルを消す。"""
    (directory / name).unlink(missing_ok=True)


def write_activity(directory: Path, tasks: Any, session_id: Optional[str], reply: Optional[str] = None) -> bool:
    """`activity.json` に時刻・セッション・応答・背景の作業の一覧を書く。書けなければ偽（診断の記録なので conductor の応答を止めない）。

    `tasks` は Stop の hook の入力の生の値。リストでなければ、観測できなかったことを表す `null` を書く（空のリストと分ける）。
    `reply` はこの Stop が終えた応答（会話の記録の最後の `assistant` の `uuid`。`observe.reply_id`）。読む側はこの値で
    今の応答の記録かを確かめる（時刻で比べると、同じ秒に入った前の Stop の記録と区別できない）。読めなければ `null`。
    """
    rows = [_task(t) for t in tasks] if isinstance(tasks, list) else None
    try:
        replace_json(directory / ACTIVITY_FILE, {"at": now_iso(), "session_id": session_id, "reply": reply, "tasks": rows})
    except OSError:  # noqa: NDF401 ファイル: 活動の記録が書けなくても、次の Stop が書き直す
        return False
    return True


def _task(item: Any) -> dict[str, str]:
    """背景の作業の要素の `id`・`type`。形が違う値は空の文字列にして要素の数を保つ（読む側で走っているとみなす）。"""
    data = item if isinstance(item, dict) else {}
    return {key: value if isinstance(value := data.get(key), str) else "" for key in ("id", "type")}


def read_activity(directory: Path) -> Optional[dict[str, Any]]:
    """`activity.json`。無い・壊れている・形が違えば None。`tasks` が `null` の記録はそのまま返す。"""
    try:
        data = json.loads((directory / ACTIVITY_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):  # noqa: NDF401 ファイル: 読めない活動の記録は無いものとして判定を今までどおりにする
        return None
    if not isinstance(data, dict) or not isinstance(data.get("at"), str):
        return None
    session, reply, tasks = data.get("session_id"), data.get("reply"), data.get("tasks")
    if session is not None and not isinstance(session, str):
        return None
    if reply is not None and not isinstance(reply, str):
        return None
    if tasks is not None and not (isinstance(tasks, list) and all(_is_task(t) for t in tasks)):
        return None
    return data


def _is_task(item: Any) -> bool:
    return isinstance(item, dict) and isinstance(item.get("id"), str) and isinstance(item.get("type"), str)
