"""claude -p などの子プロセスの待ち: 待ちの間の進行の書き込み・打ち切り・プロセスグループごとの停止（#2855 で claude から分けた）。

`supervise_lib.claude` が同じ名前で公開し直すので、呼び出し元は今までどおり `supervise_lib.claude` から import してよい。
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

import limits
import procs

TICK = 5.0  # 子プロセスの待ちを区切って見る秒数の上限


def kill_group(p: subprocess.Popen) -> None:
    """子をプロセスグループごと止める（shell=True のステップの孫も残さない。lib/procs.py）。止まらなければ 5 秒で見切る。"""
    procs.stop_tree(p.pid, grace=0)
    # 先頭が先に終わった（ゾンビの）グループの残りを procs.stop_tree は止めない。孫を残さないためグループへも送る
    with contextlib.suppress(OSError):
        os.killpg(p.pid, signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired, ValueError, OSError):
        p.communicate(timeout=limits.seconds("supervise.claude-reap"))


_STEP = threading.local()  # このスレッドで起こす子を知らせる先（RunState.note_child。耐久ステップを流すスレッドごと）


def watch_children(note) -> None:
    """このスレッドで `run_ticking` が起こす子を `note(pid, 起きたか)` へ知らせる。None で外す（孤児の片付け）。"""
    _STEP.note = note


def run_ticking(
    cmd, tick=None, every: float = TICK, timeout: float | None = None, input: str | None = None, err_path: Path | None = None, **kw
) -> subprocess.CompletedProcess:
    """subprocess.run と同じく待つが、every 秒ごとに tick() を呼ぶ（長いステップの待ちの中で進行を書く）。

    err_path を渡すと stderr をそのファイルへ書かせる（待ちの間に最後の行を読めるように）。
    子は新しいセッションで起こし、`watch_children` の知らせる先へ渡す（孤児の片付け）。打ち切りは子のプロセスグループを止めて subprocess.TimeoutExpired を投げる。
    tick() が例外を投げたら（遅れの見張りの打ち切り）、子のプロセスグループを止めてから投げ直す。
    kw の extend(開始の時刻) は待ちの周期ごとに呼び、返した時刻が締め切りより後なら後ろへずらす（#2047 の I7）。kw の follow が真なら前へも動かす（#2505）。"""
    extend, follow = kw.pop("extend", None), kw.pop("follow", False)
    with open(err_path, "w", encoding="utf-8") if err_path else contextlib.nullcontext() as errf:  # 閉じ済みでも閉じ直してよい
        p = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=errf or subprocess.PIPE,
            text=True,
            start_new_session=True,
            **kw,
        )
        note = getattr(_STEP, "note", None)
        if note:
            note(p.pid, True)
        deadline = time.time() + timeout if timeout else None
        try:
            return _communicate_with_ticks(p, errf, err_path, every, deadline, input, tick, cmd, timeout, extend, follow)
        finally:
            if note:
                note(p.pid, False)


def _communicate_with_ticks(p, errf, err_path, every, deadline, input, tick, cmd, timeout, extend, follow) -> subprocess.CompletedProcess:
    """every 秒ごとに tick() を呼びながら p の終わりを待つ。打ち切りと tick() の例外では子のプロセスグループを止める。"""
    first = True
    while True:
        wait = every if deadline is None else max(0.01, min(every, deadline - time.time()))
        try:
            out, err = p.communicate(input if first else None, timeout=wait)
            if errf:
                errf.close()
                err = Path(err_path).read_text(encoding="utf-8", errors="replace")
            return subprocess.CompletedProcess(cmd, p.returncode, out, err)
        except subprocess.TimeoutExpired:
            first = False
            if extend and deadline is not None and (later := extend(deadline - timeout)) and (follow or later > deadline):
                deadline, timeout = later, timeout + later - deadline
            if deadline is not None and time.time() >= deadline:
                kill_group(p)
                raise subprocess.TimeoutExpired(cmd, timeout) from None
            if tick:
                try:
                    tick()
                except BaseException:
                    kill_group(p)
                    raise
