from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from ndf import contracts
from ndf.domains.samples import verify

from ._world import World


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    """見本の定義が 0 件のコントラクトのコピーと、空の見本の保存先。"""
    root = tmp_path / "plugin"
    shutil.copytree(contracts.DEFAULT_DIR, root / "contracts")
    for shipped in (root / "contracts" / verify.DEFINITIONS).iterdir():
        shipped.unlink()
    contracts.use_dir(root / "contracts")
    monkeypatch.setattr(verify, "ROOT", root)
    yield World(root)
    contracts.use_dir(None)
