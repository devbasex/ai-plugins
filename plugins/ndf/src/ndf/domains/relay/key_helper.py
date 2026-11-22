"""API キーの helper の払い出しの記録（#3604 #3864 の決定 12・I9。別冊の「キーの子の認証の失敗の停止」）。

- ラッパーはキーの子を起動する前に、中継の保存先（0700 の自分のディレクトリ）に記録 `key-helper.jsonl` を作って
  形式と helper の ID の見出しの 1 行を書き、helper の ID と記録の `(st_ino, st_dev)` を残す（`create`）
- helper（`ndf relay account api-key-helper`）は記録の錠の中で見出しを確かめ、払い出しの上限を照らし、1 行を足してから
  キーを出す（`pay`）。錠・追記（短い書き込みを含む）・記録の確かめの失敗は、記録と同じディレクトリの失敗のマーク
  `key-helper.fail` に 1 行を残し、同じ ID の失敗のマークの後はキーを出さない
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
from typing import Any, Callable, Mapping, NamedTuple, Optional, Union

from ndf.contracts import limits
from ndf.domains.relay import signals
from ndf.domains.relay.account_registry import GATED_ENV

RECORD_FILE = "key-helper.jsonl"
FAIL_FILE = "key-helper.fail"
POLICY = "relay.key-helper"
# 設定の当たりで出所を読めないとき（`cause: pin` の行・出し手を読めない失敗のマーク）の理由の 1 行の出所
SETTINGS_FILE = "設定ファイル"
FORMAT = "ndf-key-helper/1"  # 記録の見出しの行の形式（`create` が書き、`pay` と `watch` が helper の ID と合わせて確かめる）


class Record(NamedTuple):
    """キーの子の払い出しの記録。`ident` は作ったときの `(st_ino, st_dev)`。"""

    path: Path
    helper_id: str
    ident: tuple[int, int]


class Seen(NamedTuple):
    """見回り 1 回の読み。`auth` はキーが断られたか記録が数えられない、`source` は設定の当たりの出所（無ければ None）。

    `paid` は今の helper の ID の `out: true` の行があるか（最初の払い出しまで会話を子へ渡さない。別冊の「最初の払い出しまで送らない」）。
    """

    auth: bool
    source: Optional[str]
    paid: bool = False


def fingerprint(key: str) -> str:
    """キーの値の SHA-256 の 16 進（起動引数とファイルに値の代わりに載せる）。"""
    return hashlib.sha256(key.encode()).hexdigest()


def _own_dir(st: os.stat_result) -> bool:
    return stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid() and stat.S_IMODE(st.st_mode) == 0o700


def _own_file(st: os.stat_result) -> bool:
    """通常のファイルで、リンクの数が 1 で、自分の 0600 か（記録と失敗のマークに書き足す前・読む前の確かめ）。"""
    return stat.S_ISREG(st.st_mode) and st.st_nlink == 1 and st.st_uid == os.getuid() and stat.S_IMODE(st.st_mode) == 0o600


def _write_all(fd: int, data: bytes) -> None:
    """1 回の `os.write` で `data` の全バイトが書けたかを確かめる。短い書き込み（空き容量の不足など）は `OSError`。

    書き直さない（途中まで書いた行は JSON にならず、`watch` が記録の異常として拾う）。
    """
    if os.write(fd, data) != len(data):
        raise OSError(f"行の一部だけが書けた（{len(data)} バイト）")


def _header(helper_id: str) -> dict[str, str]:
    return {"format": FORMAT, "helper": helper_id}


def create(directory: Path) -> Record:
    """古い記録と失敗のマークを消し、見出しの 1 行だけの記録を作る。保存先が自分の 0700 のディレクトリでない・作れなければ `OSError`。"""
    if not _own_dir(os.lstat(directory)):
        raise PermissionError(f"中継の保存先が自分の 0700 のディレクトリでない: {directory}")
    path = directory / RECORD_FILE
    path.unlink(missing_ok=True)
    (directory / FAIL_FILE).unlink(missing_ok=True)
    helper_id = secrets.token_hex(16)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        st = os.fstat(fd)
        _write_all(fd, (json.dumps(_header(helper_id)) + "\n").encode())
    except OSError:
        os.close(fd)
        path.unlink(missing_ok=True)
        raise
    os.close(fd)
    return Record(path, helper_id, (st.st_ino, st.st_dev))


def _open_mark(path: Path) -> int:
    """失敗のマークを追記で開く。無ければ 0600 で作り、在れば通常のファイル・リンクの数 1・自分の 0600 を確かめる（外れれば `OSError`）。

    `O_NOFOLLOW` はハードリンクに効かないので、開いた後の `fstat` で確かめる（差し替えたマークから別のファイルへ書き足さない）。
    """
    flags = os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    try:
        fd = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
        made = True
    except FileExistsError:
        fd, made = os.open(path, flags), False
    try:
        if made:
            os.fchmod(fd, 0o600)  # umask で削られた分を戻す
        if not _own_file(os.fstat(fd)):
            raise PermissionError(f"失敗のマークが自分の 0600 の単一リンクの通常のファイルでない: {path}")
    except OSError:
        os.close(fd)
        raise
    return fd


def _fail(path: Path, helper_id: str, cause: str, by: str = "helper") -> None:
    """錠を取らずに失敗のマークへ 1 行を足す。作れない・確かめに外れる・書けなければ諦める（ラッパーは記録の異常で拾う）。"""
    line = json.dumps({"id": helper_id, "by": by, "cause": cause}) + "\n"
    try:
        fd = _open_mark(path.parent / FAIL_FILE)
        try:
            _write_all(fd, line.encode())
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
    """拒否のマーク・`gate` の行・`out` が偽の行が無く、直近の `seconds` 秒の `out` が真の行が `attempts` 未満なら真。

    `gate` の行（`config-gate` が拒んだ変更）の後は、設定が元に戻って固定の検証が通っても出さない（ラッパーが子を止める前の払い出しを防ぐ）。
    """
    mine = [r for r in rows if isinstance(r, dict) and r.get("id") == helper_id]
    if any(r.get("deny") is True or r.get("out") is False or "gate" in r for r in mine):
        return False
    recent = [r for r in mine if r.get("out") is True and isinstance(r.get("at"), (int, float)) and now - r["at"] < limits.seconds(POLICY)]
    return len(recent) < limits.value(POLICY, "attempts")


def _failed(directory: Path, helper_id: str) -> bool:
    """この helper の ID の失敗のマーク（錠を取れずに拒んだ呼び出しなど）が在るか。読めないマークと行の無いマークも在るとみなす。

    行の無いマークは、作った後の最初の書き込みが落ちた（空き容量の不足など）か、書いている途中の拒んだ呼び出し（r3-2）。
    """
    try:
        rows = _read(directory / FAIL_FILE, own=True)
    except FileNotFoundError:  # noqa: NDF401 ファイル: マークが無ければ拒んだ呼び出しは無い
        return False
    except (OSError, ValueError):  # noqa: NDF401 ファイル: 読めないマークは拒んだ後かもしれないので出さない
        return True
    return not rows or any(not isinstance(r, dict) or r.get("id") == helper_id for r in rows)


def _locked_record(fd: int, helper_id: str) -> Union[list[Any], str]:
    """開いた記録を確かめて錠を取り、行を返す。通常のファイルでない・リンクの数が 1 でない・自分の 0600 でない・この helper の ID の
    見出しが無いときは `record`、錠を取れなければ `lock`（失敗のマークの `cause`）。`pay` と `gate` が同じ確かめを通す。"""
    if not _own_file(os.fstat(fd)):
        return "record"
    if not signals.lock(fd, limits.value(POLICY, "lock")):
        return "lock"
    try:
        rows = _rows(_read_all(fd))
    except (OSError, ValueError):  # ファイル: 読めない記録には書き足さない（払い出しを数えられないので出さない）
        rows = []
    return rows if _is_record(rows, helper_id) else "record"


def pay(
    path: Path,
    helper_id: str,
    key: Optional[str],
    clock: Callable[[], float] = time.time,
    cause: Optional[str] = None,
    pinned: Optional[Callable[[], bool]] = None,
) -> bool:
    """記録の錠の中で払い出しを照らし、1 行を足す。キーを出してよければ真（出すのは呼び手で、錠を放した後）。

    `pinned` は確かめた設定への固定の検証で、錠を取った後に打つ（錠を待つ間に変わった設定で出さない）。偽なら `cause: pin`。
    `key` が None（指紋が違う・設定が無いなどで出さない）でも `out: false` の行を足す。同じ ID の失敗のマーク（錠を取れずに
    拒んだ呼び出し）が在れば、記録の行によらず `out: false` にする（拒んだ後に払い出さない）。失敗のマークは錠を取れなかった
    呼び出しが錠の外で書くので、`out: true` の行を足した後にもう一度読み、その間に現れていれば出さない（r3-1）。払い出しの
    確定はこの読み直しで、それより前に書かれたマークの後には出さず、後に書かれたマークの呼び出しは払い出しの後に拒んだことになる。
    照らす時刻と行の時刻は錠を取った後に
    `clock` で読む（錠を待った呼び出しが古い時刻で窓を早く抜けない）。記録を開けない・通常のファイルでない・リンクの数が 1 で
    ない・自分の 0600 でない・錠を取れない・読めない・この helper の ID の見出しが無い・追記が落ちるときは、行を足さずに失敗の
    マークを残して偽。
    """
    try:
        fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:  # ファイル: 開けない記録ではキーを出さず、失敗のマークを残す
        _fail(path, helper_id, "record")
        return False
    try:
        rows = _locked_record(fd, helper_id)
        if isinstance(rows, str):
            _fail(path, helper_id, rows)
            return False
        if cause is None and pinned is not None and not pinned():
            cause = "pin"
        now = clock()
        ok = cause is None and key is not None and _may_pay(rows, helper_id, now) and not _failed(path.parent, helper_id)
        row: dict[str, Any] = {"id": helper_id, "at": now, "out": ok, **({"cause": cause} if cause else {})}
        try:
            _write_all(fd, (json.dumps(row) + "\n").encode())
        except OSError:  # ファイル: 追記できない・途中で終われば払い出しを数えられないので出さない
            _fail(path, helper_id, "append")
            return False
        return ok and not _failed(path.parent, helper_id)
    finally:
        os.close(fd)


def deny(rec: Record, wait: Optional[float] = None) -> bool:
    """helper と同じ錠の中で拒否のマークを足し、書けたら真。錠は `wait` 秒（既定は `relay.key-helper` の `lock`）まで待つ。

    錠を取れない・書けなくても止めるのを先にする（例外を出さずに偽）。
    """
    try:
        fd = os.open(rec.path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:  # noqa: NDF401 ファイル: マークを書けなくても子は止める
        return False
    try:
        if not signals.lock(fd, limits.value(POLICY, "lock") if wait is None else wait):
            return False
        _write_all(fd, (json.dumps({"id": rec.helper_id, "deny": True}) + "\n").encode())
        return True
    except OSError:  # noqa: NDF401 ファイル: マークを書けなくても子は止める
        return False
    finally:
        os.close(fd)


def _read(path: Path, own: bool = False) -> list[Any]:
    """`O_NOFOLLOW | O_NONBLOCK` で開き、通常のファイル（`own` なら自分の 0600 の単一リンク）だけの行を読む。

    無ければ `FileNotFoundError`、開けない・通常のファイルでない・確かめに外れる・読めない・JSON でなければ `ValueError`
    （FIFO の open で止まらず、終端の無いデバイスを読み続けない）。
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise ValueError(str(exc)) from exc
    try:
        st = os.fstat(fd)
        if not (_own_file(st) if own else stat.S_ISREG(st.st_mode)):
            raise ValueError(f"自分の通常のファイルでない: {path}")
        return _rows(_read_all(fd))
    except OSError as exc:
        raise ValueError(str(exc)) from exc
    finally:
        os.close(fd)


