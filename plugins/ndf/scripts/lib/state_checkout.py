"""旧の置き場のチェックアウトの記録（耐久の記録 `dbos/`・プランの状態 `sv/`）を移す（#2766）。`migrate_all` は台帳とあわせて移す入口（`state-home.py migrate`）。

- 移し先は、打った作業ツリーのチェックアウトの根（`<git の共通ディレクトリ>/ndf/state`）。実行時に `dbos/`・`sv/` を探す規則
  （作業ディレクトリの `state_home.checkout_home`）と同じである
- 耐久の記録は本体 `<鍵>.sqlite` だけを移す。移し元と移し先の同じ鍵の `.lock` を待たずに取り、衝突の再確認から移動の
  完了まで持つ（取れなければ移さず `kept`）。持っている間に移し元の WAL を本体へ書き戻し（`wal_checkpoint(TRUNCATE)`）、
  空になった `-wal`・`-shm` を消してから、本体を移し先の一時名へ写して名前を替え、移し元を消す。途中で止まっても完全な
  記録が旧か新の一方に残る。書き戻せなければ（読めない・busy）移さず `kept`。移し先のロックの実体は置き換えず、移し元のロックは移した後に消す
- 動いているプランは移さない: 旧の `dbos/` のロックが 1 つでも持たれていれば、`state_home.migrate` が何も移さず止まる
- 鍵は付け替えない。queue の鍵と耐久ワークフローの ID は旧の置き場のパスからできているため、移行の前の queue は
  `--resume` で続けられない（新しく流す）
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path
from typing import Mapping, Optional

import state_home
from state_home import _flock_free, find_git_dir

_DBOS_SUFFIXES = (".sqlite", ".sqlite-wal", ".sqlite-shm")  # 1 本の耐久の記録の組（`durable.purge_old` と同じ）。ロックは別に扱う


def _fold_wal(src: Path, key: str) -> Optional[str]:
    """`src/<key>.sqlite` の WAL を本体へ書き戻し、空の `-wal`・`-shm` を消す。書き戻せなければ理由を返す。"""
    db, wal, shm = (src / f"{key}{sfx}" for sfx in _DBOS_SUFFIXES)
    if db.exists():
        try:
            con = sqlite3.connect(f"{db.resolve().as_uri()}?mode=rw", uri=True, timeout=0)
            try:
                busy = con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]
            finally:
                con.close()
        except sqlite3.Error as e:
            return f"WAL を本体へ書き戻せない（{e}）"
        if busy:
            return "WAL を本体へ書き戻せない（busy）"
    if wal.exists() and wal.stat().st_size:
        return "WAL が本体へ書き戻されずに残っている" if db.exists() else "本体の無い WAL がある"
    wal.unlink(missing_ok=True)
    shm.unlink(missing_ok=True)
    return None


def _move_group(src: Path, key: str, dst: Path) -> Optional[str]:
    """耐久の記録 `src/<key>` を `dst/<key>` へ移す。両方のロックを持ったまま、WAL を書き戻した本体だけを移す。移さなければ理由を返す。"""
    fd = _flock_free(src / f"{key}.lock")
    if fd is None:
        return "ロックが持たれている（動いている耐久ワークフロー）"
    try:
        dst.mkdir(parents=True, exist_ok=True)
        to_fd = _flock_free(dst / f"{key}.lock", create=True)
        if to_fd is None:
            return "移し先の同じ鍵のロックが持たれている（動いている耐久ワークフロー）"
        try:
            if any((dst / f"{key}{sfx}").exists() for sfx in _DBOS_SUFFIXES):
                return "移し先に同じ鍵の記録がある"
            why = _fold_wal(src, key)
            if why:
                return why
            if (src / f"{key}.sqlite").exists():  # 写し終えた一時名を替えて公開し、それから移し元を消す
                tmp = dst / f".{key}.sqlite.migrating"
                shutil.copy2(src / f"{key}.sqlite", tmp)
                os.replace(tmp, dst / f"{key}.sqlite")
                (src / f"{key}.sqlite").unlink()
            (src / f"{key}.lock").unlink(missing_ok=True)  # 移し先のロックは持ったまま残す（実体を置き換えない）
        finally:
            os.close(to_fd)
    finally:
        if fd >= 0:
            os.close(fd)
    return None


def _move_dbos(old: Path, new: Path, moved: list, kept: list) -> None:
    """旧の `dbos/` の記録を組ごと `new` へ移す（`_move_group`。組の無いロックだけのものも、移し先のロックを作って消す）。"""
    keys = sorted({f.name[: -len(sfx)] for f in old.iterdir() for sfx in (*_DBOS_SUFFIXES, ".lock") if f.name.endswith(sfx)})
    for key in keys:
        name = f"{key}.sqlite" if (old / f"{key}.sqlite").exists() else f"{key}.lock"
        why = _move_group(old, key, new)
        if why:
            kept.append({"path": str(old / name), "reason": why})
        else:
            moved.append(str(new / name))


def _move_sv(old: Path, new: Path, moved: list, kept: list) -> None:
    """旧の `sv/` のプランの状態をディレクトリごと `new` へ移す。移し先に同じ名前があれば旧に残す。"""
    for child in sorted(old.iterdir()):
        if (new / child.name).exists() or (new / child.name).is_symlink():
            kept.append({"path": str(child), "reason": f"移し先 {new} に同じ名前のプランの状態がある"})
        else:
            new.mkdir(parents=True, exist_ok=True)
            shutil.move(str(child), str(new / child.name))
            moved.append(str(new / child.name))


def move_checkout(old: Path, cwd: Path) -> dict:
    """旧の根 `old` の `dbos/`・`sv/` を `cwd` の作業ツリーのチェックアウトの根へ移す。作業ツリーの外なら移さず理由を返す。"""
    out: dict = {"checkout": {"to": None, "reason": None}, "dbos": {"moved": [], "kept": []}, "sv": {"moved": [], "kept": []}}
    try:
        got = find_git_dir(cwd)
    except OSError:
        got = None
    if got is None:
        out["checkout"]["reason"] = f"{cwd} は git の作業ツリーでない。耐久の記録とプランの状態は移さない（主のリポジトリで打ち直す）"
        return out
    to = got[1] / "ndf" / "state"
    out["checkout"]["to"] = str(to)
    for kind, move in (("dbos", _move_dbos), ("sv", _move_sv)):
        if (old / kind).is_dir() and not (old / kind).is_symlink():
            move(old / kind, to / kind, out[kind]["moved"], out[kind]["kept"])
            try:
                (old / kind).rmdir()
            except OSError:
                pass  # 残したものがある
    return out


def migrate_all(env: Optional[Mapping[str, str]] = None, cwd: Optional[Path] = None) -> dict:
    """台帳とチェックアウトの記録を旧の根から移す（`state_home.migrate` に `move_checkout` を渡す。結果の形はそちら）。"""
    return state_home.migrate(env, cwd, move_checkout)
