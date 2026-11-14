"""配る Skill の表（`manifests/skills.json`）の読み手。

表は次の 2 つを持つ。

- `runtimes`: ランタイムごとに、Skill の並びを書く定義（プラグインの根からの相対パス。null はそのランタイムが
  `skills/` をすべて読み、並びを書かない）と、Skill ごとの暗黙の起動を止める policy のファイル（Skill の置き場からの
  相対パス。null は書かない）
- `skills`: Skill ごとに、名前（`skills/<名前>/SKILL.md`）・配るランタイム・すべてのランタイムへは配らない理由（`why`）

`skills/` をすべて読むランタイムは、表のどの行にも入る（外せない）。配るランタイムが表の `runtimes` のすべてでない
行は `why` を持ち、すべての行は `why` を持たない。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, NamedTuple, Optional

TABLE_REL = Path("manifests") / "skills.json"


class TableError(Exception):
    """表が無い・読めない・形が違う。"""


class Runtime(NamedTuple):
    name: str
    definition: Optional[str]
    policy: Optional[str]


class Skill(NamedTuple):
    name: str
    runtimes: tuple[str, ...]
    why: str


class Table(NamedTuple):
    runtimes: tuple[Runtime, ...]
    skills: tuple[Skill, ...]

    def for_runtime(self, runtime: str) -> list[str]:
        """ランタイムへ配る Skill の名前（表の並び）。"""
        return [s.name for s in self.skills if runtime in s.runtimes]


def load(plugin_root: Path) -> Table:
    """表。形が違えば `TableError`。"""
    data = _read(plugin_root / TABLE_REL)
    runtimes = tuple(_runtime(name, row) for name, row in data["runtimes"].items())
    reads_all = {r.name for r in runtimes if r.definition is None}
    skills = tuple(_skill(row, {r.name for r in runtimes}, reads_all) for row in data["skills"])
    _reject_duplicates(skills)
    return Table(runtimes, skills)


def _read(path: Path) -> dict:
    """表の JSON。読めない・最上位の形（runtimes・skills）が違えば `TableError`。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TableError(f"配る Skill の表を読めない: {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("runtimes"), dict) or not isinstance(data.get("skills"), list):
        raise TableError(f"配る Skill の表の形が違う（runtimes・skills）: {path}")
    return data


def _reject_duplicates(skills: tuple[Skill, ...]) -> None:
    """同じ名前の行が 2 回以上あれば `TableError`。"""
    names = [s.name for s in skills]
    twice = sorted({n for n in names if names.count(n) > 1})
    if twice:
        raise TableError(f"配る Skill の表に同じ名前が 2 回ある: {', '.join(twice)}")


def _runtime(name: str, row: Any) -> Runtime:
    if not isinstance(row, dict) or not {"definition", "policy"} <= set(row):
        raise TableError(f"配る Skill の表のランタイム {name} の形が違う（definition・policy）: {row}")
    if not all(row[k] is None or isinstance(row[k], str) for k in ("definition", "policy")):
        raise TableError(f"配る Skill の表のランタイム {name} の definition と policy は相対パスか null: {row}")
    return Runtime(name, row["definition"], row["policy"])


def _skill(row: Any, known: set[str], reads_all: set[str]) -> Skill:
    if not isinstance(row, dict) or not isinstance(row.get("name"), str) or not isinstance(row.get("runtimes"), list):
        raise TableError(f"配る Skill の表の行の形が違う（name・runtimes）: {row}")
    name, runtimes = row["name"], tuple(row["runtimes"])
    why = row["why"] if "why" in row else ""
    _check_name(name)
    _check_runtimes(name, runtimes, why, known, reads_all)
    return Skill(name, runtimes, why)


def _check_name(name: str) -> None:
    if not name or "/" in name or name.startswith("."):
        raise TableError(f"配る Skill の表の名前が違う: {name!r}")


def _check_runtimes(name: str, runtimes: tuple[str, ...], why: str, known: set[str], reads_all: set[str]) -> None:
    """配るランタイムが表にあり、skills/ をすべて読むランタイムを含み、why がすべてへは配らない行だけにあること。"""
    if not set(runtimes) <= known:
        raise TableError(f"{name} の runtimes に表の runtimes（{', '.join(sorted(known))}）に無いものがある: {runtimes}")
    if not reads_all <= set(runtimes):
        raise TableError(f"{name} の runtimes は skills/ をすべて読むランタイム（{', '.join(sorted(reads_all))}）を含む")
    if (set(runtimes) != known) != bool(why):
        raise TableError(f"{name} の why は、すべてのランタイムへは配らない行だけが持つ")
