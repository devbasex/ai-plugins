"""`claude -p` の起動の直前の確かめ（#1959。`claude.py` から分けた）。

スロットを取った後、子を起動する直前に、中継の確かめ直し（`recheck`）・承認した起動の設定の照らし直し（`verify`）・
CLI の下限（`claude --version`）の順に確かめる。
起動の期限（`left`）を持つ起動は、確かめの前と後に残りを測る。スロットを待つ上限と、子の期限も残りに収める。止めたときは、止めた確かめ（`Check`）と理由の 1 行を返す。
回答（`claude.ClaudeReply`）と失敗の理由（`claude.Failure`）への直しは呼び手の `claude` が持つ（このモジュールは
`claude` を読まない。読むと import の輪になる）。
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Callable, Mapping, NamedTuple, Optional, Protocol, Sequence

from ndf.adapters import cli_floor, launch_slot

# 起動の期限が切れて起動しなかったときの理由の 1 行
EXPIRED = "起動の期限が、起動を待つ間と起動の前の確かめの間に切れた"


class Check(StrEnum):
    """起動を止めた確かめ。"""

    RECHECK = "recheck"  # 中継の確かめ直し（`AccountRun.recheck`）
    VERIFY = "verify"  # 承認した起動の設定の照らし直し（`LaunchOptions.verify`）
    FLOOR = "floor"  # CLI の下限（`cli_floor.require`）
    DEADLINE = "launch_deadline"  # 起動の期限の残り（`LaunchOptions.left`。#2944）


class Stop(NamedTuple):
    """起動を止めた確かめと、理由の 1 行。"""

    check: Check
    detail: str


class PrecheckRun(Protocol):
    """確かめる起動（`claude.AccountRun` が満たす）。"""

    @property
    def argv(self) -> Sequence[str]: ...

    @property
    def env(self) -> Mapping[str, str]: ...

    @property
    def recheck(self) -> Optional[Callable[[], Optional[str]]]: ...


class PrecheckOptions(Protocol):
    """確かめの選び方（`claude.LaunchOptions` が満たす）。"""

    @property
    def cli_floor(self) -> bool: ...

    @property
    def verify(self) -> Optional[Callable[[], Optional[str]]]: ...

    @property
    def ready(self) -> Optional[Callable[[], None]]: ...

    @property
    def left(self) -> Optional[Callable[[], float]]: ...

    @property
    def slot_wait(self) -> Optional[float]: ...


def spent(left: Optional[Callable[[], float]]) -> Optional[str]:
    """起動の期限の残り（`left`）が無ければ理由の 1 行（`EXPIRED`）。期限を持たないか残りがあれば None。"""
    return EXPIRED if left is not None and left() <= 0 else None


def time_left(left: Optional[Callable[[], float]], timeout: float) -> float:
    """子の期限の秒。起動の期限を持つ起動は今の残り（0 未満にしない）、持たなければ `timeout`。"""
    return timeout if left is None else max(0.0, left())


def slot_wait(options: PrecheckOptions) -> Optional[float]:
    """重い起動のスロットを待つ上限の秒。起動の期限を持つ起動は、元の上限（`slot_wait`。None なら設定の上限）と残りの小さい方。"""
    if options.left is None:
        return options.slot_wait
    return min(launch_slot.wait_policy()[0] if options.slot_wait is None else options.slot_wait, time_left(options.left, 0.0))


def slot_failure(left: Optional[Callable[[], float]], kind: str) -> str:
    """起動の前置きで起動しなかった種類。期限を使い切ってスロットが取れなければ `timeout`（`claude.Failure` の値）。"""
    return "timeout" if kind == launch_slot.NoSlot.kind and spent(left) is not None else kind


def require_claude(run: PrecheckRun, cwd: Optional[Path], check: bool, waited: Optional[list[bool]] = None) -> Optional[Stop]:
    """CLI の下限を確かめる。古ければ `Check.FLOOR` の `Stop`、下限を満たすか `check` が偽（確かめない）なら None。

    `waited` を渡すと、確かめの間に待ったか（`claude --version` の終わりか、別の起動の `--version` を待つ錠。`cli_floor.require`）を 1 つ足す。
    """
    try:
        got = cli_floor.require("claude", run.argv[0], run.env, cwd) if check else False
    except cli_floor.TooOld as exc:
        return Stop(Check.FLOOR, str(exc))
    if waited is not None:
        waited.append(bool(got))
    return None


def precheck_claude(run: PrecheckRun, cwd: Optional[Path], options: PrecheckOptions) -> Optional[Stop]:
    """起動の直前の確かめ。起動の期限（`options.left`）の残りが無ければ、ほかを確かめず `Check.DEADLINE`（#2944）。`run.recheck` が 1 行を返せば `Check.RECHECK`、次に `options.verify` が 1 行を返せば
    `Check.VERIFY`、次に CLI の下限（`require_claude`）。下限の確かめで待った起動（自分か別の起動の `claude --version` の終わりを待った）は、
    本体を起動する直前に `options.verify` をもう一度打つ（待たずに記録を再利用した起動は 1 回で済ませる）。
    最後に期限の残りをもう一度測り（確かめの待ちで使い切ることがある）、無ければ `Check.DEADLINE`。
    すべて通れば `options.ready` を打って None。

    照らし直しは子プロセスを起動しないので、`claude --version` を打つ下限より前に打つ（取り消しで止まる起動に `--version` を打たない）。
    下限の確かめは、キャッシュが無ければ `claude --version` の終了を待つ。その間に承認が取り消されても取り消した許可で起動しないよう、
    下限の後にも照らし直す。起動の準備（`ready`）は確かめをすべて通った後に打つので、どの確かめで止まった起動でも準備をしない。
    """
    if (late := spent(options.left)) is not None:
        return Stop(Check.DEADLINE, late)
    refused = run.recheck() if run.recheck is not None else None
    if refused is not None:
        return Stop(Check.RECHECK, refused)
    changed = options.verify() if options.verify is not None else None
    if changed is not None:
        return Stop(Check.VERIFY, changed)
    waited: list[bool] = []
    too_old = require_claude(run, cwd, options.cli_floor, waited)
    if too_old is not None:
        return too_old
    changed = options.verify() if options.verify is not None and any(waited) else None
    if changed is not None:
        return Stop(Check.VERIFY, changed)
    if (late := spent(options.left)) is not None:
        return Stop(Check.DEADLINE, late)
    if options.ready is not None:
        options.ready()
    return None
