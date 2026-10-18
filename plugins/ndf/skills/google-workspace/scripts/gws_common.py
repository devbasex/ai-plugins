"""google-workspace のスクリプトが共有する部品（#2065）。

gws の出力の末尾を切り出す関数と、stopped の結果を出して終える関数と、
未認証の終了コード・gws の待ち時間を 1 か所に置く。
"""

from __future__ import annotations

import pathlib
import sys

LIB = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import step_result as sr  # noqa: E402

TAIL_LINES = 20
EXIT_UNAUTHENTICATED = sr.EXIT_GATE + 1
GWS_TIMEOUT = 120


def output_tail(text: str) -> str:
    """出力の末尾の TAIL_LINES 行。"""
    return "\n".join((text or "").strip().splitlines()[-TAIL_LINES:])


def emit_stopped(tool: str, summary: str, code: int, items=None, next_: str | None = None, metrics=None):
    """stopped の結果を 1 行の JSON で出し、code で終える。"""
    sr.emit(sr.result(tool, "stopped", summary, items=items, metrics=metrics, next=next_), code)
