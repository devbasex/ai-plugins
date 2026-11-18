"""配る Skill の一覧（`manifests/skills.json` と `manifests/<ランタイム>-skills.txt`）を読む規則。

`check-skill-frontmatter.py`・`check-skill-repo-assumptions.py`・`lib/validate_manifests.py` が同じ書式を
読むため、ここに 1 つだけ置く（`scripts/build-runtime-plugins.sh` の manifest 解釈と揃える）。標準ライブラリだけで書く。
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

# 配る Skill の表（11.0.0 からの NDF。行ごとに Skill と配るランタイムを持つ）。プラグインの根からの相対
SKILL_TABLE = "manifests/skills.json"


def plugin_families(root: Path = Path("plugins")) -> list[Path]:
    """`manifests/` と `skills/` を持つプラグインのディレクトリ（plugin family）を名前順に返す。"""
    return [d for d in sorted(root.glob("*")) if (d / "manifests").is_dir() and (d / "skills").is_dir()]


def read_skill_list(path: Path) -> list[str]:
    """`<ランタイム>-skills.txt` の Skill 名の並び。行末の `#` 以降はコメントとして落とし、空の行は読み飛ばす。"""
    names = [line.split("#", 1)[0].strip() for line in path.read_text(encoding="utf-8").splitlines()]
    return [n for n in names if n]


def runtime_skills(plugin_dir: Path, runtimes: Iterable[str] | None = None) -> dict[str, list[str]]:
    """ランタイムごとの配る Skill の名前（ランタイム → 名前の並び）。`SKILL.md` の実在では絞らない。

    表（`manifests/skills.json`）があれば、行の `runtimes` にそのランタイムを持つ Skill を返す。数える
    ランタイムは `runtimes` を渡せばそれ、渡さなければ表の `runtimes` のキー。表が無ければ
    `manifests/<ランタイム>-skills.txt` を読む（`runtimes` を渡せばそのうち在るものだけ、渡さなければ全件を名前順に）。
    """
    manifests = plugin_dir / "manifests"
    table = plugin_dir / SKILL_TABLE
    if table.is_file():
        data = json.loads(table.read_text(encoding="utf-8"))
        keys = data["runtimes"] if runtimes is None else runtimes
        return {runtime: [row["name"] for row in data["skills"] if runtime in row["runtimes"]] for runtime in keys}
    if runtimes is None:
        files = sorted(manifests.glob("*-skills.txt"))
    else:
        files = [f for f in (manifests / f"{runtime}-skills.txt" for runtime in runtimes) if f.exists()]
    return {f.name.removesuffix("-skills.txt"): read_skill_list(f) for f in files}
