"""配るコントラクトのうち、テストが読むファイルだけのコピー。全体のコピーは数百ファイルになるので、読むファイルが決まるテストはこちらを使う。"""

from __future__ import annotations

import shutil
from collections.abc import Iterable
from pathlib import Path

from ndf import contracts


def copy_contract_files(root: Path, rels: Iterable[str]) -> None:
    """`rels`（`contracts/` からの相対パス）のファイルだけを `root` へコピーし、`root` を読み先にする。"""
    for rel in rels:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(contracts.DEFAULT_DIR / rel, target)
    contracts.use_dir(root)
