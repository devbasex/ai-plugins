"""旧の置き場のチェックアウトの記録（耐久の記録 `dbos/`・プランの状態 `sv/`）を移す（#2766）。`migrate_all` は台帳とあわせて移す入口（`state-home.py migrate`）。

- 移し先は、打った作業ツリーのチェックアウトの根（`<git の共通ディレクトリ>/ndf/state`）。実行時に `dbos/`・`sv/` を探す規則
  （作業ディレクトリの `state_home.checkout_home`）と同じである
- 耐久の記録は組（`<鍵>.sqlite` と `-wal`・`-shm`）ごと移す。移し元と移し先の同じ鍵の `.lock` を待たずに取り、衝突の
  再確認から一式の移動の完了まで持つ（取れなければ移さず `kept`）。移し先のロックの実体は置き換えず、移し元のロックは移した後に消す
- 動いているプランは移さない: 旧の `dbos/` のロックが 1 つでも持たれていれば、`state_home.migrate` が何も移さず止まる
- 鍵は付け替えない。queue の鍵と耐久ワークフローの ID は旧の置き場のパスからできているため、移行の前の queue は
  `--resume` で続けられない（新しく流す）
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Mapping, Optional

import state_home
from state_home import _flock_free, find_git_dir

_DBOS_SUFFIXES = (".sqlite", ".sqlite-wal", ".sqlite-shm")  # 1 本の耐久の記録の組（`durable.purge_old` と同じ）。ロックは別に扱う


def _move_group(src: Path, key: str, dst: Path) -> Optional[str]:
    """耐久の記録 `src/<key>` の組を `dst/<key>` へ移す。両方のロックを持ったまま移す。移さなければ理由を返す。"""
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
            for sfx in [sfx for sfx in _DBOS_SUFFIXES if (src / f"{key}{sfx}").exists()]:
                shutil.move(str(src / f"{key}{sfx}"), str(dst / f"{key}{sfx}"))
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