def _record(directory: Path, rec: Record) -> Seen:
    try:
        st = os.lstat(rec.path)
        rows = _read(rec.path)
    except (OSError, ValueError):  # ファイル: 無い・読めない・JSON でない記録は払い出しを数えられない
        return Seen(True, None)
    if stat.S_ISLNK(st.st_mode) or (st.st_ino, st.st_dev) != rec.ident or st.st_nlink != 1:
        return Seen(True, None)
    if not _is_record(rows, rec.helper_id):
        return Seen(True, None)
    auth, source, paid = False, None, False
    for r in (r for r in rows[1:] if r.get("id") == rec.helper_id):
        paid = paid or r.get("out") is True
        if "gate" in r:
            source = str(r["gate"])
        elif r.get("out") is False and r.get("cause") == "pin":
            source = source or SETTINGS_FILE
        elif r.get("out") is False:
            auth = True
    return Seen(auth, source, paid)


def _marks(directory: Path, rec: Record) -> Seen:
    path = directory / FAIL_FILE
    try:
        if stat.S_ISLNK(os.lstat(path).st_mode):
            return Seen(False, SETTINGS_FILE)
        rows = _read(path, own=True)
    except FileNotFoundError:  # ファイル: マークが無ければ失敗は無い
        return Seen(False, None)
    except (OSError, ValueError):  # ファイル: 出し手を読めない・差し替えたマークは設定の当たりとして扱う
        return Seen(False, SETTINGS_FILE)
    if not rows:  # 行の無いマークは helper の最初の書き込みが落ちたもの（r3-2）。拒否を見逃さないよう記録の異常として扱う
        return Seen(True, None)
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
    return Seen(a.auth or b.auth, a.source or b.source, a.paid)


