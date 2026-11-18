"""配布先は Claude Code と Codex だけである（#2231 の I1）。

agy と Kiro CLI へは何も配らず、cross-review / cross-refactoring の相手役として起動するだけにした。
その導入物（`agy-skills.txt`・`kiro-skills.txt`・`dev.agy/`・`dev.kiro/`）がどのプラグインにも戻らないこと、
リリースの対象ランタイム（`.ndf/project.json` の `branch_strategy.release.runtimes`）が `claude`・`codex` の中に
収まることを確かめる。生成の手順は agy・kiro の導入物を作らないため、手で足したものはここでしか見つからない。
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DISTRIBUTED = {"claude", "codex"}
RETIRED_FILES = ("agy-skills.txt", "kiro-skills.txt")
RETIRED_DIRS = ("dev.agy", "dev.kiro")


def retired_paths(root: Path) -> list[str]:
    """`plugins/` の下にある agy・kiro の導入物を、根からの相対パスで返す。"""
    found = [p for name in RETIRED_FILES for p in root.glob(f"plugins/**/manifests/{name}")]
    found += [p for name in RETIRED_DIRS for p in root.glob(f"plugins/**/{name}") if p.is_dir()]
    return sorted(p.relative_to(root).as_posix() for p in found)


def release_runtimes(root: Path) -> set[str]:
    data = json.loads((root / ".ndf/project.json").read_text(encoding="utf-8"))
    return set(data["branch_strategy"]["release"]["runtimes"])


def test_no_plugin_ships_agy_or_kiro_files() -> None:
    assert retired_paths(REPO) == []


def test_release_runtimes_are_claude_and_codex_only() -> None:
    runtimes = release_runtimes(REPO)
    assert runtimes
    assert runtimes <= DISTRIBUTED, runtimes - DISTRIBUTED


def test_a_restored_kiro_installer_is_found(tmp_path: Path) -> None:
    """mcp-* の `dev.kiro/install.sh` を 1 つ戻すと見つかる（検査が空振りしていないこと）。"""
    installer = tmp_path / "plugins/mcp/mcp-notion/dev.kiro/install.sh"
    installer.parent.mkdir(parents=True)
    installer.write_text("#!/bin/sh\n", encoding="utf-8")
    manifest = tmp_path / "plugins/ndf/manifests/agy-skills.txt"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("pr\n", encoding="utf-8")
    assert retired_paths(tmp_path) == ["plugins/mcp/mcp-notion/dev.kiro", "plugins/ndf/manifests/agy-skills.txt"]


def test_a_kiro_release_runtime_is_found(tmp_path: Path) -> None:
    (tmp_path / ".ndf").mkdir()
    decl = {"branch_strategy": {"release": {"runtimes": ["claude", "codex", "kiro"]}}}
    (tmp_path / ".ndf/project.json").write_text(json.dumps(decl), encoding="utf-8")
    assert release_runtimes(tmp_path) - DISTRIBUTED == {"kiro"}


def table_runtimes(root: Path) -> set[str]:
    """配る Skill の表（`plugins/*/manifests/skills.json`）に現れるランタイム（表の runtimes と行の runtimes）。"""
    found: set[str] = set()
    for table in root.glob("plugins/*/manifests/skills.json"):
        data = json.loads(table.read_text(encoding="utf-8"))
        found |= set(data["runtimes"])
        found |= {runtime for row in data["skills"] for runtime in row["runtimes"]}
    return found


def test_skill_tables_name_claude_and_codex_only() -> None:
    runtimes = table_runtimes(REPO)
    assert runtimes, "配る Skill の表が見つからない"
    assert runtimes <= DISTRIBUTED, runtimes - DISTRIBUTED


def test_a_kiro_row_in_a_skill_table_is_found(tmp_path: Path) -> None:
    table = tmp_path / "plugins/ndf/manifests/skills.json"
    table.parent.mkdir(parents=True)
    table.write_text(json.dumps({"runtimes": {"claude": {}}, "skills": [{"name": "pr", "runtimes": ["claude", "kiro"]}]}), encoding="utf-8")
    assert table_runtimes(tmp_path) - DISTRIBUTED == {"kiro"}
