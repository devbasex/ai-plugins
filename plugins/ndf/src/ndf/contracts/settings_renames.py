"""設定のキーの改名表（`contracts/data/ndf-settings-renames.json`）の行の振り分け（#3613・#3672）。

改名表は 1 つで、2 つの読み手が行を分けて読む。同じ判定を読み手ごとに書かないため、振り分けをここに置く。

- ブランチ戦略のセクション（`branch_strategy`）へ移す行と、移した後に消す行（`new` が null）: 解析の
  `ndf project branch-strategy migrate` と旧い設定の検査（`ndf.core.branch_strategy_legacy`）
- 残りの行（同じファイルの中でキーの名前と値を改める行）: `ndf settings migrate` と設定を読む側の止める検査
  （`ndf.domains.settings.renames`）
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ndf import contracts, identity

# 改名表（コントラクトの保存先からの相対パス）
TABLE = "data/ndf-settings-renames.json"
# ブランチ戦略のセクションの名前（`.ndf/project.json` の最上位のキー）
SECTION = identity.BRANCH_STRATEGY_SECTION


def removes(row: Mapping[str, Any]) -> bool:
    """移した後に消す旧いキーの行（`new` が null）。"""
    return row.get("new") is None


def moves_into_section(row: Mapping[str, Any]) -> bool:
    """`.ndf/project.json` のセクション `branch_strategy` の下へ移す行。"""
    new = row.get("new")
    return row.get("new_file") == "project" and isinstance(new, str) and (new == f"/{SECTION}" or new.startswith(f"/{SECTION}/"))


def renames_in_place(row: Mapping[str, Any]) -> bool:
    """`ndf settings migrate` が扱う行（ブランチ戦略のセクションへ移す行と消す行を除く）。"""
    return not removes(row) and not moves_into_section(row)


def rows(error_type: type[contracts.ContractError], rel: str = TABLE) -> list:
    """改名表の行の並び。読めなければ `error_type`、`rows` が無ければ `KeyError`、配列でなければ `TypeError`。

    `rel` はコントラクトの保存先からの相対パス。形の誤りの文は読み手ごとに違うため、読み手が受けて組む。
    """
    data = contracts.load_as(rel, error_type)
    found = data["rows"] if isinstance(data, dict) else None
    if not isinstance(found, list):
        raise TypeError("rows が配列でない")
    return found
