"""登録済みアカウントの認証ファイルと保留の更新の読み書き（`account_dir` の部品。#3604 #3864）。

- `pin_dir` で設定ディレクトリを記述子に固定し、以後はその記述子からの相対で開く・置き換える・消す（名前を辿らない）
- 開いたファイルは `fstat` で通常のファイル・リンク数 1・`check_local` が確かめた `(st_dev, st_ino)` と確かめてから読む
- 認証ファイルを書くのは `replace_auth` だけ。値は返り・例外の文に入れない（I5）

詳細設計の関数の表は `account_dir.py` に置くが、ファイルの行数の上限（1,000 行）のため分けた。`account_dir` が同じ名前で引く。
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import secrets
import stat
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any, Iterator, Optional, Protocol, Union

from ndf.domains.relay import account_registry as reg

NOT_LOCAL = "not_account_local"
REFRESH_LOCK = ".oauth_refresh.lock"  # claude 本体と同じ更新の排他（ディレクトリ）
REFRESH_OWNER = REFRESH_LOCK + ".ndf-owner"  # NDF が作った排他のオーナー（排他の中に置くと claude が rmdir で消せない）
_STAGE = ".ndf-new."  # 排他の名前へ公開する前の候補（`REFRESH_LOCK` の後ろに付ける）
# 認証の値を持つ一時ファイル。`.credentials.json` の前方一致なのでアカウント固有（symlink を張らない）
_AUTH_TMP = reg.CRED_FILE + ".ndf-{what}.{tok}.tmp"


class Fixed(Protocol):
    """`account_dir.Local` の形（確かめた実体）。"""

    account: Path
    ids: dict[str, Optional[tuple[int, int]]]


def pin_dir(local: Fixed) -> Union[int, str]:
    """設定ディレクトリを `O_NOFOLLOW` で開き、確かめた実体と同じならその記述子を、違えば `not_account_local` を返す。"""
    try:
        d = os.open(local.account, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return NOT_LOCAL
    st = os.fstat(d)
    if (st.st_dev, st.st_ino) != local.ids.get("dir"):
        os.close(d)
        return NOT_LOCAL
    return d


def _open_fixed(d: int, name: str, want: Optional[tuple[int, int]]) -> Optional[tuple[int, os.stat_result]]:
    """記述子 `d` の下の `name` を開き、通常のファイル・リンク数 1・`want` と同じ実体なら (記述子, fstat)、違えば None。"""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=d)
    except OSError:  # noqa: NDF401 ファイル: 無い・symlink・開けないファイルは確かめた実体でない（呼び手が理由にする）
        return None
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or (st.st_dev, st.st_ino) != want:
        os.close(fd)
        return None
    return fd, st


def _load(fd: int) -> Any:
    with os.fdopen(fd, "rb") as f:
        try:
            return json.loads(f.read().decode("utf-8"))
        except (OSError, ValueError):  # noqa: NDF401 ファイル: 壊れた中身は呼び手が理由にする
            return None


def read_auth(local: Fixed) -> Union[tuple[dict[str, Any], os.stat_result], str]:
    """認証ファイルを読む唯一の手順。(中身, 読んだ記述子の fstat) か理由の符号（`not_account_local`・`no_credentials`）。"""
    d = pin_dir(local)
    if isinstance(d, str):
        return d
    try:
        got = _open_fixed(d, reg.CRED_FILE, local.ids.get(reg.CRED_FILE))
        if got is None:
            return NOT_LOCAL
        body = _load(got[0])
    finally:
        os.close(d)
    return (body, got[1]) if isinstance(body, dict) else "no_credentials"


def _put(d: int, what: str, data: dict[str, Any], durable: bool = True) -> str:
    """記述子 `d` の下に一時ファイル（0600。`O_EXCL | O_NOFOLLOW`）を作って書き、`durable` なら同期する。一時の名前を返す。"""
    tmp = _AUTH_TMP.format(what=what, tok=secrets.token_hex(8))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
            f.flush()
            if durable:
                os.fsync(f.fileno())
    except OSError:
        with suppress(OSError):  # noqa: NDF402 ファイル: 書けなかった一時ファイルの後始末。元の失敗を上げる
            os.unlink(tmp, dir_fd=d)
        raise
    return tmp


def _sig(st: os.stat_result) -> tuple[int, int, int, int]:
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


def replace_auth(local: Fixed, seen: os.stat_result, oauth: dict[str, Any], body: dict[str, Any]) -> bool:
    """`body` の `claudeAiOauth` だけを `oauth` に替えて置き換える。認証ファイルが `seen` のままのときだけ書いて真。

    呼ぶ側が登録の排他を持つ。比べて違えば（読んだ後に誰かが書いた）書かずに偽。書けないときも偽。"""
    d = pin_dir(local)
    if isinstance(d, str):
        return False
    try:
        tmp = _put(d, "auth", {**body, "claudeAiOauth": oauth})
        if _sig(os.stat(reg.CRED_FILE, dir_fd=d, follow_symlinks=False)) != _sig(seen):
            os.unlink(tmp, dir_fd=d)
            return False
        os.replace(tmp, reg.CRED_FILE, src_dir_fd=d, dst_dir_fd=d)
        os.fsync(d)
        return True
    except OSError:  # noqa: NDF401 ファイル: 書けなければ書いた側の内容を残す（呼び手は偽で続ける）
        return False
    finally:
        os.close(d)


def pair_hash(access: str, refresh: str) -> str:
    """アクセストークンとリフレッシュトークンの組の SHA-256（保留の更新の `base`。値を残さない）。"""
    return hashlib.sha256(f"{access}\0{refresh}".encode()).hexdigest()


def _pair_of(body: Any) -> str:
    oauth = body.get("claudeAiOauth") if isinstance(body, dict) else None
    if not isinstance(oauth, dict):
        return ""
    return pair_hash(str(oauth.get("accessToken") or ""), str(oauth.get("refreshToken") or ""))  # noqa: NDF501 ファイル: 欠けたトークンは空の組として比べる


def write_pending(local: Fixed, generation: str, base: tuple[str, str], oauth: dict[str, Any]) -> bool:
    """認証ファイルへ当てられなかった更新の成功の応答を `.ndf-oauth-pending.json` に書いて同期し、書けたら真。

    固定に外れた（設定ディレクトリが `local` の実体でない）・書けない（容量・I/O）ときは偽。"""
    d = pin_dir(local)
    if isinstance(d, str):
        return False
    try:
        tmp = _put(d, "pending", {"generation": generation, "base": pair_hash(*base), "claudeAiOauth": oauth})
        os.replace(tmp, reg.PENDING_FILE, src_dir_fd=d, dst_dir_fd=d)
        os.fsync(d)
        return True
    except OSError:  # noqa: NDF401 ファイル: 書けなかった保留は呼び手が `unsaved` として知らせる
        return False
    finally:
        os.close(d)


def _ident(d: int, name: str) -> Optional[tuple[int, int]]:
    """記述子 `d` の下の `name` がディレクトリなら `(st_dev, st_ino)`、無い・ディレクトリでなければ None（symlink を辿らない）。"""
    try:
        st = os.stat(name, dir_fd=d, follow_symlinks=False)
    except OSError:  # noqa: NDF401 ファイル: 無い排他は実体を持たない
        return None
    return (st.st_dev, st.st_ino) if stat.S_ISDIR(st.st_mode) else None


def _owner_name(ident: tuple[int, int]) -> str:
    """排他の実体 `ident` のオーナーの記録の名前（実体ごとに 1 つ。別の実体の記録を書き換えない）。"""
    return f"{REFRESH_OWNER}.{ident[0]}-{ident[1]}"


def _read_owner(d: int, ident: tuple[int, int]) -> Optional[dict[str, Any]]:
    """NDF が作った排他 `ident` のオーナー（`pid`・`start`）。記録が無い・形が違えば None（claude の排他かオーナーが分からない）。"""
    try:
        fd = os.open(_owner_name(ident), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=d)
    except OSError:  # noqa: NDF401 ファイル: 記録の無い排他は claude のものかオーナーが分からないもの
        return None
    row = _load(fd)
    who = row.get("who") if isinstance(row, dict) else None
    if not (isinstance(who, dict) and isinstance(who.get("pid"), int) and isinstance(who.get("start"), (int, float))):
        return None
    return who


# 名前を空けずに置く・入れ替える rename（`renameat2` の `RENAME_NOREPLACE`・`RENAME_EXCHANGE`、macOS は `renameatx_np`）
_NOREPLACE, _EXCHANGE = "noreplace", "exchange"
_FLAGS = {"renameat2": {_NOREPLACE: 1, _EXCHANGE: 2}, "renameatx_np": {_NOREPLACE: 4, _EXCHANGE: 2}}


def _rename2(d: int, src: str, dst: str, how: str) -> None:
    """記述子 `d` の下で `src` を `dst` へ、上書きせずに移す（`noreplace`）か、2 つを入れ替える（`exchange`）。

    どちらも 1 回のシステムコールで、`dst` の名前が空く瞬間が無い。使えない環境では `OSError` を上げる（排他を取らない）。"""
    libc = ctypes.CDLL(None, use_errno=True)
    for fn, flags in _FLAGS.items():
        call = getattr(libc, fn, None)
        if call is None:
            continue
        call.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
        if call(d, os.fsencode(src), d, os.fsencode(dst), flags[how]) != 0:
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err), dst)
        return
    raise OSError(errno.ENOSYS, "renameat2", dst)


def _stage(d: int) -> Optional[tuple[str, tuple[int, int]]]:
    """排他の候補（空のディレクトリ）を別名で作り、そのオーナーの記録を先に置く。(別名, 実体) か、置けなければ None。

    排他の名前に現れる時点でオーナーが分かる（公開の前に記録がある）。記録は公開のためのもので同期しない。
    電源断で記録が失われた排他は NDF が回収しないが、claude が自分の残骸の規則（60 秒）で退ける。"""
    start = reg.proc_start(os.getpid())
    if start is None:
        return None
    name = f"{REFRESH_LOCK}{_STAGE}{secrets.token_hex(8)}"
    try:
        os.mkdir(name, 0o700, dir_fd=d)
    except OSError:  # noqa: NDF401 ファイル: 作れない排他は取れないとして流す
        return None
    ident = _ident(d, name)
    if ident is None:
        return None
    try:
        tmp = _put(d, "owner", {"who": {"pid": os.getpid(), "start": start}}, durable=False)
        os.replace(tmp, _owner_name(ident), src_dir_fd=d, dst_dir_fd=d)
    except OSError:  # オーナーを記録できない排他は公開しない
        _drop(d, name, ident)
        return None
    return name, ident


def _drop(d: int, name: str, ident: tuple[int, int]) -> None:
    """公開しなかった候補 `name` が `ident` のままなら消し、`ident` のオーナーの記録を消す。"""
    if _ident(d, name) == ident:
        with suppress(OSError):  # noqa: NDF402 ファイル: 消せない候補は排他の名前でないので働かない
            os.rmdir(name, dir_fd=d)
    with suppress(OSError):  # noqa: NDF402 ファイル: 消えた記録はそのまま
        os.unlink(_owner_name(ident), dir_fd=d)


def _sweep(d: int) -> None:
    """取得の途中で強制終了した残り（公開しなかった候補・実体の無いオーナーの記録）を、オーナーの終わりを確かめて消す。

    NDF の取り手は登録の排他で 1 本ずつなので、ここで消すのは終わったオーナーのものだけで足りる。"""
    lock = _ident(d, REFRESH_LOCK)
    kept = _owner_name(lock) if lock is not None else None
    for name in os.listdir(d):
        if name.startswith(REFRESH_LOCK + _STAGE):
            _sweep_stage(d, name)
        elif name.startswith(REFRESH_OWNER + ".") and name != kept and _ended(_read_owner_file(d, name)):
            with suppress(OSError):  # noqa: NDF402 ファイル: 消えた記録はそのまま
                os.unlink(name, dir_fd=d)


def _ended(who: Optional[dict[str, Any]]) -> bool:
    """記録の無い・壊れたオーナーか、終わったと確かめたオーナー。"""
    return who is None or reg.exited(who)


def _sweep_stage(d: int, name: str) -> None:
    ident = _ident(d, name)
    if ident is not None and _ended(_read_owner(d, ident)):
        _drop(d, name, ident)


def _read_owner_file(d: int, name: str) -> Optional[dict[str, Any]]:
    parts = name[len(REFRESH_OWNER) + 1 :].split("-")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return None
    return _read_owner(d, (int(parts[0]), int(parts[1])))


def _reclaim(d: int, name: str) -> bool:
    """オーナーが終わったと確かめた NDF の排他の残骸を、候補 `name` との入れ替えで退けて真。claude の排他・生きている排他は偽。

    入れ替えは 1 回のシステムコールで、排他の名前は空かない（その間に別のプロセスが取れない）。入れ替えた後に退けたものが
    確かめた残骸でなければ（確かめた後に claude が取り直した）、もう一度入れ替えて戻す。"""
    old = _ident(d, REFRESH_LOCK)
    who = _read_owner(d, old) if old is not None else None
    if who is None or not reg.exited(who):
        return False
    try:
        _rename2(d, name, REFRESH_LOCK, _EXCHANGE)
    except OSError:  # noqa: NDF401 ファイル: 入れ替えられなければ回収せず、取れないとして流す
        return False
    if _ident(d, name) != old:
        with suppress(OSError):  # noqa: NDF402 ファイル: 戻せなければ、取り直した側が放した後と同じになる
            _rename2(d, name, REFRESH_LOCK, _EXCHANGE)
        return False
    with suppress(OSError):  # noqa: NDF402 ファイル: 消せない残骸の別名は排他として働かない
        os.rmdir(name, dir_fd=d)
    with suppress(OSError):  # noqa: NDF402 ファイル: 消えた記録はそのまま
        os.unlink(_owner_name(old), dir_fd=d)
    return True


def _take(d: int) -> Optional[tuple[int, int]]:
    """記述子 `d` の下に排他を取り、取った実体を返す。持たれていれば残骸の回収を 1 度だけ試し、取れなければ None。

    候補を上書きしない rename で排他の名前へ公開するので、公開した時点でオーナーの記録がある。"""
    _sweep(d)
    staged = _stage(d)
    if staged is None:
        return None
    name, ident = staged
    if _publish(d, name):
        return ident
    _drop(d, name, ident)
    return None


def _publish(d: int, name: str) -> bool:
    """候補 `name` を排他の名前へ上書きせずに移す。持たれていれば残骸の回収（入れ替え）を試す。排他の名前に入れば真。"""
    try:
        _rename2(d, name, REFRESH_LOCK, _NOREPLACE)
    except FileExistsError:
        return _reclaim(d, name)  # 真なら入れ替えで候補が排他の名前に入った
    except OSError:  # noqa: NDF401 ファイル: 公開できない排他は取れないとして流す
        return False
    return True


def _release(d: int, ident: tuple[int, int]) -> None:
    """自分が作った実体のときだけ排他を消し、その実体のオーナーの記録を消す。"""
    if _ident(d, REFRESH_LOCK) == ident:
        with suppress(OSError):  # noqa: NDF402 ファイル: 消えた排他はそのまま
            os.rmdir(REFRESH_LOCK, dir_fd=d)
    with suppress(OSError):  # noqa: NDF402 ファイル: 消えた記録はそのまま
        os.unlink(_owner_name(ident), dir_fd=d)


@contextmanager
def refresh_lock(local: Fixed) -> Iterator[bool]:
    """claude と同じ更新の排他を待たずに取る。取れなければ偽を流し、オーナーの生きている排他・claude の排他を横取りしない。

    確かめた設定ディレクトリの記述子からの相対で作り・消すので、持つ間にディレクトリが移されても別のディレクトリの排他に触れない。
    NDF が作った排他はオーナーを実体ごとの記録（`_owner_name`）に公開の前に置き、強制終了の残骸はオーナーの終わりを
    確かめてから入れ替えで回収する。"""
    d = pin_dir(local)
    if isinstance(d, str):
        yield False
        return
    try:
        ident = _take(d)
        if ident is None:
            yield False  # 持たれている（か作れない）排他は取れないとして流す
            return
        try:
            yield True
        finally:
            _release(d, ident)
    finally:
        os.close(d)


def apply_pending(generation: str, local: Fixed) -> str:
    """保留の更新を当てる。`written`（書いた）・`none`（無い・捨てた）・`kept`（残した）のどれか。呼ぶ側が登録の排他を持つ。"""
    if local.ids.get(reg.PENDING_FILE) is None:
        return "none"
    with refresh_lock(local) as got:
        if not got:
            return "kept"
        d = pin_dir(local)
        if isinstance(d, str):
            return "kept"
        try:
            return _apply(d, generation, local)
        finally:
            os.close(d)


def _apply(d: int, generation: str, local: Fixed) -> str:
    opened = _open_fixed(d, reg.PENDING_FILE, local.ids.get(reg.PENDING_FILE))
    if opened is None:
        return "kept"
    pending, pst = _load(opened[0]), opened[1]
    pending = pending if isinstance(pending, dict) else {}
    auth = read_auth(local)
    if isinstance(auth, str):
        return "kept"
    body, st = auth
    oauth = pending.get("claudeAiOauth")
    if pending.get("generation") == generation and pending.get("base") == _pair_of(body) and isinstance(oauth, dict):
        if not replace_auth(local, st, oauth, body):
            return "kept"
        out = "written"
    else:
        out = "none"  # 再登録・利用者のログイン・claude の更新が後なので捨てる
    with suppress(OSError):  # noqa: NDF402 ファイル: 消えた保留はそのまま
        now = os.stat(reg.PENDING_FILE, dir_fd=d, follow_symlinks=False)
        if (now.st_dev, now.st_ino) == (pst.st_dev, pst.st_ino):
            os.unlink(reg.PENDING_FILE, dir_fd=d)
    return out
