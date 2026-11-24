"""hook と中継の間のシグナルファイル。標準ライブラリだけで書く（hook の経路から同じプロセスで呼ぶ。決定 111）。

中継のラッパーは子のセッションへセグメントのやり取りの保存先（環境変数 `NDF_RELAY_DIR`）を渡す。hook はそこへ
書いて返り、読むのも待つのも中継である（docs/specifications/ndf-rebuild.md の決定 93）。

| ファイル | 書き手 | 中身 |
| --- | --- | --- |
| `next.json` | Stop の hook（`relay-marker`） | 次のセグメントのブロック。中継が読んで次のセグメントを始める |
| `question.json` | PreToolUse の hook（`ask-forward`） | AskUserQuestion の質問。中継が人かオーガナイザーへ回す |
| `launch.json` | 中継のラッパー | 今の子の起動の ID（子へは `NDF_RELAY_LAUNCH` で渡す）。止めた子の hook に質問を書かせない（#3814） |
| `busy-<起動の ID>.json` | 子の会話の hook（SessionStart・PreToolUse の `relay-busy` と Stop の `relay-marker`） | その起動のバックグラウンドの作業の件数。ラッパーは書かず、ほかの起動のものを消すだけである（#4049） |
| `keep-<会話の ID>.json` | 起床の hook（Stop のバックグラウンド）と UserPromptSubmit の guard（`turn`） | 会話ごとのキャッシュの起床の状態（世代・待ち・起床のフラグ）。子を止める排他の中で書く（#4068） |
| `cache-log.jsonl` | 同上 | 起床の記録（待ちの始まりと終わり・起床・その結果・やめた理由）。`ndf relay cache report` が集計する（#4068） |

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
from contextlib import contextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, NamedTuple, Optional, Union

from ndf import identity

NEXT_FILE = "next.json"
QUESTION_FILE = "question.json"
LAUNCH_FILE = "launch.json"
LAUNCH_ENV = "NDF_RELAY_LAUNCH"
ACTIVITY_FILE = "activity.json"
BUSY_PREFIX = "busy-"
BUSY_SOURCES = ("session", "start", "stop")
# 起動の ID の形（ラッパーが作る `uuid.uuid4().hex`）
LAUNCH_ID_RE = re.compile(r"^[0-9a-f]{32}$")
# 子を止める排他のファイル。質問の転送とバックグラウンドの作業の開始の記録が共有する
STOP_LOCK = f"{QUESTION_FILE}.lock"
# 会話ごとのキャッシュの起床の状態の名前の頭と、起床の記録（#4068）
KEEP_PREFIX = "keep-"
CACHE_LOG = "cache-log.jsonl"
# 会話の ID の形（状態のファイルの名前を作る前に突き合わせる）
SESSION_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
# 世代の書き込み元のうち、起床の hook でないもの（guard・同じ応答の取り合い・到着による失効）
BY_TURN, BY_CONTESTED, BY_ARRIVAL = "turn", "contested", "arrival"
# 待ちの項目（待ちを終えるか取り消すときにまとめて消す）
WAIT_KEYS = ("wait_id", "k", "n_max", "spent", "wait_stop", "opened")
# レーン・ラッパー・アカウントの名前の形（中継の名前はどれもこの形）
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class SignalError(ValueError):
    """シグナルファイルが壊れている（形が違う・時刻が無い）。"""


class QuestionUnreadable(SignalError):
    """質問のファイルが在るが読めない（権限・ディレクトリ・I/O の誤り）。中身が壊れていることと見分ける（#3814）。"""


class Background(NamedTuple):
    """今の起動のバックグラウンドの作業の記録（`read_busy`）。`count` は作業、`monitors` は監視の件数。

    `source` は書いた事象（`start` なら `count` は「少なくとも 1 件」）、`at` は書いた時刻。
    """

    count: int
    source: str
    at: str
    monitors: int


def require_keys() -> None:
    """中継の変数の名前をすべてコントラクトから引けることを確かめる。引けなければ `identity.EnvNameError`（#4126 の決定 3）。"""
    identity.env_name("relay-dir")
    identity.env_name("relay-session")


def keys_known() -> bool:
    """`require_keys` が通るか。通らないコントラクトでは、hook は中継へもオーガナイザーへも書かない側へ倒す。"""
    try:
        require_keys()
    except identity.EnvNameError:  # noqa: NDF401 ファイル: コントラクトの誤りは hook の例外にせず、呼び手が対象外として扱う
        return False
    return True


def relay_dir(env: Mapping[str, str]) -> Optional[Path]:
    """中継の下ならセグメントのやり取りの保存先。外か保存先が無ければ None。"""
    value = env.get(identity.env_name("relay-dir"))
    path = Path(value) if value else None
    return path if path is not None and path.is_dir() else None


