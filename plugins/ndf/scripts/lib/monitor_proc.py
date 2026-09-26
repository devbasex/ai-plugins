"""監視する CLI の PID とプロセスグループ（#1142 の C3 で `monitor.py` から分けた）。

pid ファイルの読み取りと停止（SIGTERM の後に SIGKILL）を持つ。生死・ゾンビ・起動の引数・グループの先頭の
判定は `lib/procs.py`（psutil の包み）へ渡す（#1142 の D3）。

**`procs` は使う関数の中で import する。** `monitor.py` は `supervise_lib/claude.py` が利用上限の文言の表を読むためにも
import し、そこでは `deps.require("procs")` が呼ばれていない。監視を流すのは `monitor.py` の `main()` で、そこで
`deps.require("procs", "locks")` を呼ぶ。
"""
from __future__ import annotations

import os
import pathlib
import signal
import time
from typing import Optional


def _procs():
    import procs  # deps.require("procs") の後でだけ import できる
    return procs


def _read_pidfile(p: pathlib.Path) -> Optional[int]:
    try:
        s = p.read_text().strip()
        return int(s) if s else None
    except (FileNotFoundError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    """pid が生きているか。ゾンビは死んだとみなす（`os.kill(pid, 0)` はゾンビにも成功するため）。

    Docker を `--init` なしで動かすと、親を失ったプロセスはゾンビのまま残る。
    """
    return _procs().pid_alive(pid)


def _is_zombie(pid: int) -> bool:
    """PID がゾンビかどうか。_pid_alive() とは独立に呼べるユーティリティ。"""
    return _procs().pid_is_zombie(pid)


def _leads_own_group(pid: int) -> bool:
    """pid がプロセスグループの先頭で、かつ監視自身のグループではないか。

    `launch-cli.sh` は `set -m` で起動するため CLI の pid = pgid になる。そうでない pid
    （古い起動の手順・別の経路）は先頭でないか、監視と同じグループに居る。**監視自身の
    グループへ送ると、進行側のシェルまで止まる**（#584 の候補で退けた形）。
    """
    return _procs().leads_own_group(pid)


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
    """pid の起動の引数が `expected` を含むか（大文字と小文字を区別しない）。

    読めない（pid が無い・権限が無い）ときは None を返す（PID 再利用チェック非対応）。
    """
    return _procs().cmdline_contains(pid, expected)
