"""親のステップの締め切りを、子プロセスの上限の分だけ後ろへずらす（#2457 決定 8・#2047 決定 4）。

検査のプランの `refactor` のステップは、実装の予算と安全網から導いた `timeout` を持つ。テストの実行・CI の待ちは
その外にあるため、駆動と `refactor.py` は子プロセスを起動する前に「ステップの開始からの経過 + その子プロセスの上限」を
ステップの延長のファイルへ書く。延長は締め切りを後ろへずらすだけで、前へずらさない（`drive_pause.write_extension`）。

ステップの開始の時刻は駆動が環境変数 `CROSS_REFACTORING_STEP_STARTED`（UNIX 時刻の秒）で子へ渡す。無ければ
このプロセスの開始を使う。エンジンの外で打ったとき（延長のファイルが無い）は何もしない。
"""

from __future__ import annotations

import os
import time

import drive_pause

STARTED_ENV = "CROSS_REFACTORING_STEP_STARTED"
_PROCESS_STARTED = time.time()


def started_at() -> float:
    """ステップの開始（UNIX 時刻の秒）。"""
    raw = os.environ.get(STARTED_ENV)
    try:
        return float(raw) if raw else _PROCESS_STARTED
    except ValueError:
        return _PROCESS_STARTED


def ahead(seconds: float, reason: str) -> bool:
    """今から `seconds` 秒の子プロセスがステップの締め切りで切られないよう、延長を書く。書かなければ False。"""
    if not os.environ.get(drive_pause.EXTEND_ENV) or seconds is None or float(seconds) <= 0:
        return False
    elapsed = max(0.0, time.time() - started_at())
    return drive_pause.write_extension(elapsed + float(seconds), reason)
