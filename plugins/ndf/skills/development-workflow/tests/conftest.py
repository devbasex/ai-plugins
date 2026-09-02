"""進行の記録のテストのフィクスチャ。

判定は `plugins/ndf/scripts/lib/projects-common.sh` に集約されている。テストはこの層に
対して書き、GitHub への通信は行わない。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from projects_helpers import init_repo



@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """宣言を置く前のリポジトリ。"""
    return init_repo(tmp_path / "main")
