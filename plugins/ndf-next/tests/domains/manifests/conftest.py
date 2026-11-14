"""manifests のテストの前提。"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from ndf.domains.manifests import table

from ._manifests import ROOT


@pytest.fixture
def plugin(tmp_path) -> Path:
    """表と Codex の定義だけを写したプラグインの根。"""
    root = tmp_path / "plugin"
    for rel in (str(table.TABLE_REL), ".codex-plugin/plugin.json"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, root / rel)
    return root
