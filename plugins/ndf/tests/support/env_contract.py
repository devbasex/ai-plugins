"""環境変数のコントラクト（`contracts/domains/env.json`）を、テストの間だけ一時の根の中身に差し替える（#4126）。"""

from __future__ import annotations

import json
import shutil
from functools import lru_cache
from pathlib import Path

import pytest

from ndf import identity

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
ENV_JSON = Path("contracts") / "domains" / "env.json"
PLUGIN_JSON = Path(".claude-plugin") / "plugin.json"


def use_env_contract(monkeypatch: pytest.MonkeyPatch, root: Path, text: str | None) -> None:
    """`text` を環境変数のコントラクトに持つ根（None ならコントラクトの無い根）を指し、読み手のキャッシュを空から始める。"""
    (root / PLUGIN_JSON).parent.mkdir(parents=True)
    shutil.copy(PLUGIN_ROOT / PLUGIN_JSON, root / PLUGIN_JSON)
    if text is not None:
        (root / ENV_JSON).parent.mkdir(parents=True)
        (root / ENV_JSON).write_text(text, encoding="utf-8")
    monkeypatch.setattr(identity, "ROOT", root)
    monkeypatch.setattr(identity, "_env_keys", lru_cache(maxsize=1)(identity._env_keys.__wrapped__))


def without_key(key: str) -> str:
    """製品のコントラクトから、`key` を持つ行のキーを外した中身。"""
    data = json.loads((PLUGIN_ROOT / ENV_JSON).read_text(encoding="utf-8"))
    for row in data["rows"]:
        if row.get("key") == key:
            del row["key"]
    return json.dumps(data)
