"""`.claude.json` の共有する部分（`projects`・`mcpServers`）の 3 者の同期（`account_dir` の部品。#3604 #3864）。

共有側・アカウント側・同期のバックアップ（前の同期の値）を葉ごとに突き合わせ、片側だけの変更を他方へ移し、
両側の変更が食い違えば共有側を採る（決定 2）。書き込みの進み具合は途中の記録（`.ndf-shared-base.next.json`）と
一時ファイルの有無で持ち、途中で止まった後の次の同期が詳細設計の「書く手順と途中で止まった後」の表の値にそろえる。

読み書きは `check_local` が確かめた `Local` の固定したパスだけで行い、claude 本体と同じ `<固定したパス>.lock` の
ディレクトリを取った後に、両側の実体を確かめ直す。値（`mcpServers` の認証のヘッダーなど）は返りに入れない。
詳細設計の関数の表は `account_dir.py` に置くが、ファイルの行数の上限（1,000 行）のため分けた。
"""

from __future__ import annotations

import glob
import json
import os
import secrets
import stat
import time
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional, Protocol, Union

from ndf.contracts import limits
from ndf.domains.relay import account_files as af
from ndf.domains.relay import account_registry as reg

SHARED_KEYS = ("projects", "mcpServers")
ONBOARDING_KEYS = ("hasCompletedOnboarding", "lastOnboardingVersion")
LOCK_STALE = 10.0  # 本体と同じく、これより古い `<実パス>.lock` は残骸として除く
FIRST = "first"  # 側の基準: 最初の同期（共有側の値にそろえる）
SAME_AS_B = "B"  # 側の基準: 記録の B と同じ
_MISSING = object()


class Fixed(Protocol):
    """`account_dir.Local` の形。"""

    account: Path
    config: Path
    shared: Path
    ids: dict[str, Optional[tuple[int, int]]]


class _Lost(Exception):
    """固定したパスの実体が `Local` と違う（読み書きしない）。"""


Base = Union[dict[str, Any], str]  # 値の object か FIRST


@dataclass
class _Plan:
    """側ごとの基準。`first` はこの同期（と、その復旧の連なり）が最初の同期から始まったか。"""

    account: Base
    shared: Base
    first: bool


@dataclass
class _Sides:
    s: dict[str, Any]
    a: dict[str, Any]
    new_s: dict[str, Any]
    new_a: dict[str, Any]
    part: dict[str, Any]  # 両側へ書く共有する部分の値（B）
    uid: Optional[str]


# ---------------------------------------------------------------- 本体の錠と実体


def _stale(lock: Path) -> bool:
    """10 秒より古い錠を消したら真（消えていても真で取り直す）。"""
    try:
        if time.time() - os.stat(lock).st_mtime <= LOCK_STALE:
            return False
        os.rmdir(lock)
    except FileNotFoundError:  # noqa: NDF401 ファイル: 待つ間にオーナーが返した
        return True
    except OSError:  # noqa: NDF401 ファイル: 調べられない錠は持たれているとみなして待つ
        return False
    return True


@contextmanager
def _claude_lock(path: Path) -> Iterator[None]:
    """claude 本体と同じ `<固定したパス>.lock` のディレクトリの排他。取れなければ `TimeoutError`。"""
    lock = Path(f"{path}.lock")
    deadline = time.monotonic() + limits.seconds("claude-account-dir.lock")
    while True:
        try:
            os.mkdir(lock)
            break
        except FileExistsError:
            if _stale(lock):
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError(lock.name) from None
            time.sleep(0.05)
    try:
        yield
    finally:
        with suppress(OSError):  # noqa: NDF402 ファイル: 消えた錠はそのまま
            os.rmdir(lock)


def _same(st: os.stat_result, want: Optional[tuple[int, int]]) -> bool:
    return stat.S_ISREG(st.st_mode) and st.st_nlink == 1 and (st.st_dev, st.st_ino) == want


