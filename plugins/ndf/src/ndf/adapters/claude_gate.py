"""関所で起動した子の関所を開けて claude になったかを確かめ、関所の口を閉じる（#1959 の起動の期限を含む。`claude.py` から分けた）。

子の起動と、止めた子の回収は呼び手の `claude` が持つ（このモジュールは `claude` を読まない。読むと import の輪になる）。
"""

from __future__ import annotations

import errno
import os
import subprocess
from typing import Callable, Optional, Protocol, Sequence

from ndf import process_group
from ndf.adapters import claude_precheck
from ndf.contracts import limits


class GateRun(Protocol):
    """関所を開ける起動（`claude.AccountRun` が満たす）。"""

    @property
    def attach(self) -> Optional[Callable[[int], Optional[str]]]: ...


def close_fds(fds: Sequence[int]) -> None:
    for fd in fds:
        try:
            os.close(fd)
        except OSError:  # noqa: S110 ファイル: 閉じ済みの関所の口は閉じ直さない
            pass


def open_gate(run: GateRun, proc: subprocess.Popen, w: int, sr: int, left: Optional[Callable[[], float]]) -> Optional[str]:
    """`attach` に通り起動の期限が残っていれば関所を開け、起動の知らせを読む。claude になれば None、なれなければ理由の符号。"""
    why = (run.attach(proc.pid) if run.attach is not None else None) or claude_precheck.spent(left)
    if why is not None:
        return why
    os.write(w, b"1")
    got = process_group.read_status(sr, claude_precheck.time_left(left, limits.seconds("relay.gate-exec")))
    if got is None:  # 起動の期限の残りで打ち切ったら、期限切れを理由にする
        return claude_precheck.spent(left) or "gate_timeout"
    return f"launch_failed errno {_errno_name(got)}" if got else None


def _errno_name(got: bytes) -> str:
    """関所が知らせた errno の数値を名前にする（`2` は `ENOENT`）。数値でなければ受けた文字列のまま。"""
    text = got.decode(errors="replace").strip()
    code = int(text) if text.isdigit() else None
    return errno.errorcode[code] if code in errno.errorcode else text
