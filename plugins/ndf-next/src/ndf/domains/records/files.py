"""宣言のファイルの置き場を解く（表 ndf-files の行の place から表 places の path を引き、ndf-files の path を継ぐ）。"""

from __future__ import annotations

from pathlib import Path

from ndf.contracts import facts


def relpath(file_id: str) -> str:
    """宣言のファイルのパス（根からの相対。`<places の path>/<ndf-files の path>`）。"""
    place = facts.value("ndf-files", file_id, "place")
    return f"{facts.value('places', place, 'path')}/{facts.value('ndf-files', file_id, 'path')}"


def path(root: Path, file_id: str) -> Path:
    """宣言のファイルのパス（`root` の下）。"""
    return root / relpath(file_id)
