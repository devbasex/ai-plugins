"""branch_strategy_legacy.py: 旧い宣言の検査（#3672 の決定 10・I4・I8）。

ブランチ戦略・マージ・リリースの旧いキー（`.ndf/worktree.json`・`.ndf/project.json`・`.ndf/supervise.json`・
`.ndf/release.json` の 4 か所）の位置を、移す行の表（`data/ndf-settings-renames.json`）から探す。旧いキーの名前を
コードに書かず、表だけを読む。旧いキーを新しいキーの別名として読む形は持たない（後方互換は要らない）。

旧いキーの名前が残ってよいのは、この検査・移行のコマンド（`project_lib/branch_strategy_migrate.py`）・表とその
テストだけである（`scripts/check-legacy-branch-decl.py` が照らす）。標準ライブラリだけで書く。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data"
MOVES = DATA / "ndf-settings-renames.json"
SECTION = "branch_strategy"
# 表の file（ndf-files の ID）→ `.ndf/` の下のファイル名
FILES = {"project": "project.json", "worktree": "worktree.json", "supervise": "supervise.json", "release": "release.json"}
# 系ごとの解析のサブコマンド（作成・移行のコマンドの文）
COMMAND = {"10": 'python3 "$SCRIPTS/project-decl.py" branch-strategy', "11": "ndf project branch-strategy"}


class LegacyTableError(ValueError):
    """移す行の表が無い・形が違う（導入の破損）。"""


@dataclass(frozen=True)
class Move:
    """移す行の 1 つ。`new` が None の行は、移した後に消す旧いキー（`remove` の行）。"""

    id: str
    file: str
    old: str
    new_file: str | None
    new: str | None
    values: dict = field(default_factory=dict)
    transform: str | None = None


@dataclass(frozen=True)
class Found:
    """見つけた旧いキーの位置（`.ndf/` の下のファイル名と JSON Pointer）。"""

    file: str
    pointer: str


def _concerns_section(row: dict) -> bool:
    new = row.get("new")
    if new is None:
        return row.get("file") in FILES
    return row.get("new_file") == "project" and (new == f"/{SECTION}" or new.startswith(f"/{SECTION}/"))


def load_moves(path: Path | None = None) -> list[Move]:
    """表のうち、セクション `branch_strategy` へ移す行と、旧い 4 か所の `remove` の行だけを返す。"""
    p = path or MOVES
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        rows = data["rows"]
        if not isinstance(rows, list):
            raise TypeError("rows が配列でない")
        moves = []
        for r in rows:
            if not _concerns_section(r):
                continue
            moves.append(
                Move(r["id"], r["file"], r["old"], r.get("new_file"), r.get("new"), dict(r.get("values") or {}), r.get("transform"))
            )
        return moves
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise LegacyTableError(f"{p}: 移す行の表を読めない（{e}）") from e


def _pointer_tokens(pointer: str) -> list[str]:
    return [t.replace("~1", "/").replace("~0", "~") for t in pointer.split("/")[1:]]


def has_pointer(doc, pointer: str) -> bool:
    cur = doc
    for t in _pointer_tokens(pointer):
        if not isinstance(cur, dict) or t not in cur:
            return False
        cur = cur[t]
    return True


def find_legacy(files: dict[str, dict], moves: list[Move]) -> list[Found]:
    """読んだ `.ndf/` の中身（ID → 中身。無いファイルは入れない）から旧いキーの位置を探す。

    親のキー（`remove` の行）と子のキーが両方あるときは、子の位置だけを並べる（同じキーを 2 度出さない）。
    """
    hits: list[Found] = []
    for m in moves:
        doc = files.get(m.file)
        if isinstance(doc, dict) and has_pointer(doc, m.old):
            hits.append(Found(FILES[m.file], m.old))
    out = []
    for h in hits:
        if any(o is not h and o.file == h.file and o.pointer.startswith(h.pointer + "/") for o in hits):
            continue
        out.append(h)
    return out


def legacy_reason(found: list[Found], system: str) -> str:
    """止まる理由の文（ファイル・キーの位置・移行のコマンド）。"""
    where = "、".join(f"`.ndf/{f.file}` の `{f.pointer}`" for f in found)
    return f"{where} は旧い宣言のキーです。`{COMMAND[system]} migrate` で移してください"
