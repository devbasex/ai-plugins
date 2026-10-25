"""スプリントの状態ファイルの絶対パス（sprint.py と sprint_waves.py が共有する）。"""

from __future__ import annotations

from pathlib import Path


def sprint_state_path(a) -> str:
    return str(Path(a.state).resolve())
