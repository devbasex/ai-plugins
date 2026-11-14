"""配る Skill の表（`manifests/skills.json`）の読み手。

表の行は Skill の名前（`skills/<名前>/SKILL.md`）・配るランタイム・すべてのランタイムへは配らない理由（`why`）を持つ。

- Claude Code は `skills/` をすべて読む（定義に Skill の並びを書かない）ので、どの行も `claude` を含む
- 配るランタイムが表の `runtimes` のすべてでない行は `why` を持つ。すべての行は `why` を持たない
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, NamedTuple

TABLE_REL = Path("manifests") / "skills.json"
# skills/ をすべて読むランタイム（定義に並びを書かない）
READS_ALL = "claude"


class TableError(Exception):
    """表が無い・読めない・形が違う。"""


class Skill(NamedTuple):
    name: str
    runtimes: tuple[str, ...]
    why: str


def load(plugin_root: Path) -> tuple[Skill, ...]:
    """表の行。形が違えば `TableError`。"""
    path = plugin_root / TABLE_REL
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TableError(f"配る Skill の表を読めない: {path}: {exc}") from exc
    known = tuple(data.get("runtimes") or ())
    if READS_ALL not in known:
        raise TableError(f"配る Skill の表の runtimes に {READS_ALL} が無い: {path}")
    rows = tuple(_row(r, known) for r in data.get("skills") or ())
    names = [r.name for r in rows]
    twice = sorted({n for n in names if names.count(n) > 1})
    if twice:
        raise TableError(f"配る Skill の表に同じ名前が 2 回ある: {', '.join(twice)}")
    return rows


def _row(row: Any, known: tuple[str, ...]) -> Skill:
    if not isinstance(row, dict) or not isinstance(row.get("name"), str) or not isinstance(row.get("runtimes"), list):
        raise TableError(f"配る Skill の表の行の形が違う（name・runtimes）: {row}")
    name, runtimes, why = row["name"], tuple(row["runtimes"]), row.get("why", "")
    if not name or "/" in name or name.startswith("."):
        raise TableError(f"配る Skill の表の名前が違う: {name!r}")
    if not set(runtimes) <= set(known) or READS_ALL not in runtimes:
        raise TableError(f"{name} の runtimes は {READS_ALL} を含み、表の runtimes（{', '.join(known)}）の中だけにする: {runtimes}")
    if (set(runtimes) != set(known)) != bool(why):
        raise TableError(f"{name} の why は、すべてのランタイムへは配らない行だけが持つ")
    return Skill(name, runtimes, why)


def for_runtime(rows: tuple[Skill, ...], runtime: str) -> list[str]:
    """ランタイムへ配る Skill の名前（表の並び）。"""
    return [r.name for r in rows if runtime in r.runtimes]