def under_relay(env: Mapping[str, str]) -> bool:
    """中継のラッパーの下か。保存先が在るかは見ない。"""
    return bool(env.get(identity.env_name("relay-dir")))


def launch_env(directory: Path, launch: str, session_id: str) -> dict[str, str]:
    """子のセッションへ渡す、セグメントのやり取りの保存先・起動の ID・子の会話の ID の環境変数。"""
    return {
        identity.env_name("relay-dir"): str(directory),
        LAUNCH_ENV: launch,
        identity.env_name("relay-session"): session_id,
    }


def relay_launch(env: Mapping[str, str]) -> Optional[str]:
    """hook の環境の起動の ID。無ければ None。"""
    return env.get(LAUNCH_ENV) or None  # noqa: NDF501 利用者の入力: 空の環境変数は起動の ID が無いとして扱う


def relay_session(env: Mapping[str, str]) -> Optional[str]:
    """hook の環境の子の会話の ID。無ければ None。"""
    return env.get(identity.env_name("relay-session")) or None  # noqa: NDF501 利用者の入力: 空の環境変数は会話の ID が無いとして扱う


def set_launch(directory: Path, launch: Optional[str], rewake: bool = False, claude_version: Optional[str] = None) -> None:
    """今の起動の ID を記録する。None は「今の起動が無い」で、止めた子の hook が書く前に外す（`question.json.lock` を持って呼ぶ）。

    `rewake` はその起動に起床の hook の登録を渡したか、`claude_version` はその起動の claude のバージョン（#4068）。
    """
    replace_json(directory / LAUNCH_FILE, {"launch": launch, "rewake": rewake, "claude_version": claude_version})


def _launch_file(directory: Path) -> dict[str, Any]:
    """今の起動の記録。無い・読めないなら空（起床の登録を渡していない起動として扱う）。"""
    try:
        found = _read(directory / LAUNCH_FILE)
    except (SignalError, OSError, UnicodeDecodeError):  # 読めない記録は今の起動が無いとして書かない
        found = None
    return {} if found is None else found


def rewake_on(directory: Path, launch: str) -> bool:
    """今の起動が `launch` で、その起動に起床の hook の登録を渡したか（#4068）。"""
    found = _launch_file(directory)
    return found.get("launch") == launch and found.get("rewake") is True