def _recheck(path: Path, want: Optional[tuple[int, int]]) -> bool:
    """錠を取った後の確かめ直し。`want` が None なら、まだ無いこと。"""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return want is None
    except OSError:  # noqa: NDF401 ファイル: 調べられない実体は確かめに外れる
        return False
    return _same(st, want)


def _load(fd: int) -> Any:
    with os.fdopen(fd, "rb") as f:
        try:
            return json.loads(f.read().decode("utf-8"))
        except (OSError, ValueError):  # noqa: NDF401 ファイル: 壊れた中身は呼び手が理由の符号にする
            return None


def _read_side(path: Path, want: Optional[tuple[int, int]]) -> Optional[dict[str, Any]]:
    """固定したパスの設定ファイル。無ければ空、JSON の object でなければ None。実体が違えば `_Lost`。"""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        if want is None:
            return {}
        raise _Lost from None
    except OSError:
        raise _Lost from None
    if not _same(os.fstat(fd), want):
        os.close(fd)
        raise _Lost
    data = _load(fd)
    return data if isinstance(data, dict) else None


def _plain(path: Path) -> Optional[dict[str, Any]]:
    """バックアップと途中の記録（アカウント固有のファイル）。無い・読めなければ None。"""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:  # noqa: NDF401 ファイル: 無い・読めない記録は無いものとする
        return None
    data = _load(fd)
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------- 葉の突き合わせ


def _leaves(key: str, value: Any) -> dict[tuple[str, ...], Any]:
    """`projects` はプロジェクトのパスと項目の 2 層、`mcpServers` は名前の 1 層の葉へ平らにする。"""
    out: dict[tuple[str, ...], Any] = {}
    if not isinstance(value, dict):
        return out
    for k, v in value.items():
        if key == "projects" and isinstance(v, dict) and v:
            out.update({(k, k2): v2 for k2, v2 in v.items()})
        else:
            out[(k,)] = v
    return out


