"""カットポイントのアナウンス（`ndf relay notice`）。呼び手の claude がラッパーの直接の子かを決め、1 文を組む（#3569）。

旧いプラグインの `relay_lib/mark.py` の `notice_lines` と `proc.py` の `relay_position` を移した。

- 区間のやり取りの置き場（`NDF_RELAY_DIR`）が無いか、ディレクトリでなければ `no-dir`
- ラッパーの排他（`relay.lock`）が持たれていなければ `not-running`
- 親をたどって最初に当たる claude の親の pid が、ラッパーの状態（`wrapper.json`）の `pid` と違えば `not-child`
  （fork したセッション・別のエントリポイント。conductor が Bash から起こした `claude -p` は子の claude の孫に当たり、先にそちらに当たる）
- 同じなら `relay`

秒数はラッパーの静かな時間（上限の表 `relay.quiet`）そのもので、切り替えの時間は足さない。
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import NamedTuple, Optional

import psutil

from ndf.domains.relay import registry, signals

# 親のたどりの上限の世代
ANCESTOR_LIMIT = 64
# 位置の種類
RELAY, NO_DIR, NOT_RUNNING, NOT_CHILD = "relay", "no-dir", "not-running", "not-child"


class Position(NamedTuple):
    kind: str
    wrapper_pid: Optional[int] = None
    child_pid: Optional[int] = None


class Notice(NamedTuple):
    inside: bool
    text: str


def _parent_and_name(pid: int) -> Optional[tuple[int, str]]:
    """(親の pid, 名前)。読めなければ None。"""
    try:
        p = psutil.Process(pid)
        return p.ppid(), p.name()
    except psutil.Error:  # noqa: NDF401 外部コマンド: プロセスが消えた・読めないなら親をたどれない
        return None


def first_claude(start_pid: int, info: Callable[[int], Optional[tuple[int, str]]] = _parent_and_name) -> Optional[tuple[int, int]]:
    """`start_pid` から親をたどって最初に当たる claude の (pid, 親の pid)。当たらなければ None。"""
    pid = start_pid
    for _ in range(ANCESTOR_LIMIT):
        if pid <= 1:
            return None
        got = info(pid)
        if got is None:
            return None
        ppid, name = got
        if name == "claude":
            return pid, ppid
        pid = ppid
    return None


def _wrapper_pid(directory: Path) -> Optional[int]:
    try:
        state = registry.read_state(directory)
    except registry.RegistryError:  # noqa: NDF401 ファイル: 壊れた状態は動いていないとみなす
        return None
    pid = state["pid"] if state is not None and "pid" in state else None
    return pid if isinstance(pid, int) else None


def position(environ: Mapping[str, str], start_pid: int, info: Callable[[int], Optional[tuple[int, str]]] = _parent_and_name) -> Position:
    """呼び手の claude とラッパーの位置。"""
    directory = signals.relay_dir(environ)
    if directory is None:
        return Position(NO_DIR)
    wrapper_pid = _wrapper_pid(directory)
    if not registry.lock_busy(directory / registry.LOCK_FILE) or wrapper_pid is None:
        return Position(NOT_RUNNING)
    found = first_claude(start_pid, info)
    if found is None or found[1] != wrapper_pid:
        return Position(NOT_CHILD, wrapper_pid, found[0] if found is not None else None)
    return Position(RELAY, wrapper_pid, found[0])


def lines(pos: Position, quiet: float) -> Notice:
    """位置と静かな時間からアナウンスの 1 文を組む。外のときは理由で文を変え、原因と対処を書く（#1016）。"""
    if pos.kind == NOT_RUNNING:
        return Notice(False, "ラッパーは既に終わっている。/exit してから claude を起動し、下の中身を最初の入力として貼り付ける")
    if pos.kind == NOT_CHILD:
        return Notice(
            False,
            "ラッパーは元の会話しか見ていないため、この会話で出した ndf-next は自動では拾われない。"
            "元の会話へ戻って同じ ndf-next を出すか、元の会話を /exit してから claude を起動し、下の中身を最初の入力として貼り付ける",
        )
    if pos.kind != RELAY:
        return Notice(False, "/exit してから claude を起動し、下の中身を最初の入力として貼り付ける（ラッパーを入れると自動になる）")
    if math.isinf(quiet) and quiet > 0:
        return Notice(
            True,
            "静かな時間が有限でないため、ラッパーは自動で切り替えない。/exit してから claude を起動し、下の中身を最初の入力として貼り付ける",
        )
    # ラッパーは負・nan の静かな時間を待たずに通すので、0 として数える
    n = round(quiet) if quiet == quiet and quiet > 0 else 0
    when = f"約 {n} 秒後に" if n > 0 else "まもなく"
    return Notice(
        True,
        f"{when}自動で新しい会話へ切り替わる。キー入力やスクロールをせずに、そのまま待つ"
        "（切り替わらずに ndf-relay: で始まる 1 行が出たら、その案内に従う）",
    )
