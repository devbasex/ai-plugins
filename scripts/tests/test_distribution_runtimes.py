"""配布先は Claude Code と Codex だけである（#2231 の I1）。

agy と Kiro CLI へは何も配らず、cross-review / cross-refactoring の相手役として起動するだけにした。
その導入物（`agy-skills.txt`・`kiro-skills.txt`・`dev.agy/`・`dev.kiro/`）がどのプラグインにも戻らないこと、
リリースの対象ランタイム（`.ndf/supervise.json` の `release.runtimes`）が `claude`・`codex` の中に
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
    data = json.loads((root / ".ndf/supervise.json").read_text(encoding="utf-8"))
    return set(data["release"]["runtimes"])


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
    (tmp_path / ".ndf/supervise.json").write_text(json.dumps({"release": {"runtimes": ["claude", "codex", "kiro"]}}), encoding="utf-8")
    assert release_runtimes(tmp_path) - DISTRIBUTED == {"kiro"}
