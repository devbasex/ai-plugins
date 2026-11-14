"""中継の印: 中継が子へ入力を送る前に立てる印（#3345 の決定 92）。標準ライブラリだけで書く。

hook のセッションの数え手は、利用者の入力で数え直し、中継が送った入力では数え直さない。中継は入力を送る前に
`mark` で `<状態の置き場>/relay/marks/<セッション>.json` を書き、hook（M3a）は UserPromptSubmit で同じプロセスの
中から `take` を呼んで印を消す（決定 111。依存は hook → 中継の向きで、中継は hook を読まない）。`take` が真なら
その入力は中継が送ったものである。
"""

from __future__ import annotations

import re
from pathlib import Path

from ndf.domains.relay import signals

SESSION_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class MarkError(ValueError):
    """セッションの ID の形が違う。"""


def mark_path(state_dir: Path, session_id: str) -> Path:
    if not SESSION_RE.match(session_id):
        raise MarkError(f"セッションの ID の形が違う: {session_id}")
    return state_dir / "relay" / "marks" / f"{session_id}.json"


def mark(state_dir: Path, session_id: str) -> Path:
    """印を立てる。立てた印のパスを返す。"""
    path = mark_path(state_dir, session_id)
    signals.replace_json(path, {"session_id": session_id, "at": signals.now_iso()})
    return path


def take(state_dir: Path, session_id: str) -> bool:
    """印があれば消して真。無ければ偽（利用者の入力）。"""
    path = mark_path(state_dir, session_id)
    try:
        path.unlink()
    except FileNotFoundError:  # noqa: NDF401 ファイル: 印が無ければ利用者の入力
        return False
    return True
