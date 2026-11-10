"""ループの止める指示のファイル（置いた時刻・主体・理由の 1 行の JSON）を書き・読む（#2910）。

sprint_loop_record が正本として再び公開する。書くのは loop stop（sprint_loop.cmd_stop）で、ループの記録は読むだけなので、
持ち主がループの 1 回の途中でも置ける。読むのは境の判定（boundary_stop）。
"""

from __future__ import annotations

import json
from pathlib import Path

STOP_FILE = "stop"
STOP_BY = ("user", "organizer", "unknown")  # 止める指示を置いた主体: 人・オーガナイザー（利用者の指示を受けて）・不明


def stop_order_text(at: str, by: str, reason: str) -> str:
    """止める指示のファイルの中身（1 行の JSON。置いた時刻・主体・理由）。主体が STOP_BY の外なら ValueError。"""
    if by not in STOP_BY:
        raise ValueError(f"止める指示の主体は {'/'.join(STOP_BY)} のどれか: {by!r}")
    return json.dumps({"at": at, "by": by, "reason": reason.strip()}, ensure_ascii=False) + "\n"


def read_stop_order(path: Path) -> dict:
    """止める指示のファイルを読む。主体の記録が無い（時刻だけ・直接置いた・読めない）ものは主体を unknown とし、人の意思として扱わない。"""
    text = path.read_text(encoding="utf-8").strip()
    try:
        got = json.loads(text)
    except ValueError:
        got = None
    if not isinstance(got, dict):
        return {"at": text, "by": "unknown", "reason": ""}
    by = got.get("by") if got.get("by") in STOP_BY else "unknown"
    return {"at": str(got.get("at") or ""), "by": by, "reason": str(got.get("reason") or "")}
