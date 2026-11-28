"""中継のラッパー: 外側の端末の大きさを子の擬似端末へ合わせる。

子の擬似端末の大きさは起動した時の値のまま変わらないので、ラッパーが SIGWINCH を受けて写し直す。大きさが変われば
カーネルが子へ SIGWINCH を送る（同じ値を写しても送られない）。
"""

from __future__ import annotations

import contextlib
import os
import signal
import sys
import threading
from typing import Any, Callable, Iterator, Optional


def dims() -> Optional[tuple[int, int]]:
    """標準出力の端末の（行, 列）。端末でない・大きさが 0（大きさを持たない擬似端末）なら None。"""
    try:
        size = os.get_terminal_size(sys.stdout.fileno())
    except (OSError, ValueError):  # noqa: NDF401 ファイル: 標準出力が端末でない・閉じていれば大きさは無い
        return None
    return (size.lines, size.columns) if size.lines and size.columns else None


def fit(child: Any) -> None:
    """子の擬似端末を今の端末の大きさに合わせる。子が無い・端末でない・子の擬似端末が閉じていれば何もしない。

    シグナルハンドラからも呼ぶので、子の生き死には読まない（`isalive` は子を回収して、割り込んだ側の見回りと競う）。
    """
    size = dims()
    if child is None or size is None:
        return
    try:
        child.setwinsize(*size)
    except (OSError, ValueError):  # noqa: S110 ファイル: 閉じた擬似端末の口は ioctl が失敗する
        pass


@contextlib.contextmanager
def following(current: Callable[[], Any]) -> Iterator[None]:
    """with の間、SIGWINCH のたびに `current()` の返す子（無ければ None）を合わせる。抜けるとき前のハンドラへ戻す。

    シグナルハンドラはメインスレッドでしか登録できないので、ほかのスレッドでは何もしない。見回りの `select` は
    割り込まれても残りの時間を待ち直す（PEP 475）。起動の途中の変化はハンドラの相手がまだ新しい子でないので、
    起動した側が起動の後に `fit` を呼ぶ。
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.signal(signal.SIGWINCH, lambda *_: fit(current()))
    try:
        yield
    finally:
        signal.signal(signal.SIGWINCH, signal.SIG_DFL if previous is None else previous)
