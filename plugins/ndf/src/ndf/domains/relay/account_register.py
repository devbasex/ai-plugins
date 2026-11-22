"""登録と登録し直し（`ndf relay account add` が保存先へ置く処理。#3604 #3864）。

ログインの済んだ一時ディレクトリを保存先へ置き、同じ名前の登録し直しでは認証のファイルだけを置き換える。旧形式の
`.config.json` はアカウント固有の `backups/` へ退避する。保存先の形と排他は `account_registry` が持つ。
"""

from __future__ import annotations

import os
import secrets
import shutil
from pathlib import Path
from typing import Any, Callable, Optional

from ndf.domains.relay import account_registry as reg


def _move_aside(src: Path, backups: Path, stem: str) -> Path:
    """`src` を `backups/<stem>` へ移す。同じ名前があれば `-1`・`-2`… を足し、既存のバックアップを上書きしない。

    移し先を `O_EXCL` で空のまま作ってから、その自分で作った空のファイルへ `os.replace` で移す。既にある名前は作れずに
    次の名前へ進むので上書きせず、ハードリンクも要らない（ハードリンク非対応のファイルシステムでも退避できる）。"""
    for i in range(1000):
        dst = backups / (stem if i == 0 else f"{stem}-{i}")
        try:
            os.close(os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600))
        except FileExistsError:  # noqa: S112 ファイル: 既にある退避先は上書きせず、次の名前を試す
            continue
        try:
            os.replace(src, dst)
        except BaseException:
            dst.unlink(missing_ok=True)
            raise
        return dst
    raise reg.AccountError(f"{backups} に {stem} の空いた退避先が無い")


def _replace_on_relogin(staging: Path, final: Path, now: float) -> None:
    """登録し直しで、保存先 `final` の認証のファイルだけを `staging` のものへ置き換える。ほかの項目には触れない。

    置き換えの前に今の登録へ `needs_relogin` を立てる（読めなければ `account.json` を消す）。途中で止まって新しい認証と
    旧い本人・世代が混ざっても、起動と使用率の取得はその登録を飛ばす。`needs_relogin` は最後に置く新しい `account.json` で外れる。
    旧形式の `.config.json` は `backups/` へ移す（残すと Claude Code が先に読み、次の起動も `legacy_config` で止まる）。
    """
    try:
        reg.mark_relogin(final.parent, final.name)
    except reg.NotRegistered:  # noqa: S110 ファイル: 登録の無い保存先は `needs_relogin` を立てる先が無い（新しい account.json を最後に置く）
        pass
    except reg.AccountError:  # 壊れた登録は `needs_relogin` を書けない。消して登録の無い状態にする（新しい account.json を最後に置く）
        (final / reg.ACCOUNT_FILE).unlink(missing_ok=True)
    legacy = final / reg.LEGACY_CONFIG_FILE
    if legacy.exists() or legacy.is_symlink():
        backups = final / reg.BACKUPS_DIR
        if backups.is_symlink():
            raise reg.AccountError(f"{backups} が symlink なので旧形式の設定を退避できない")
        backups.mkdir(mode=0o700, exist_ok=True)
        _move_aside(legacy, backups, f"{reg.LEGACY_CONFIG_FILE}.{int(now)}")
    # 同期のバックアップ・途中の記録・アカウント側の一時ファイルも消し、次の同期を最初の同期にする
    for f in (reg.USAGE_FILE, reg.BASE_FILE, reg.BASE_NEXT_FILE):
        (final / f).unlink(missing_ok=True)
    for tmp in final.glob(reg.SYNC_TMP_FORMAT.format(path=reg.CONFIG_FILE, round="*")):
        tmp.unlink(missing_ok=True)
    for f in reg.REPLACED_ON_RELOGIN:
        if (staging / f).exists():
            os.replace(staging / f, final / f)
        elif f == reg.CONFIG_FILE:  # 前の oauthAccount を残さない
            (final / f).unlink(missing_ok=True)
    shutil.rmtree(staging, ignore_errors=True)


def register(
    store: Path, name: str, staging: Path, who: reg.Identity, now: float, settle: Optional[Callable[[Optional[str], str], None]] = None
) -> str:
    """ログインの済んだ `staging` を `name` として置き、新しい登録の世代を返す。同じ名前の保存先は丸ごと消さず、認証のファイルだけを置き換える。

    世代は登録のたびに新しく発行する（同じ名前の登録し直しでも、前の世代の記録を今の記録と読まない）。`settle` を渡すと、
    置いた後に同じ排他の中で `settle(前の世代, 新しい世代)` を呼ぶ（前の世代の記録の後始末。前の登録が無ければ前の世代は None）。
    """
    with reg.locked(store, name):
        return register_held(store, name, staging, who, now, settle)


def register_held(
    store: Path, name: str, staging: Path, who: reg.Identity, now: float, settle: Optional[Callable[[Optional[str], str], None]] = None
) -> str:
    """`register` の本体。呼ぶ側が登録の排他（`locked(store, name)`）を持つ（`account add` が認可コードを渡す前から持ち続ける）。"""
    reg.refuse_in_use(store, name)
    final = reg.account_dir(store, name)
    try:
        old: Optional[str] = reg.generation(store, name) if (final / reg.ACCOUNT_FILE).is_file() else None
    except reg.AccountError:  # 壊れた前の登録は世代が分からない。後始末を空文字の世代で打つ
        old = ""
    new = secrets.token_hex(16)
    row: dict[str, Any] = {
        "name": name,
        "email": who.email,
        "registered_at": reg.iso_utc(now),
        "needs_relogin": False,
        "limit": None,
        "generation": new,
    }
    if who.org_id:
        row.update(org_id=who.org_id, org_name=who.org_name)
    reg.write(staging / reg.ACCOUNT_FILE, row)
    if final.is_dir() and not final.is_symlink():
        _replace_on_relogin(staging, final, now)
    else:
        if final.is_symlink() or final.exists():
            final.unlink()
        os.replace(staging, final)
    reg.secure(store, name)
    if settle is not None:
        settle(old, new)
    return new
