"""検査のテストの共通の前提: 一時の git リポジトリの中に、見る対象のプラグインを作る。"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from ndf.checks import child_deps, run
from ndf.checks.model import Finding, Scope, load_rules

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
# 開発の間のディレクトリ名とプラグイン名（#3347 の決定 100）
DIR = "ndf-next"
NAME = "ndf11"


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return done.stdout.strip()


@dataclass
class Plugin:
    repo: Path
    root: Path

    def write(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def scope(self, base: str | None = None, pr_body: str = "") -> Scope:
        scope = Scope(self.repo, self.root, NAME, base, pr_body)
        scope.sources = run.collect(self.root, self.repo)
        return scope

    def commit(self, message: str = "c") -> str:
        git(self.repo, "add", "-A")
        git(self.repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", message)
        return git(self.repo, "rev-parse", "HEAD")


@pytest.fixture
def plugin(tmp_path: Path) -> Plugin:
    repo = tmp_path / "repo"
    root = repo / "plugins" / DIR
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": NAME, "version": "0"}))
    shutil.copy(PLUGIN_ROOT / "pyproject.toml", root / "pyproject.toml")
    (root / "src" / "ndf").mkdir(parents=True)
    (root / "src" / "ndf" / "__init__.py").write_text('"""p"""\n')
    # 規則 child-deps の子の表。1 つの子が `ndf` の全体を持つ（ほかの規則のテストに持ち主の指摘を出さない）
    child = {"packages": ["ndf"], "needs": [], "order": 1, "lane": "l", "closed": False}
    (root / child_deps.CHILDREN_REL).parent.mkdir(parents=True)
    (root / child_deps.CHILDREN_REL).write_text(json.dumps({"children": {"X": child}, "tables": {}}))
    git(repo, "init", "-q")
    return Plugin(repo, root)


@pytest.fixture
def rules() -> dict:
    return load_rules()


def _where(found: list[Finding]) -> list[tuple[str, int]]:
    return [(f.path.rsplit("/", 1)[-1], f.line) for f in found]


@pytest.fixture
def where():
    """指摘を（ファイル名, 行）の組に縮める関数（`where(found)`）。"""
    return _where
