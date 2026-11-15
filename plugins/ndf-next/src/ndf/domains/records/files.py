"""宣言のファイルの置き場を解く（表 ndf-files の行の location から表 locations の path を引き、ndf-files の path を継ぐ）。"""

from __future__ import annotations

from pathlib import Path

from ndf.contracts import facts


def relpath(file_id: str) -> str:
    """宣言のファイルのパス（根からの相対。`<locations の path>/<ndf-files の path>`）。"""
    location = facts.value("ndf-files", file_id, "location")
    return f"{facts.value('locations', location, 'path')}/{facts.value('ndf-files', file_id, 'path')}"


def path(root: Path, file_id: str) -> Path:
    """宣言のファイルのパス（`root` の下）。"""
    return root / relpath(file_id)
