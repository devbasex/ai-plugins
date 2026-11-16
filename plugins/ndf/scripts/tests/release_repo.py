"""release-steps.py のテストに共通する一時の git リポジトリの土台。

origin は bare（ベースブランチは trunk）。seed は git の利用者と、ブランチ戦略の宣言（`.ndf/project.json` の
`branch_strategy`。ベースブランチ trunk・本番ブランチ live・リリースの形 package-plugin）を持つ。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import strategy_fixture  # noqa: E402


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)


def seed_release_repo(tmp_path: Path) -> tuple[Path, Path]:
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "trunk", str(origin)], check=True)
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "trunk")
    _git(seed, "config", "user.email", "t@example.com")
    _git(seed, "config", "user.name", "t")
    strategy_fixture.declare(seed, "trunk", "live", {"form": "package-plugin", "plugin": "foo"})
    return origin, seed


def push_origin(root: Path, branch: str = "develop") -> Path:
    """root の HEAD を、隣に置く bare の origin の `branch` へ送り、取り直す（`bump` は origin を取り直してから版を読む。#2164）。"""
    bare = root.parent / f"{root.name}-origin.git"
    if not bare.exists():
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        _git(root, "remote", "add", "origin", str(bare))
    _git(root, "push", "-q", "-f", "--tags", "origin", f"HEAD:refs/heads/{branch}")
    _git(root, "fetch", "-q", "origin")
    return bare
