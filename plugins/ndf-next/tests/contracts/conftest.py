from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Callable

import pytest

from ndf import contracts


@pytest.fixture(autouse=True)
def _plugin_contracts():
    """各テストはプラグインの契約から始め、差し替えを次のテストへ残さない。"""
    contracts.use_dir(None)
    yield
    contracts.use_dir(None)


class ContractsCopy:
    """一時の置き場へ写した契約（`root`）。呼ぶと 1 ファイルを書き換え、読み先をこの置き場にし直す。"""

    def __init__(self, root: Path) -> None:
        self.root = root

    def __call__(self, rel: str, fn: Callable[[Any], Any] | None = None, *, text: str | None = None) -> Path:
        path = self.root / rel
        if text is not None:
            path.write_text(text, encoding="utf-8")
        else:
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
            path.write_text(json.dumps(fn(data), ensure_ascii=False), encoding="utf-8")
        contracts.use_dir(self.root)
        return path


@pytest.fixture
def contracts_copy(tmp_path: Path) -> ContractsCopy:
    """プラグインの契約を一時の置き場へ写して読み先にし、`contracts_copy(rel, fn)` で 1 ファイルを書き換える。"""
    root = tmp_path / "contracts"
    shutil.copytree(contracts.DEFAULT_DIR, root)
    contracts.use_dir(root)
    return ContractsCopy(root)


@pytest.fixture
def fact_table() -> Callable[..., dict[str, Any]]:
    """事実の表の雛形（version・summary・owner）に列の宣言と行を入れて返す。"""

    def make(columns: dict[str, Any], rows: list[dict[str, Any]], *, summary: str = "s", owner: str = "o") -> dict[str, Any]:
        return {"version": 1, "summary": summary, "owner": owner, "columns": columns, "rows": rows}

    return make
