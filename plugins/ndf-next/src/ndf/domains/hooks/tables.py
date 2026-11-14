"""hook が読む契約の表（`contracts/limits.json` と `contracts/hooks/*.json`）の読み込み。

hook の経路では `ndf.contracts`（jsonschema を読む）を import しないため、表は `json` で直に読む。表の形は CI の
テストが確かめる。置き場はこのファイルの位置だけから求める（ほかのプラグインの `CLAUDE_PLUGIN_ROOT` を読まない）。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

# このファイルから 4 つ上がプラグインの根（src/ndf/domains/hooks/tables.py）
PLUGIN_ROOT = Path(__file__).resolve().parents[4]
CONTRACTS = PLUGIN_ROOT / "contracts"


class TableError(Exception):
    """契約の表が無い・読めない・引いた行が無い。"""


@lru_cache(maxsize=None)
def load(rel: str) -> Any:
    """`contracts/` からの相対パスの表を 1 プロセスに 1 回読む。"""
    path = CONTRACTS / rel
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TableError(f"契約の表を読めない: {path}: {exc}") from exc


def policies() -> dict[str, dict[str, Any]]:
    """上限の宣言の方針の表（名前 → 値）。"""
    return load("limits.json")["policies"]


def policy_value(name: str, key: str) -> Any:
    """方針の 1 つの値。無ければ `TableError`。"""
    row = policies().get(name)
    if row is None or key not in row:
        raise TableError(f"上限の宣言の方針 {name} に {key} が無い")
    return row[key]


def guards() -> list[dict[str, Any]]:
    return load("hooks/guards.json")["guards"]


def runtimes() -> dict[str, dict[str, Any]]:
    return load("hooks/runtimes.json")["runtimes"]


def event_spec(runtime: str, event: str) -> dict[str, Any] | None:
    """ランタイムの事象の契約。表に無ければ None。"""
    rt = runtimes().get(runtime)
    return rt["events"].get(event) if rt else None