def stamp(directory: Path, rec: Record) -> tuple:
    """保存先・記録・失敗のマークの `lstat` のスタンプ（変わったときだけ `watch` で読み直す）。"""

    def one(p: Path) -> Optional[tuple]:
        try:
            st = os.lstat(p)
        except OSError:  # noqa: NDF401 ファイル: 無いこともスタンプの 1 つ
            return None
        return (st.st_ino, st.st_dev, st.st_mode, st.st_uid, st.st_nlink, st.st_size, st.st_mtime_ns)

    return (one(directory), one(rec.path), one(directory / FAIL_FILE))


def gate(rec: Record, source: str) -> None:
    """`config-gate` が拒んだ変更を、helper と同じ錠の中で記録へ `{"id", "gate": <source>}` として足す。

    追記の前に `pay` と同じ確かめ（通常のファイル・リンクの数・自分の 0600・helper の ID の見出し）を通し、外れれば書かない
    （ハードリンクや別の用途のファイルを壊さない）。外れた・錠を取れない・書けなければ失敗のマークへ `"by": "gate"` の行を足す
    （ラッパーは設定の当たりとして読む）。
    """
    try:
        fd = os.open(rec.path, os.O_RDWR | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:  # ファイル: 記録を開けなければ失敗のマークで知らせる
        _fail(rec.path, rec.helper_id, "record", by="gate")
        return
    try:
        rows = _locked_record(fd, rec.helper_id)
        if isinstance(rows, str):
            _fail(rec.path, rec.helper_id, rows, by="gate")
            return
        _write_all(fd, (json.dumps({"id": rec.helper_id, "gate": source}) + "\n").encode())
    except OSError:  # ファイル: 追記できなければ失敗のマークで知らせる
        _fail(rec.path, rec.helper_id, "append", by="gate")
    finally:
        os.close(fd)


# ---------------------------------------------------------------- 確かめた設定への固定（別冊の「確かめた設定への固定」）

# 比べる範囲の設定ファイルの項目（`env` の外。関所の有効性の判定に使う項目と helper）
GATED_ITEMS = ("apiKeyHelper", "disableAllHooks", "allowManagedHooksOnly")


def gate_view(data: Mapping[str, Any]) -> dict[str, Any]:
    """設定ファイルの object の比べる範囲（`env` の `GATED_ENV` の値と `GATED_ITEMS`）。`permissions`・`hooks`・`model` などは入らない。"""
    env = data.get("env")
    env = {k: v for k, v in env.items() if k in GATED_ENV} if isinstance(env, dict) else {}
    return {"env": env, **{k: data[k] for k in GATED_ITEMS if k in data}}


def view_sha(data: Mapping[str, Any]) -> str:
    """比べる範囲の SHA-256 の 16 進（`json.dumps(sort_keys=True)`）。"""
    return hashlib.sha256(json.dumps(gate_view(data), sort_keys=True).encode()).hexdigest()


def file_sha(path: Path) -> Optional[str]:
    """設定ファイルの比べる範囲の SHA-256。無いファイルは空の object と同じ。読めない・JSON の object でなければ None。

    確かめ側（`account_dir._layer`）と同じく symlink を辿る（dotfiles の管理で `~/.claude/settings.json` を symlink にする利用者がいる）。
    実パスに解いてから `O_NOFOLLOW` で開き、解いた後に差し替わった symlink は辿らない。
    実パスの先が FIFO やデバイスなら、`O_NONBLOCK` で開いて読む前に外す（open で止まらず、終端の無いデバイスを読み続けない）。
    """
    try:
        fd = os.open(os.path.realpath(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except FileNotFoundError:  # ファイル: 無い設定ファイルは空の object と同じ（Claude Code も読まない）
        return view_sha({})
    except OSError:  # noqa: NDF401 ファイル: 読めない設定ファイルは照らせない（呼び手が出さない・拒む）
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        data = json.loads(_read_all(fd).decode("utf-8"))
    except (OSError, ValueError):  # noqa: NDF401 ファイル: 解けない設定ファイルは照らせない
        return None
    finally:
        os.close(fd)
    return view_sha(data) if isinstance(data, dict) else None


def listing(directory: Path) -> Optional[list[str]]:
    """`managed-settings.d` の読まれるファイルの名前（`.json` で隠しでない。名前の順）。無ければ None、列挙できなければ `OSError`。"""
    try:
        names = os.listdir(directory)
    except (FileNotFoundError, NotADirectoryError):  # noqa: NDF401 ファイル: 無い管理の設定の場所は一覧を持たない
        return None
    return sorted(n for n in names if n.endswith(".json") and not n.startswith("."))


def write_pin(path: Path, files: Mapping[str, Optional[str]], dirs: Mapping[str, Optional[list[str]]]) -> None:
    """固定のファイルを `O_EXCL | O_NOFOLLOW`・0600 で書く。`files` はパス → 比べる範囲の SHA-256、`dirs` は名前の一覧を記録する場所。"""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        _write_all(fd, json.dumps({"format": FORMAT, "files": dict(files), "dirs": dict(dirs)}).encode())
    except OSError:
        os.close(fd)
        path.unlink(missing_ok=True)
        raise
    os.close(fd)


def read_pin(path: Path) -> Optional[dict[str, Any]]:
    """固定のファイルを `O_NOFOLLOW` で開き、通常のファイル・所有が自分・0600 を確かめて読む。外れれば None。"""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except OSError:  # noqa: NDF401 ファイル: 開けない固定のファイルでは出さない
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o600:
            return None
        data = json.loads(_read_all(fd).decode("utf-8"))
    except (OSError, ValueError):  # noqa: NDF401 ファイル: 読めない固定のファイルでは出さない
        return None
    finally:
        os.close(fd)
    ok = isinstance(data, dict) and data.get("format") == FORMAT
    return data if ok and isinstance(data.get("files"), dict) and isinstance(data.get("dirs"), dict) else None


def pin_ok(path: Path) -> bool:
    """記録した各パスの今の比べる範囲の SHA-256 と `managed-settings.d` の名前の一覧が、固定のファイルと同じなら真。"""
    pin = read_pin(path)
    if pin is None:
        return False
    try:
        dirs = all(listing(Path(d)) == names for d, names in pin["dirs"].items())
    except OSError:  # noqa: NDF401 ファイル: 列挙できない管理の設定は照らせない
        return False
    return dirs and all(want is not None and file_sha(Path(p)) == want for p, want in pin["files"].items())


def pin_has(path: Path, file_path: str) -> Optional[str]:
    """固定のファイルが `file_path`（実パスで比べる）を記録していれば、その SHA-256。無い・読めなければ None。"""
    pin = read_pin(path)
    if pin is None:
        return None
    real = os.path.realpath(file_path)
    return next((v for p, v in pin["files"].items() if os.path.realpath(p) == real and v is not None), None)
