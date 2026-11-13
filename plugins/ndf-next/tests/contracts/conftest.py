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


@pytest.fixture
def contracts_copy(tmp_path: Path) -> Callable[..., Path]:
    """プラグインの契約を一時の置き場へ写して読み先にし、`edit(rel, fn)` で 1 ファイルを書き換える。"""
    root = tmp_path / "contracts"
    shutil.copytree(contracts.DEFAULT_DIR, root)
    contracts.use_dir(root)

    def edit(rel: str, fn: Callable[[Any], Any] | None = None, *, text: str | None = None) -> Path:
        path = root / rel
        if text is not None:
            path.write_text(text, encoding="utf-8")
        else:
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
            path.write_text(json.dumps(fn(data), ensure_ascii=False), encoding="utf-8")
        contracts.use_dir(root)
        return path

    edit.root = root  # type: ignore[attr-defined]
    return edit
