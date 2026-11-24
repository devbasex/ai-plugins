"""配る構成の設定（`contracts/data/toolchain.json`）の読み手（#3687 の決定 3）。

NDF 自身が動く条件（Python の下限・hook のシステムの python3 の下限・uv のバージョンとインストーラの sha256）を持つ。CI が何で回すかは
リポジトリの設定（`.ndf/project.json` の `toolchain`）が持ち、検査（`ndf.checks.toolchain`）が両方を突き合わせる。
hook の経路はこのモジュールを import せず、同じ表を標準ライブラリで読む（`ndf.domains.hooks.boot`）。
"""

from __future__ import annotations

from typing import Any

from ndf import contracts

TABLE = "data/toolchain.json"
SCHEMA = contracts.schema_path("toolchain")
# 読んだ値の型（スキーマで確かめた辞書）
Toolchain = dict[str, Any]


class ToolchainError(contracts.ContractError):
    """配る構成の設定が無い・形が違う。"""


def version_tuple(text: str) -> tuple[int, int]:
    """`3.11` → `(3, 11)`（形はスキーマが `<数>.<数>` に縛る）。"""
    major, minor = text.split(".")
    return int(major), int(minor)


@contracts.cached_reader
def table() -> Toolchain:
    """表を 1 プロセスに 1 回読み、スキーマで確かめた値を返す。"""
    raw = contracts.load_as(TABLE, ToolchainError)
    contracts.require_schema(raw, SCHEMA, ToolchainError, f"配る構成の設定 {TABLE}")
    return raw


def python_floor() -> tuple[int, int]:
    """NDF が動く Python の下限。"""
    return version_tuple(table()["python"]["floor"])


def hook_floor() -> tuple[int, int]:
    """依存の環境が無いときに hook のエントリポイントが動くシステムの python3 の下限。"""
    return version_tuple(table()["python"]["hook_floor"])


def uv_version() -> str:
    """起動が入れる uv のバージョン。"""
    return table()["uv"]["version"]


def uv_installer_sha256() -> str:
    """uv のインストーラの sha256。"""
    return table()["uv"]["installer_sha256"]
