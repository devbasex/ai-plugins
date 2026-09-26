"""監視する CLI の PID とプロセスグループ（#1142 の C3 で `monitor.py` から分けた）。

pid ファイルの読み取り・生死とゾンビの判定・`/proc/<pid>/cmdline` の照合・停止（SIGTERM の後に SIGKILL）を持つ。
標準ライブラリだけを import する。
"""
from __future__ import annotations

import os
import pathlib
import signal
import time
from typing import Optional


def _read_pidfile(p: pathlib.Path) -> Optional[int]:
    try:
        s = p.read_text().strip()
        return int(s) if s else None
    except (FileNotFoundError, ValueError):
        return None


def _proc_state(pid: int) -> Optional[str]:
    """`/proc/<pid>/status` の State 行の値。読めない・State 行が無いときは None。"""
    try:
        status_text = pathlib.Path(f"/proc/{pid}/status").read_text()
    except (FileNotFoundError, PermissionError, OSError):
        return None
    for line in status_text.splitlines():
        if line.startswith("State:"):
            return line[len("State:"):]
    return None


def _pid_alive(pid: int) -> bool:
    """`kill -0` + ゾンビ検出。

    `os.kill(pid, 0)` はゾンビプロセスに対しても成功する (PID エントリが
    残っているため)。Docker without `--init` 環境では orphan プロセスが
    ゾンビ化して永久に残るため、`/proc/<pid>/status` で State: Z を検出する。
    """
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False
    state = _proc_state(pid)
    return state is None or "Z" not in state


def _is_zombie(pid: int) -> bool:
    """PID がゾンビかどうか。_pid_alive() とは独立に呼べるユーティリティ。"""
    state = _proc_state(pid)
    return state is not None and "Z" in state


def _leads_own_group(pid: int) -> bool:
    """pid がプロセスグループの先頭で、かつ監視自身のグループではないか。

    `launch-cli.sh` は `set -m` で起動するため CLI の pid = pgid になる。そうでない pid
    （古い起動の手順・別の経路）は先頭でないか、監視と同じグループに居る。**監視自身の
    グループへ送ると、進行側のシェルまで止まる**（#584 の候補で退けた形）。
    """
    try:
        pgid = os.getpgid(pid)
    except OSError:
        return False
    return pgid == pid and pgid != os.getpgrp()


def _kill_pid(pid: int, sigterm_grace: float = 3.0) -> None:
    """対象プロセスに SIGTERM、`sigterm_grace` 秒後も生きていたら SIGKILL。

    TIMEOUT / STALLED / EARLY_ERROR で監視を打ち切るとき、対象プロセスが残ったまま
    だと後から `gh api` 投稿や result.json 書き込みを実行してメインフローと
    競合する。失敗扱いで返るときは必ず停止させる。
    ゾンビプロセスにはシグナルを送れないためスキップする。

    対象がプロセスグループの先頭なら **グループへ送る**（#584 / #729 の決定 10）。pid だけへ
    送ると、CLI の子プロセスが残って止めた後に結果ファイルを書く。生存の確認は先頭の pid で見る。
    """
    if pid <= 0:
        return
    if _is_zombie(pid):
        return
    send = (lambda sig: os.killpg(pid, sig)) if _leads_own_group(pid) else (
        lambda sig: os.kill(pid, sig))
    try:
        send(signal.SIGTERM)
    except OSError:
        return
    deadline = time.monotonic() + sigterm_grace
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return
        time.sleep(0.5)
    try:
        send(signal.SIGKILL)
    except OSError:
        pass


def _pid_cmdline_matches(pid: int, expected: str) -> Optional[bool]:
    """`/proc/<pid>/cmdline` を読んで `expected` を含むか。

    /proc が読めない環境では None を返す（PID 再利用チェック非対応）。
    """
    try:
        cmdline = pathlib.Path(f"/proc/{pid}/cmdline").read_text()
        return expected.lower() in cmdline.lower()
    except (FileNotFoundError, PermissionError, OSError):
        return None