def _unflatten(leaves: dict[tuple[str, ...], Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for path, v in sorted(leaves.items(), key=lambda kv: len(kv[0])):  # 1 層の葉を先に（空の項目を 2 層の葉で上書き）
        if len(path) == 1:
            out[path[0]] = v
        else:
            sub = out.get(path[0])
            if not isinstance(sub, dict):
                sub = out[path[0]] = {}
            sub[path[1]] = v
    return out


def _state(leaves: dict, k: tuple[str, ...]) -> Any:
    """葉の状態（値か「無い」）。"""
    return leaves[k] if k in leaves else _MISSING


def _merge_leaves(s: dict, a: dict, bs: dict, ba: dict) -> dict:
    """葉ごとに、共有側が基準から変わっていれば共有側、そうでなければアカウント側の状態を採る。

    片側だけの変更はその側、両側の変更は共有側になる。どちらも変わっていなければアカウント側（両側の基準が同じなら
    共有側と同じ値。復旧でアカウント側だけが B へ置き換え済みなら B）。"""
    out = {}
    for k in set(s) | set(a) | set(bs) | set(ba):
        sv, av = _state(s, k), _state(a, k)
        v = sv if sv != _state(bs, k) else av
        if v is not _MISSING:
            out[k] = v
    return out


def _merged(s: dict[str, Any], a: dict[str, Any], plan: _Plan) -> _Sides:
    new_s, new_a, part = dict(s), dict(a), {}
    for key in SHARED_KEYS:
        sl, al = _leaves(key, s.get(key)), _leaves(key, a.get(key))
        if isinstance(plan.account, dict) and isinstance(plan.shared, dict):
            leaves = _merge_leaves(sl, al, _leaves(key, plan.shared.get(key)), _leaves(key, plan.account.get(key)))
        else:  # 最初の同期: 共有側の値にそろえる（アカウント側にだけある葉も消す）
            leaves = sl
        value = _unflatten(leaves)
        if key in s or leaves:
            new_s[key] = value
        if key in a or leaves:
            new_a[key] = value
        part[key] = value
    for k in ONBOARDING_KEYS:
        if k not in new_a and k in s:
            new_a[k] = s[k]
    uid = a.get("userID") if isinstance(a.get("userID"), str) else None
    return _Sides(s, a, new_s, new_a, part, uid)


# ---------------------------------------------------------------- 側ごとの基準


def _usable(rec: Any, uid: Optional[str]) -> bool:
    if not (uid and isinstance(rec, dict) and rec.get("userID") == uid and isinstance(rec.get("B"), dict)):
        return False
    base, tmp = rec.get("base"), rec.get("tmp")
    if not (isinstance(base, dict) and isinstance(tmp, dict)):
        return False
    return all(isinstance(base.get(k), dict) or base.get(k) in (FIRST, SAME_AS_B) for k in ("account", "shared")) and all(
        tmp.get(k) is None or isinstance(tmp.get(k), str) for k in ("account", "shared")
    )


def _side_base(rec: dict[str, Any], side: str) -> Base:
    """途中の記録が指す一時ファイルの有無と記録の基準から、その側の基準を決める（詳細設計の表）。"""
    tmp, base = rec["tmp"].get(side), rec["base"][side]
    if tmp is None or not os.path.lexists(tmp):  # 置き換え済み（その回で変えなかった側も B のまま）
        return rec["B"]
    if isinstance(base, dict):
        return base
    if side == "account" and base == FIRST:
        return FIRST
    return rec["B"]  # 共有側の FIRST・B は B（最初の同期の B は共有側の値から作る）


def _plan(local: Fixed, uid: Optional[str]) -> _Plan:
    nxt = local.account / reg.BASE_NEXT_FILE
    rec = _plain(nxt)
    backup = _plain(local.account / reg.BASE_FILE)
    backup_ok = bool(uid) and isinstance(backup, dict) and backup.get("userID") == uid and isinstance(backup.get("B"), dict)
    if rec is not None and _usable(rec, uid) and (rec.get("first") is True or backup_ok):
        return _Plan(_side_base(rec, "account"), _side_base(rec, "shared"), rec.get("first") is True)
    if os.path.lexists(nxt):  # 読めない・userID の違う途中の記録は、一時ファイルと一緒に無いものとする（一時ファイルは終わりに消す）
        os.unlink(nxt)
    if backup_ok and backup is not None:
        return _Plan(backup["B"], backup["B"], False)
    return _Plan(FIRST, FIRST, True)


# ---------------------------------------------------------------- 書く手順


def _mode(path: Path) -> int:
    try:
        return os.lstat(path).st_mode & 0o777
    except OSError:  # noqa: NDF401 ファイル: まだ無いファイルは 0600 で作る
        return 0o600


def _stage(path: Path, data: dict[str, Any], rnd: str, mode: int) -> Path:
    """固定したパスと同じディレクトリの一時ファイル `<パス>.ndf-sync.<回>.tmp` に書いて同期する。"""
    tmp = Path(reg.SYNC_TMP_FORMAT.format(path=path, round=rnd))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            os.fchmod(f.fileno(), mode)
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    return tmp


def _record(sides: _Sides, plan: _Plan, rnd: str, tmps: dict[str, Optional[Path]]) -> dict[str, Any]:
    return {
        "version": 1,
        "B": sides.part,
        "userID": sides.uid,
        "round": rnd,
        "first": plan.first,
        "tmp": {k: str(v) if v else None for k, v in tmps.items()},
        "base": {"account": plan.account, "shared": plan.shared},
    }


def _sweep(local: Fixed) -> None:
    """途中の記録が指さない同期の一時ファイル（前の回の残り）を消す。"""
    found = [*local.account.glob(reg.SYNC_TMP_FORMAT.format(path="*", round="*"))]
    found += local.shared.parent.glob(reg.SYNC_TMP_FORMAT.format(path=glob.escape(local.shared.name), round="*"))
    for p in found:
        with suppress(OSError):  # noqa: NDF402 ファイル: 消せない残りは次の同期で消す（判定には使わない）
            p.unlink()


def _keep(local: Fixed, sides: _Sides, plan: _Plan) -> Optional[str]:
    """両側が既に B のとき。バックアップだけを B にそろえ、途中の記録と一時ファイルを消す。"""
    base = local.account / reg.BASE_FILE
    old = _plain(base)
    try:
        if old is None or old.get("B") != sides.part or old.get("userID") != sides.uid:
            rnd = secrets.token_hex(8)
            os.replace(_stage(base, _record(sides, plan, rnd, {"account": None, "shared": None}), rnd, 0o600), base)
        (local.account / reg.BASE_NEXT_FILE).unlink(missing_ok=True)
    except OSError:  # noqa: NDF401 ファイル: 書けなければ次の同期でそろえる
        return "write_failed"
    _sweep(local)
    return None


def _write_round(local: Fixed, sides: _Sides, plan: _Plan) -> Optional[str]:
    """1〜6 の手順。途中の記録を書く前の失敗は一時ファイルを消し、書いた後の失敗は次の同期の復旧に任せる。"""
    rnd, nxt = secrets.token_hex(8), local.account / reg.BASE_NEXT_FILE
    made: list[Path] = []
    tmps: dict[str, Optional[Path]] = {"account": None, "shared": None}
    try:
        if sides.new_a != sides.a:
            tmps["account"] = _stage(local.config, sides.new_a, rnd, _mode(local.config))
            made.append(tmps["account"])
        if sides.new_s != sides.s:
            tmps["shared"] = _stage(local.shared, sides.new_s, rnd, _mode(local.shared))
            made.append(tmps["shared"])
        made.append(_stage(nxt, _record(sides, plan, rnd, tmps), rnd, 0o600))
        os.replace(made[-1], nxt)
    except OSError:
        for p in made:
            p.unlink(missing_ok=True)
        return "write_failed"
    try:
        for side, dst in (("account", local.config), ("shared", local.shared)):
            if tmps[side] is not None:
                os.replace(tmps[side], dst)
        os.replace(nxt, local.account / reg.BASE_FILE)
    except OSError:  # noqa: NDF401 ファイル: 途中の記録と一時ファイルを残し、次の同期が復旧する
        return "write_failed"
    _sweep(local)
    return None


def _sync_held(local: Fixed) -> Optional[str]:
    s = _read_side(local.shared, local.ids.get("shared"))
    if s is None:
        return "shared_unreadable"
    a = _read_side(local.config, local.ids.get("config"))
    if a is None:
        return "account_unreadable"
    uid = a.get("userID") if isinstance(a.get("userID"), str) else None
    plan = _plan(local, uid)
    sides = _merged(s, a, plan)
    if sides.new_s == s and sides.new_a == a:
        return _keep(local, sides, plan)
    return _write_round(local, sides, plan)


def sync_config(local: Fixed) -> Optional[str]:
    """`local` が固定したパスの `.claude.json` の共有する部分を 3 者で突き合わせて両側へ書く。パスを解き直さない。

    呼ぶ側がアカウントの排他を持つ。同期できたら None、できなければ理由の符号（`same_file`・`not_account_local`・
    `shared_unreadable`・`account_unreadable`・`lock_busy`・`write_failed`）。"""
    if local.config == local.shared:
        return "same_file"
    try:
        with _claude_lock(local.config), _claude_lock(local.shared):
            if not (_recheck(local.config, local.ids.get("config")) and _recheck(local.shared, local.ids.get("shared"))):
                return af.NOT_LOCAL
            return _sync_held(local)
    except _Lost:
        return af.NOT_LOCAL
    except TimeoutError:  # noqa: NDF401 ファイル: 本体の錠を取れなければ同期せずに返す（次の同期でそろえる）
        return "lock_busy"
    except OSError:  # noqa: NDF401 ファイル: 錠・途中の記録の書き込みの失敗は次の同期でそろえる
        return "write_failed"
