"""旧い設定の検査（#3672 の決定 10・I4・I8）。

ブランチ戦略・マージ・リリースの旧いキー（`.ndf/worktree.json`・`.ndf/project.json`・`.ndf/supervise.json`・
`.ndf/release.json` の 4 か所）の位置を、移す行の表（`contracts/data/ndf-settings-renames.json`）から探す。旧いキーの
名前をコードに書かず、表だけを読む。旧いキーを新しいキーの別名として読む形は持たない（後方互換は要らない）。

表の SSOT は 11.0 のこのプラグインのコントラクトで、10 系の `scripts/data/ndf-settings-renames.json` はコピー
（`scripts/check-ndf-contracts.py` が一致を確かめる）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ndf import contracts, identity
from ndf.contracts import facts, settings_renames
from ndf.core import json_pointer

MOVES = settings_renames.TABLE
SECTION = settings_renames.SECTION
# 利用先のリポジトリの設定の保存先（`.ndf`）
DECL_DIR = identity.FIXED_WORDS["ndf_settings_dir"]
# 表の file（ndf-files の ID）→ `.ndf/` の下のファイル名（表 ndf-files の path から引く）
FILES = {id: facts.value("ndf-files", id, "path") for id in ("project", "worktree", "supervise", "release")}
# 系ごとの解析のサブコマンド（作成・移行のコマンドの文）
COMMAND = {"10": 'python3 "$SCRIPTS/project-decl.py" branch-strategy', "11": "ndf project branch-strategy"}


class LegacyTableError(contracts.ContractError):
    """移す行の表が無い・形が違う（導入の破損）。"""


@dataclass(frozen=True)
class Move:
    """移す行の 1 つ。`new` が None の行は、移した後に消す旧いキー（`remove` の行）。"""

    id: str
    file: str
    old: str
    new_file: Optional[str]
    new: Optional[str]
    values: dict = field(default_factory=dict)
    transform: Optional[str] = None


@dataclass(frozen=True)
class Found:
    """見つけた旧いキーの位置（`.ndf/` の下のファイル名と JSON Pointer）。"""

    file: str
    pointer: str


def _concerns_section(row: dict) -> bool:
    if settings_renames.removes(row):
        return row.get("file") in FILES
    return settings_renames.moves_into_section(row)


def load_moves(path: Optional[str] = None) -> list[Move]:
    """表のうち、セクション `branch_strategy` へ移す行と、旧い 4 か所の `remove` の行だけを返す。

    `path` はコントラクトの保存先（`contracts/`）からの相対パス。省けば移す行の表。"""
    rel = path or MOVES
    try:
        return [
            Move(
                r["id"],
                r["file"],
                r["old"],
                r.get("new_file"),
                r.get("new"),
                dict(r["values"]) if r.get("values") else {},
                r.get("transform"),
            )
            for r in settings_renames.rows(LegacyTableError, rel)
            if _concerns_section(r)
        ]
    except (KeyError, TypeError, AttributeError) as exc:
        raise LegacyTableError(f"{rel}: 移す行の表を読めない（{exc}）") from None


def find_legacy(files: dict[str, dict], moves: list[Move]) -> list[Found]:
    """読んだ `.ndf/` の中身（ID → 中身。無いファイルは入れない）から旧いキーの位置を探す。

    親のキー（`remove` の行）と子のキーが両方あるときは、子の位置だけを並べる（同じキーを 2 度出さない）。
    """
    hits = [Found(FILES[m.file], m.old) for m in moves if isinstance(files.get(m.file), dict) and json_pointer.has(files[m.file], m.old)]
    return [h for h in hits if not any(o.file == h.file and o.pointer.startswith(h.pointer + "/") for o in hits)]


def legacy_reason(found: list[Found], system: str) -> str:
    """停止理由の文（ファイル・キーの位置・移行のコマンド）。"""
    where = "、".join(f"`{DECL_DIR}/{f.file}` の `{f.pointer}`" for f in found)
    return f"{where} は旧い設定のキーです。`{COMMAND[system]} migrate` で移してください"
