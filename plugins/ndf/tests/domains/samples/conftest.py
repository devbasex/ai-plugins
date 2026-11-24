from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from contract_files import copy_contract_files

from ndf import contracts
from ndf.contracts import limits
from ndf.domains.samples import verify

from ._world import World


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    """見本の定義が 0 件のコントラクト（突き合わせが読むファイルだけのコピー）と、空の見本の保存先。"""
    root = tmp_path / "plugin"
    (root / "contracts" / verify.DEFINITIONS).mkdir(parents=True)
    copy_contract_files(
        root / "contracts",
        [f"{limits.NDF_SETTINGS}/{verify.FETCH_POLICY}.json", limits.SCHEMA, verify.DEFINITION_SCHEMA],
    )
    monkeypatch.setattr(verify, "ROOT", root)
    yield World(root)
    contracts.use_dir(None)
