"""旧の置き場のチェックアウトの記録（耐久の記録 `dbos/`・プランの状態 `sv/`）を移す（#2766）。`migrate` は台帳とあわせて移す入口（`state-home.py migrate`）。

- 耐久の記録は組（`<鍵>.sqlite` と `-wal`・`-shm`）ごと移す。移し元と移し先の同じ鍵の `.lock` を待たずに取り、衝突の
  再確認から一式の移動の完了まで持つ（取れなければ動いている起動があるとみなし、移さず `kept`）。移し先のロックの実体は
  置き換えず、移し元のロックは移した後に消す
- プランの状態は `plan.json` のパスから持ち主のチェックアウトが分かればそこへ、分からなければ打った作業ツリーのチェックアウトへ移す
- 鍵の付け替え: queue の実行の鍵は、プランの状態の queue-done.json の解決したパスから作る（`supervise_lib/flow.launch_queue`）。
  プランの状態を移すとそのパスが変わり、移した記録を開かなくなる（済んだプランを流し直す）。そこで、移し先の `sv/` の
  プランの状態ごとに、旧のパス（`<旧の根>/sv/<名前>`）から作る鍵の記録を、新しいパスから作る鍵へ 1 度だけ付け替える。
  旧の置き場は読まず、旧のパスの文字列から鍵を計算するだけなので、前に移し終えた分にも次の migrate で当たる。
  `run` の鍵の元はプランのパス（移しても変わらない）で、状態ディレクトリを元にするのはプランのパスの無い `Engine` だけ
  （その耐久ワークフローの ID も同じ元から作るため、ファイルの名前を替えても続けられない）なので付け替えない
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Mapping, Optional

from durable_keys import launch_key
import state_home
from state_home import _flock_free, _held_running, find_git_dir

_DBOS_SUFFIXES = (".sqlite", ".sqlite-wal", ".sqlite-shm")  # 1 本の耐久の記録の組（`durable.purge_old` と同じ）。ロックは別に扱う
_PLAN_PATHS = ("リポジトリ", "作業場所")  # プランの状態の持ち主を確かめるパス（`plan.json` の写し。この順に見る）
QUEUE_DONE = "queue-done.json"  # queue の実行の鍵の元（`supervise_lib/paths.queue_done_path` の既定）


def _move_group(src: Path, key: str, dst: Path, to_key: str) -> Optional[str]:
    """耐久の記録 `src/<key>` の組を `dst/<to_key>` へ移す。両方の鍵のロックを持ったまま移す。移さなければ理由を返す。"""
    fd = _flock_free(src / f"{key}.lock")
    if fd is None:
        return "ロックが持たれている（動いている耐久ワークフロー）"
    try:
        dst.mkdir(parents=True, exist_ok=True)
        to_fd = _flock_free(dst / f"{to_key}.lock", create=True)
        if to_fd is None:
            return "移し先の同じ鍵のロックが持たれている（動いている耐久ワークフロー）"
        try:
            if any((dst / f"{to_key}{sfx}").exists() for sfx in _DBOS_SUFFIXES):
                return "移し先に同じ鍵の記録がある" if key == to_key else f"付け替え先の鍵 {to_key} の記録がある"
            for sfx in [sfx for sfx in _DBOS_SUFFIXES if (src / f"{key}{sfx}").exists()]:
                shutil.move(str(src / f"{key}{sfx}"), str(dst / f"{to_key}{sfx}"))
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
        why = _move_group(old, key, new, key)
        if why:
            kept.append({"path": str(old / name), "reason": why})
        else:
            moved.append(str(new / name))


def _rekey(svs: list[Path], legacy_sv: Path, dbos: list[Path], rekeyed: list, kept: list) -> None:
    """移し先の `sv/`（`svs`）のプランの状態ごとに、旧のパスから作る queue の鍵の記録を新しいパスから作る鍵へ付け替える。
    探す記録の置き場は、その `sv/` のチェックアウトの `dbos/` と `dbos`（打った作業ツリーのチェックアウト）。"""
    for sv in svs:
        for state in sorted(sv.iterdir()) if sv.is_dir() else []:
            old_key = launch_key("queue", str((legacy_sv / state.name / QUEUE_DONE).resolve()))
            new_key = launch_key("queue", str((state / QUEUE_DONE).resolve()))
            for d in dict.fromkeys([sv.parent / "dbos", *dbos]):
                if old_key == new_key or not any((d / f"{old_key}{sfx}").exists() for sfx in _DBOS_SUFFIXES):
                    continue
                why = _move_group(d, old_key, d, new_key)
                if why:
                    kept.append({"path": str(d / f"{old_key}.sqlite"), "reason": why})
                else:
                    rekeyed.append({"from": str(d / f"{old_key}.sqlite"), "to": str(d / f"{new_key}.sqlite"), "plan_state": str(state)})


def _step_running(plan_state: Path) -> bool:
    """プランの状態 `plan_state` のステップの子（`step.pid` の `children`）が生きているか。"""
    try:
        data = json.loads((plan_state / "step.pid").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    children = data.get("children") if isinstance(data, dict) else None
    return any(isinstance(c, dict) and isinstance(c.get("pid"), int) and _held_running(c["pid"]) for c in children or [])


def _sv_owner(plan_state: Path) -> Optional[Path]:
    """プランの状態 `plan_state` の持ち主のチェックアウトの根（`<git の共通ディレクトリ>/ndf/state`）。
    `plan.json` のパスのうち、今ある作業ツリーの最初のもので決める。決まらなければ None。"""
    try:
        plan = json.loads((plan_state / "plan.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for key in _PLAN_PATHS:
        value = plan.get(key) if isinstance(plan, dict) else None
        if not isinstance(value, str) or not Path(value).is_absolute():
            continue
        try:
            got = find_git_dir(Path(value)) if Path(value).is_dir() else None
        except OSError:
            got = None
        if got is not None:
            return got[1] / "ndf" / "state"
    return None


def _move_sv(old: Path, new: Path, moved: list, kept: list) -> None:
    """旧の `sv/` のプランの状態をディレクトリごと、持ち主のチェックアウトの根（`_sv_owner`）の `sv/` へ移す。
    持ち主が決まらない（パスを持たない・作業ツリーが無くなった・git の外）ものは `new`（打った作業ツリーのチェックアウト）へ移す。"""
    for child in sorted(old.iterdir()):
        owner = _sv_owner(child)
        to = owner / "sv" if owner is not None else new
        if (to / child.name).exists() or (to / child.name).is_symlink():
            kept.append({"path": str(child), "reason": f"移し先 {to} に同じ名前のプランの状態がある"})
        elif _step_running(child):
            kept.append({"path": str(child), "reason": "ステップの子が生きている（動いているプラン）"})
        else:
            to.mkdir(parents=True, exist_ok=True)
            shutil.move(str(child), str(to / child.name))
            moved.append(str(to / child.name))


def move_checkout(old: Path, cwd: Path) -> dict:
    """旧の根 `old` の `dbos/`・`sv/` を `cwd` の作業ツリーのチェックアウトの根へ移し、queue の鍵を付け替える（`_rekey`）。
    作業ツリーの外なら移さず理由を返す。結果の `dbos` は `{"moved", "kept", "rekeyed": [{"from", "to", "plan_state"}]}`。"""
    out: dict = {
        "checkout": {"to": None, "reason": None},
        "dbos": {"moved": [], "kept": [], "rekeyed": []},
        "sv": {"moved": [], "kept": []},
    }
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
    svs = [to / "sv", *(Path(p).parent for p in out["sv"]["moved"])]
    _rekey(list(dict.fromkeys(svs)), old / "sv", [to / "dbos"], out["dbos"]["rekeyed"], out["dbos"]["kept"])
    return out


def migrate(env: Optional[Mapping[str, str]] = None, cwd: Optional[Path] = None) -> dict:
    """台帳とチェックアウトの記録を旧の根から移す（`state_home.migrate` に `move_checkout` を渡す。結果の形はそちら）。"""
    return state_home.migrate(env, cwd, move_checkout)
