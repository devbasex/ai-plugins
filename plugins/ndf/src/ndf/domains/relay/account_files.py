"""登録済みアカウントの認証ファイルと保留の更新の読み書き（`account_dir` の部品。#3604 #3864）。

- `pin_dir` で設定ディレクトリを記述子に固定し、以後はその記述子からの相対で開く・置き換える・消す（名前を辿らない）
- 開いたファイルは `fstat` で通常のファイル・リンク数 1・`check_local` が確かめた `(st_dev, st_ino)` と確かめてから読む
- 認証ファイルを書くのは `replace_auth` だけ。値は返り・例外の文に入れない（I5）

詳細設計の関数の表は `account_dir.py` に置くが、ファイルの行数の上限（1,000 行）のため分けた。`account_dir` が同じ名前で引く。
"""

from __future__ import annotations

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


def _put(d: int, what: str, data: dict[str, Any]) -> str:
    """記述子 `d` の下に一時ファイル（0600。`O_EXCL | O_NOFOLLOW`）を作って書き、同期する。一時の名前を返す。"""
    tmp = _AUTH_TMP.format(what=what, tok=secrets.token_hex(8))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        with suppress(OSError):  # noqa: NDF402 ファイル: 書けなかった一時ファイルの後始末。元の失敗を上げる
            os.unlink(tmp, dir_fd=d)
        raise
    return tmp


def _commit(d: int, tmp: str, name: str, seen: Optional[os.stat_result] = None) -> bool:
    """一時ファイル `tmp` を `name` へ置き換えて同期し、真。`seen` を渡せば `name` がそのままのときだけ置き換え、違えば偽。

    置き換えなかった・置き換えられなかったときは `tmp`（認証の値を持つ）を消す。失敗は消した後に上げる。"""
    try:
        if seen is not None and _sig(os.stat(name, dir_fd=d, follow_symlinks=False)) != _sig(seen):
            os.unlink(tmp, dir_fd=d)
            return False
        os.replace(tmp, name, src_dir_fd=d, dst_dir_fd=d)
    except OSError:
        with suppress(OSError):  # noqa: NDF402 ファイル: 置き換えられなかった一時ファイルの後始末。元の失敗を上げる
            os.unlink(tmp, dir_fd=d)
        raise
    os.fsync(d)
    return True


def _sig(st: os.stat_result) -> tuple[int, int, int, int]:
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


def replace_auth(local: Fixed, seen: os.stat_result, oauth: dict[str, Any], body: dict[str, Any]) -> bool:
    """`body` の `claudeAiOauth` だけを `oauth` に替えて置き換える。認証ファイルが `seen` のままのときだけ書いて真。

    呼ぶ側が登録の排他を持つ。比べて違えば（読んだ後に誰かが書いた）書かずに偽。書けないときも偽。"""
    d = pin_dir(local)
    if isinstance(d, str):
        return False
    try:
        return _commit(d, _put(d, "auth", {**body, "claudeAiOauth": oauth}), reg.CRED_FILE, seen)
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
        return _commit(d, tmp, reg.PENDING_FILE)
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


def _take(d: int) -> Optional[tuple[int, int]]:
    """記述子 `d` の下に claude と同じ形（`mkdir`）で排他を取り、取った実体を返す。持たれている・作れなければ None。

    持たれている排他は、NDF が強制終了で残したものも回収しない。claude は固定の名前だけで取り・放すので、残骸と確かめた後に
    claude が取り直したものと区別して退ける手段が無い。残骸は claude が自分の残骸の規則で退ける。"""
    try:
        os.mkdir(REFRESH_LOCK, 0o700, dir_fd=d)
    except OSError:  # noqa: NDF401 ファイル: 持たれている・作れない排他は取れないとして流す
        return None
    return _ident(d, REFRESH_LOCK)


def _release(d: int, ident: tuple[int, int]) -> None:
    """自分が作った実体のときだけ排他を消す。"""
    if _ident(d, REFRESH_LOCK) == ident:
        with suppress(OSError):  # noqa: NDF402 ファイル: 消えた排他はそのまま
            os.rmdir(REFRESH_LOCK, dir_fd=d)


@contextmanager
def refresh_lock(local: Fixed) -> Iterator[bool]:
    """claude と同じ更新の排他を待たずに取る。取れなければ偽を流し、オーナーの生きている排他・claude の排他を横取りしない。

    確かめた設定ディレクトリの記述子からの相対で作り・消すので、持つ間にディレクトリが移されても別のディレクトリの排他に触れない。"""
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
