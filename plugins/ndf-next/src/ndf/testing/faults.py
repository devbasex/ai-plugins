"""障害の注入（#3377。要求の C-6「`kill -9` の後に打ち直すと、落ちたステップから流し直す」の試験の部品）。

テストは子プロセスを `start_new_session=True` で起こし、落としたい時点に来たこと（ファイルが現れた・行が
書かれた）を条件で待ってから、組ごと SIGKILL で落とす。後片付けの猶予を与えない落ち方（電源断・OOM Killer）
の代わりにする。落とした後の打ち直しは、テストが同じコマンドをもう一度起こして確かめる。
"""

from __future__ import annotations

import signal
import subprocess
from collections.abc import Callable

from ndf import process_group
from ndf.testing import waits


def kill_group_when(proc: subprocess.Popen, ready: Callable[[], object], *, what: str, timeout: float = waits.DEFAULT_TIMEOUT) -> int:
    """`ready` が真になるまで待ち、`proc` の組へ SIGKILL を送って回収し、終了コード（負のシグナルの番号）を返す。

    `proc` は `start_new_session=True` で起こしてある。`ready` が成り立つ前に `proc` が終わったら、落とす時点に
    届かなかったとして `AssertionError` を送る。
    """

    def reached() -> object:
        if proc.poll() is not None:
            raise AssertionError(f"{what} の前に子が終わった（終了コード {proc.returncode}）")
        return ready()

    waits.wait_until(reached, timeout=timeout, what=what)
    process_group.signal_group(proc.pid, signal.SIGKILL)
    return proc.wait()