def launch_version(directory: Path) -> Optional[str]:
    """今の起動の claude のバージョン（ラッパーが `set_launch` に渡した値）。無ければ None。"""
    got = _launch_file(directory).get("claude_version")
    return got if isinstance(got, str) else None


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
    """`activity.json` に時刻・セッション・応答・バックグラウンドの作業の一覧を書く。書けなければ偽（診断の記録なので conductor の応答を止めない）。

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
    """バックグラウンドの作業の要素の `id`・`type`。形が違う値は空の文字列にして要素の数を保つ（読む側で走っているとみなす）。"""
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
    if not all(isinstance(data.get(key), (str, type(None))) for key in ("session_id", "reply")):
        return None
    tasks = data.get("tasks")
    if tasks is not None and not (isinstance(tasks, list) and all(_is_task(t) for t in tasks)):
        return None
    return data


def _is_task(item: Any) -> bool:
    return isinstance(item, dict) and isinstance(item.get("id"), str) and isinstance(item.get("type"), str)


def _busy_path(directory: Path, launch: str) -> Optional[Path]:
    """起動の記録のファイル。起動の ID の形が違えば None（環境変数の値をファイルの名前に使わない）。"""
    return directory / f"{BUSY_PREFIX}{launch}.json" if LAUNCH_ID_RE.fullmatch(launch) else None


def write_busy(directory: Path, launch: str, session_id: str, count: int, source: str, monitors: int = 0) -> bool:
    """起動の記録に作業の件数 `count` と監視の件数 `monitors` を書く。起動の ID の形が違うか書けなければ偽で、前の中身のまま（#4049）。

    `source` は `BUSY_SOURCES` のどれか。排他は取らない（開始の記録は `record_start` と `record_monitor` が取って呼ぶ）。
    """
    path = _busy_path(directory, launch)
    if path is None:
        return False
    data = {"launch": launch, "session_id": session_id, "count": count, "monitors": monitors, "source": source, "at": now_iso()}
    try:
        replace_json(path, data)
    except OSError:  # noqa: NDF401 ファイル: 書けなければ偽を返し、呼び手が扱う
        return False
    return True


def record_start(directory: Path, launch: str, session_id: str, wait: float) -> bool:
    """子を止める排他を `wait` 秒まで待って取り、今の起動が `launch` のときだけ件数 1 を書く。書けなければ偽（#4049）。

    ラッパーも同じ排他の中で子を止め、今の起動を外すので、止めた子の開始は偽になる。
    """
    try:
        with held(directory / STOP_LOCK, wait) as got:
            return got and current_launch(directory) == launch and write_busy(directory, launch, session_id, 1, "start")
    except OSError:  # noqa: NDF401 ファイル: 排他を取れなければ偽を返し、呼び手が開始を拒否する
        return False


def record_monitor(directory: Path, launch: str, session_id: str, wait: float) -> bool:
    """`record_start` と同じ排他の中で、今の起動の記録の作業の件数を保ったまま監視を 1 件以上にする。書けなければ偽（#4049）。

    既に監視が記録されていれば、排他を取らずに真を返す（書くものが無い監視の開始を、排他の競合で遅らせない）。
    記録が無ければ書かない（作業の件数が分からないので、0 件と書くとラッパーが作業を抱えた子を止めうる）。
    """
    if _has_monitors(read_busy(directory, launch, session_id)):
        return True
    try:
        with held(directory / STOP_LOCK, wait) as got:
            found = read_busy(directory, launch, session_id) if got and current_launch(directory) == launch else None
            if found is None:
                return False
            return _has_monitors(found) or write_busy(directory, launch, session_id, found[0], found[1], 1)
    except OSError:  # noqa: NDF401 ファイル: 排他を取れなければ偽を返す（監視の開始は止めない）
        return False


def _has_monitors(found: Optional[tuple[int, str, str, int]]) -> bool:
    """`read_busy` の返りが監視を 1 件以上持つか。"""
    return found is not None and found[3] > 0


def read_busy(directory: Path, launch: str, session_id: str) -> Optional[Background]:
    """今の起動・今の会話の記録。無い・読めない・形が違えば None（不明）。"""
    path = _busy_path(directory, launch)
    try:
        data = _read(path) if path is not None else None
    except (SignalError, OSError, UnicodeDecodeError):  # noqa: NDF401 ファイル: 読めない記録は件数が分からないとして扱う
        return None
    if data is None or data.get("launch") != launch or data.get("session_id") != session_id:
        return None
    count, source, at = data.get("count"), data.get("source"), data.get("at")
    monitors = data["monitors"] if "monitors" in data else 0
    if not (_is_count(count) and _is_count(monitors) and source in BUSY_SOURCES and _is_zoned(at)):
        return None
    if source == "start" and count == 0:  # 開始の記録は「少なくとも 1 件」なので、0 件は矛盾
        return None
    return Background(count, source, at, monitors)


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_zoned(value: Any) -> bool:
    """タイムゾーンを持つ時刻の文字列か（`now_iso` の形）。"""
    try:
        return isinstance(value, str) and datetime.fromisoformat(value).tzinfo is not None
    except ValueError:  # noqa: NDF401 ファイル: 時刻として読めない値は形が違うとして扱う
        return False


class Generation(NamedTuple):
    """起床の予約を会話ごとに 1 つに決める世代（#4068 の詳細設計の「内部の型」）。

    `by` は書き込み元の ID（起床の hook の `hook_id`・guard の `turn`・取り合いの `contested`・到着による失効の `arrival`）、
    `message_id` は起床の hook の Stop が取った応答（guard と起床は None）、`sleeps` はその世代を取った hook が眠ると決めたか。
    """

    at: float
    by: str
    message_id: Optional[str] = None
    sleeps: bool = False


def keep_path(directory: Path, session_id: Any) -> Optional[Path]:
    """会話の起床の状態のファイル。会話の ID が UUID の形でなければ None（名前を作らない）。"""
    if not isinstance(session_id, str) or not SESSION_ID_RE.match(session_id):
        return None
    return directory / f"{KEEP_PREFIX}{session_id}.json"


def read_keep(directory: Path, session_id: Any) -> Optional[dict[str, Any]]:
    """会話の起床の状態。無い・読めない・会話の ID の形が違うなら None。"""
    path = keep_path(directory, session_id)
    if path is None:
        return None
    try:
        return _read(path)
    except (SignalError, OSError, UnicodeDecodeError):  # noqa: NDF401 ファイル: 読めない状態は無い状態として扱い、書き直す
        return None


def generation_of(keep: Optional[Mapping[str, Any]]) -> Optional[Generation]:
    """状態の世代。無い・形が違うなら None。"""
    raw = keep.get("generation") if keep else None
    if not isinstance(raw, dict) or not isinstance(raw.get("at"), (int, float)) or not isinstance(raw.get("by"), str):
        return None
    mid = raw.get("message_id")
    return Generation(float(raw["at"]), raw["by"], mid if isinstance(mid, str) else None, raw.get("sleeps") is True)


def with_generation(keep: Mapping[str, Any], gen: Generation) -> dict[str, Any]:
    """世代を書き換えた状態。"""
    return {**keep, "generation": gen._asdict()}


def update_keep(directory: Path, session_id: str, keep: Mapping[str, Any]) -> None:
    """会話の起床の状態を書く（子を止める排他の中で呼ぶ）。会話の ID の形が違えば `SignalError`。"""
    path = keep_path(directory, session_id)
    if path is None:
        raise SignalError(f"会話の ID の形が違う: {session_id!r}")
    replace_json(path, keep)


def append_cache_log(directory: Path, launch: str, session_id: str, kind: str, wait_id: Any, **fields: Any) -> None:
    """起床の記録へ 1 行を足す。どの行も `at`・`launch`・`session_id`・`claude_version`・`wait_id` を持つ。"""
    row = {"kind": kind, "launch": launch, "session_id": session_id, "claude_version": launch_version(directory), "wait_id": wait_id}
    append_jsonl(directory / CACHE_LOG, {**row, **fields})


def void_wait(keep: dict[str, Any], at: float) -> bool:
    """`at` で始まった、まだ起床させていない待ち（`k` 0・`pending` 無し）なら、待ちの項目を消して真。"""
    if keep.get("wait_id") != at or keep.get("k") != 0 or keep.get("pending"):
        return False
    for key in WAIT_KEYS:
        keep.pop(key, None)
    return True


def bump_generation(directory: Path, launch: str, session_id: str, gen: Generation, works: int = 0) -> tuple[str, dict[str, Any]]:
    """世代を新しい時刻でだけ書き換える（子を止める排他の中で呼ぶ）。返すのは結果と書いた後の状態。

    - `contested`: 同じ応答（`message_id`）を別の書き込み元が既に取っていた。世代の `by` を `contested` に替え、その応答で始まった
      起床させていない待ちを取り消す（`wait_void`）。替えたときだけ `contest` の行を書く
    - `older`: 世代が既に同じか新しい時刻。何も書かない
    - `taken`: 世代を書いた。作業が 1 件以上なら `work_stop` の行を書く。状態が無ければ世代だけの状態を作る
    """
    found = read_keep(directory, session_id)
    keep = {} if found is None else found
    now = generation_of(keep)
    if now is not None and gen.message_id is not None and now.message_id == gen.message_id and now.by != gen.by:
        if now.by != BY_CONTESTED:
            wait_id = keep.get("wait_id")
            voided = void_wait(keep, now.at)
            keep = with_generation(keep, now._replace(by=BY_CONTESTED, sleeps=False))
            update_keep(directory, session_id, keep)
            if voided:
                append_cache_log(directory, launch, session_id, "wait_void", wait_id, reason=BY_CONTESTED)
            append_cache_log(directory, launch, session_id, "contest", keep.get("wait_id"), message_id=gen.message_id, works=works)
        return "contested", keep
    if now is not None and now.at >= gen.at:
        return "older", keep
    keep = with_generation(keep, gen)
    update_keep(directory, session_id, keep)
    if works >= 1:
        append_cache_log(directory, launch, session_id, "work_stop", keep.get("wait_id"), message_id=gen.message_id)
    return "taken", keep


def revoke_generation(keep: dict[str, Any], started: float) -> bool:
    """生きた予約（`sleeps` 真で `by` が取り合い・失効でない世代）の `by` を `arrival` に替えたか。`at`・`message_id` は変えない。

    替えるのは、この hook が予約の応答より後に動いた（`started` が世代の `at` より後）ときだけ。書くのは呼び手。
    """
    now = generation_of(keep)
    if now is None or not now.sleeps or now.by in (BY_CONTESTED, BY_ARRIVAL) or started <= now.at:
        return False
    keep["generation"] = now._replace(by=BY_ARRIVAL)._asdict()
    return True


def drop_other_busy(directory: Path, launch: str) -> None:
    """ほかの起動の記録を消す。起動の ID の形でない名前のファイルには触れず、消せないものは残して続ける。"""
    for path in list(directory.glob(f"{BUSY_PREFIX}*.json")):
        other = path.stem.removeprefix(BUSY_PREFIX)
        if other != launch and LAUNCH_ID_RE.fullmatch(other):
            with suppress(OSError):  # noqa: NDF402 ファイル: 残っても起動ごとの名前なので読まれず、次の起動が消し直す
                drop(directory, path.name)
