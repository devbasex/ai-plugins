"""API キーの helper の払い出しの記録（#3604 #3864 の決定 12・I9。別冊の「キーの子の認証の失敗の停止」）。

- ラッパーはキーの子を起動する前に、中継の保存先（0700 の自分のディレクトリ）に記録 `key-helper.jsonl` を作って
  形式と helper の ID の見出しの 1 行を書き、helper の ID と記録の `(st_ino, st_dev)` を残す（`create`）
- helper（`ndf relay account api-key-helper`）は記録の錠の中で見出しを確かめ、払い出しの上限を照らし、1 行を足してから
  キーを出す（`pay`）。錠・追記・記録の確かめの失敗は、記録と同じディレクトリの失敗のマーク `key-helper.fail` に 1 行を残す
- 記録と失敗のマークはどれも待たずに開き（FIFO で止まらない）、読み書きの前に開いた FD が自分の 0600 の単一リンクの
  通常ファイルかを確かめる（差し替えた先やハードリンクの先の利用者のファイルを読み書きしない）
- ラッパーは見回りごとに記録と失敗のマークを読み、今の helper の ID の拒み・記録の異常・設定の当たりを返す（`watch`）。
  401 を確かめたら、止める前に記録へ拒否のマークを足す（`deny`）
行にはキーの値も指紋も書かない。helper から速く起動できるよう、重い部品を読み込まない。
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
import time
from pathlib import Path
from typing import Any, Callable, NamedTuple, Optional

from ndf.contracts import limits
from ndf.domains.relay import signals

RECORD_FILE = "key-helper.jsonl"
FAIL_FILE = "key-helper.fail"
POLICY = "relay.key-helper"
# 設定の当たりで出所を読めないとき（`cause: pin` の行・出し手を読めない失敗のマーク）の理由の 1 行の出所
SETTINGS_FILE = "設定ファイル"
FORMAT = "ndf-key-helper/1"  # 記録の見出しの行の形式（`create` が書き、`pay` と `watch` が helper の ID と合わせて確かめる）
_OPEN = os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


class Record(NamedTuple):
    """キーの子の払い出しの記録。`ident` は作ったときの `(st_ino, st_dev)`。"""

    path: Path
    helper_id: str
    ident: tuple[int, int]


class Seen(NamedTuple):
    """見回り 1 回の読み。`auth` はキーが断られたか記録が数えられない、`source` は設定の当たりの出所（無ければ None）。"""

    auth: bool
    source: Optional[str]


def fingerprint(key: str) -> str:
    """キーの値の SHA-256 の 16 進（起動引数とファイルに値の代わりに載せる）。"""
    return hashlib.sha256(key.encode()).hexdigest()


def _own_dir(st: os.stat_result) -> bool:
    return stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid() and stat.S_IMODE(st.st_mode) == 0o700


def _own_file(st: os.stat_result) -> bool:
    return stat.S_ISREG(st.st_mode) and st.st_nlink == 1 and st.st_uid == os.getuid() and stat.S_IMODE(st.st_mode) == 0o600


def _open_own(path: Path, flags: int, ident: Optional[tuple[int, int]] = None) -> int:
    """symlink をたどらず待たずに開き、開いた FD が自分の 0600 の単一リンクの通常ファイル（`ident` を渡せばその inode）なら返す。

    違えば閉じて `PermissionError`。開けなければ `OSError`。
    """
    fd = os.open(path, flags | _OPEN, 0o600)
    try:
        st = os.fstat(fd)
    except OSError:
        os.close(fd)
        raise
    if not _own_file(st) or (ident is not None and (st.st_ino, st.st_dev) != ident):
        os.close(fd)
        raise PermissionError(f"自分の 0600 の単一リンクの通常ファイルでない: {path}")
    return fd


def secure_dir(directory: Path) -> None:
    """保存先を symlink をたどらずに開き、自分のディレクトリなら 0700 にそろえる（違うときだけ変える）。自分のでなければ `PermissionError`。

    ラッパーはどの起動経路でも、錠を取る・状態を書く前にこれを打つ（旧版の 0755 の保存先の質問や `wrapper.json` を他の利用者に読ませない）。
    """
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        st = os.fstat(fd)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
            raise PermissionError(f"中継の保存先が自分のディレクトリでない: {directory}")
        if stat.S_IMODE(st.st_mode) != 0o700:
            os.fchmod(fd, 0o700)
    finally:
        os.close(fd)


def _header(helper_id: str) -> dict[str, str]:
    return {"format": FORMAT, "helper": helper_id}


def create(directory: Path) -> Record:
    """古い記録と失敗のマークを消し、見出しの 1 行だけの記録を作る。保存先が自分の 0700 のディレクトリにできない・作れなければ `OSError`。"""
    secure_dir(directory)
    if not _own_dir(os.lstat(directory)):
        raise PermissionError(f"中継の保存先が自分の 0700 のディレクトリでない: {directory}")
    path = directory / RECORD_FILE
    path.unlink(missing_ok=True)
    (directory / FAIL_FILE).unlink(missing_ok=True)
    helper_id = secrets.token_hex(16)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        st = os.fstat(fd)
        os.write(fd, (json.dumps(_header(helper_id)) + "\n").encode())
    except OSError:
        os.close(fd)
        path.unlink(missing_ok=True)
        raise
    os.close(fd)
    return Record(path, helper_id, (st.st_ino, st.st_dev))


def _fail(path: Path, helper_id: str, cause: str) -> None:
    """錠を取らずに失敗のマークへ 1 行を足す。作れない・自分の 0600 の単一リンクの通常ファイルでない・書けなければ諦める
    （ラッパーは記録の異常か、読めないマークとして拾う）。"""
    line = json.dumps({"id": helper_id, "by": "helper", "cause": cause}) + "\n"
    try:
        fd = _open_own(path.parent / FAIL_FILE, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, line.encode())
        finally:
            os.close(fd)
    except OSError:  # noqa: NDF401 ファイル: マークを作れなくてもキーは出さない（保存先の異常はラッパーが記録の異常として拾う）
        return


def _rows(data: bytes) -> list[Any]:
    return [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]


def _is_record(rows: list[Any], helper_id: str) -> bool:
    """最初の行がこの helper の ID の見出しで、どの行も辞書なら真（空のファイルや別の用途の JSONL を記録とみなさない）。"""
    return bool(rows) and rows[0] == _header(helper_id) and all(isinstance(r, dict) for r in rows)


def _read_all(fd: int) -> bytes:
    chunks, offset = [], 0
    while chunk := os.pread(fd, 65536, offset):
        chunks.append(chunk)
        offset += len(chunk)
    return b"".join(chunks)


def _may_pay(rows: list[Any], helper_id: str, now: float) -> bool:
    """拒否のマークか `out` が偽の行が無く、直近の `seconds` 秒の `out` が真の行が `attempts` 未満なら真。"""
    mine = [r for r in rows if isinstance(r, dict) and r.get("id") == helper_id]
    if any(r.get("deny") is True or r.get("out") is False for r in mine):
        return False
    recent = [r for r in mine if r.get("out") is True and isinstance(r.get("at"), (int, float)) and now - r["at"] < limits.seconds(POLICY)]
    return len(recent) < limits.value(POLICY, "attempts")


def pay(path: Path, helper_id: str, key: Optional[str], clock: Callable[[], float] = time.time) -> bool:
    """記録の錠の中で払い出しを照らし、1 行を足す。キーを出してよければ真（出すのは呼び手で、錠を放した後）。

    `key` が None（指紋が違う・設定が無いなどで出さない）でも `out: false` の行を足す。照らす時刻と行の時刻は錠を取った後に
    `clock` で読む（錠を待った呼び出しが古い時刻で窓を早く抜けない）。記録を開けない・自分の 0600 の単一リンクの通常ファイルで
    ない・錠を取れない・読めない・この helper の ID の見出しが無い・追記が落ちるときは、行を足さずに失敗のマークを残して偽。
    """
    try:
        fd = _open_own(path, os.O_RDWR | os.O_APPEND)
    except OSError:  # ファイル: 開けない・確かめの外れた記録ではキーを出さず、失敗のマークを残す
        _fail(path, helper_id, "record")
        return False
    try:
        if not signals.lock(fd, limits.value(POLICY, "lock")):
            _fail(path, helper_id, "lock")
            return False
        try:
            rows = _rows(_read_all(fd))
        except (OSError, ValueError):  # ファイル: 読めない記録には書き足さない（払い出しを数えられないので出さない）
            rows = []
        if not _is_record(rows, helper_id):
            _fail(path, helper_id, "record")
            return False
        now = clock()
        ok = key is not None and _may_pay(rows, helper_id, now)
        try:
            os.write(fd, (json.dumps({"id": helper_id, "at": now, "out": ok}) + "\n").encode())
        except OSError:  # ファイル: 追記できなければ払い出しを数えられないので出さない
            _fail(path, helper_id, "append")
            return False
        return ok
    finally:
        os.close(fd)


def deny(rec: Record, wait: Optional[float] = None) -> None:
    """helper と同じ錠の中で拒否のマークを足す。錠は `wait` 秒（既定は `relay.key-helper` の `lock`）まで待つ。

    開いた FD が作ったときの記録（同じ inode の自分の 0600 の単一リンクの通常ファイル）でなければ書かない。
    錠を取れない・書けなくても止めるのを先にする（例外を出さない）。
    """
    try:
        fd = _open_own(rec.path, os.O_WRONLY | os.O_APPEND, rec.ident)
    except OSError:  # noqa: NDF401 ファイル: マークを書けなくても子は止める
        return
    try:
        if signals.lock(fd, limits.value(POLICY, "lock") if wait is None else wait):
            os.write(fd, (json.dumps({"id": rec.helper_id, "deny": True}) + "\n").encode())
    except OSError:  # noqa: NDF401 ファイル: マークを書けなくても子は止める
        return
    finally:
        os.close(fd)


def _read(path: Path, ident: Optional[tuple[int, int]] = None) -> list[Any]:
    """`_open_own` で開いて行を読む。無ければ `FileNotFoundError`、開けない・確かめが外れる・読めない・JSON でなければ `ValueError`。"""
    try:
        fd = _open_own(path, os.O_RDONLY, ident)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise ValueError(str(exc)) from exc
    try:
        return _rows(_read_all(fd))
    except OSError as exc:
        raise ValueError(str(exc)) from exc
    finally:
        os.close(fd)


def _record(directory: Path, rec: Record) -> Seen:
    try:
        rows = _read(rec.path, rec.ident)
    except (OSError, ValueError):  # ファイル: 無い・差し替えた・読めない・JSON でない記録は払い出しを数えられない
        return Seen(True, None)
    if not _is_record(rows, rec.helper_id):
        return Seen(True, None)
    auth, source = False, None
    for r in (r for r in rows[1:] if r.get("id") == rec.helper_id):
        if "gate" in r:
            source = str(r["gate"])
        elif r.get("out") is False and r.get("cause") == "pin":
            source = source or SETTINGS_FILE
        elif r.get("out") is False:
            auth = True
    return Seen(auth, source)


def _marks(directory: Path, rec: Record) -> Seen:
    path = directory / FAIL_FILE
    try:
        rows = _read(path)
    except FileNotFoundError:  # ファイル: マークが無ければ失敗は無い
        return Seen(False, None)
    except (OSError, ValueError):  # ファイル: 出し手を読めないマークは設定の当たりとして扱う
        return Seen(False, SETTINGS_FILE)
    auth, source = False, None
    for r in rows:
        if not isinstance(r, dict) or "by" not in r:
            source = SETTINGS_FILE
        elif r.get("id") == rec.helper_id and r["by"] == "helper":
            auth = True
        elif r.get("id") == rec.helper_id:
            source = SETTINGS_FILE
    return Seen(auth, source)


def watch(directory: Path, rec: Record) -> Seen:
    """今の helper の ID の拒み（`out: false`）・記録と保存先の異常・helper の失敗のマークを `auth`、設定の当たりを `source` で返す。"""
    try:
        if not _own_dir(os.lstat(directory)):
            return Seen(True, None)
    except OSError:  # ファイル: 消えた保存先は記録の異常
        return Seen(True, None)
    a, b = _record(directory, rec), _marks(directory, rec)
    return Seen(a.auth or b.auth, a.source or b.source)


def stamp(directory: Path, rec: Record) -> tuple:
    """保存先・記録・失敗のマークの `lstat` のスタンプ（変わったときだけ `watch` で読み直す）。"""

    def one(p: Path) -> Optional[tuple]:
        try:
            st = os.lstat(p)
        except OSError:  # noqa: NDF401 ファイル: 無いこともスタンプの 1 つ
            return None
        return (st.st_ino, st.st_dev, st.st_mode, st.st_uid, st.st_nlink, st.st_size, st.st_mtime_ns)

    return (one(directory), one(rec.path), one(directory / FAIL_FILE))
