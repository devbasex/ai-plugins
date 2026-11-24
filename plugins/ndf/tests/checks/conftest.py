"""検査のテストの共通の前提: 一時の git リポジトリの中に、見る対象のプラグインを作る。"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import git_sandbox
import pytest

from ndf.checks import child_deps, run, spec_docs, spec_render
from ndf.checks.model import Finding, Scope, load_rules

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
# 仮のディレクトリ名とプラグイン名。I1 が `identity.FINAL_NAME`（`ndf`）と別に `plugin.json` の `name` と
# ディレクトリ名も当てることを確かめるため、どちらも `ndf` と違う名前にする（#3347 の決定 100）
DIR = "ndf-dev"
NAME = "ndfdev"
# 規約の規則のテストが一時の保存先へ写すディレクトリ
COPIED = ("contracts", "docs", "bin", "skills", "agents", "manifests")


@dataclass
class Plugin:
    repo: Path
    root: Path

    def write(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def scope(self) -> Scope:
        scope = Scope(self.repo, self.root, NAME)
        scope.sources = run.collect(self.root, self.repo)
        return scope

    def commit(self, message: str = "c") -> str:
        git_sandbox.sh(self.repo, "add", "-A", strip=True)
        git_sandbox.sh(self.repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", message, strip=True)
        return git_sandbox.sh(self.repo, "rev-parse", "HEAD", strip=True)


@pytest.fixture
def plugin(tmp_path: Path) -> Plugin:
    repo = tmp_path / "repo"
    root = repo / "plugins" / DIR
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": NAME, "version": "0"}))
    shutil.copy(PLUGIN_ROOT / "pyproject.toml", root / "pyproject.toml")
    (root / "src" / "ndf").mkdir(parents=True)
    (root / "src" / "ndf" / "__init__.py").write_text('"""p"""\n')
    # 規則 child-deps の子の表。1 つの子が `ndf` の全体を持つ（ほかの規則のテストにオーナーの指摘を出さない）
    child = {"packages": ["ndf"], "needs": [], "order": 1, "lane": "l", "closed": False}
    (root / child_deps.CHILDREN_REL).parent.mkdir(parents=True)
    (root / child_deps.CHILDREN_REL).write_text(json.dumps({"children": {"X": child}, "tables": {}}))
    git_sandbox.sh(repo, "init", "-q", strip=True)
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


class Copy:
    """プラグインの規約の入力と生成物を写したリポジトリ。設計レビューの項目の表と 11.x の対応表は合成する。"""

    def __init__(self, tmp: Path) -> None:
        self.repo = tmp / "repo"
        self.root = self.repo / "plugins" / "p"
        self.spec = load_rules()["spec"]
        self.table = self.repo / self.spec["review_items_source"]
        for name in COPIED:
            shutil.copytree(PLUGIN_ROOT / name, self.root / name, ignore=shutil.ignore_patterns("__pycache__"))
        self.write_items(("1-1", "要旨", "S2", "10", "既存", "344", "11.0.0"))
        self.write_children(spec_render.review_items(self.table, self.spec["review_items_columns"]))

    def path(self, rel: str) -> Path:
        return self.root / rel

    def edit_json(self, rel: str, change) -> None:
        path = self.path(rel)
        data = json.loads(path.read_text(encoding="utf-8"))
        change(data)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def write(self, rel: str, text: str) -> None:
        path = self.path(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def write_items(self, *rows: tuple[str, ...], columns: tuple[str, ...] | None = None) -> None:
        """項目の表（列は `rules.json` の順。`columns` を渡せばその列）を書く。"""
        self._table(self.table, columns or tuple(self.spec["review_items_columns"].values()), rows)

    def write_correspondence(self, name: str, *rows: tuple[str, ...]) -> None:
        """項目の表と同じ保存先に、対応表 `name` を書く。"""
        self._table(self.table.parent / name, tuple(self.spec["correspondence_columns"].values()), rows)

    def _table(self, path: Path, columns: tuple[str, ...], rows: tuple[tuple[str, ...], ...]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# 表\n\n" + spec_render.md_table(list(columns), [list(r) for r in rows]) + "\n\n後の文\n", encoding="utf-8")

    def write_children(self, items: list[dict]) -> None:
        self.write(child_deps.CHILDREN_REL, spec_render.children({"summary": "s", "children": {}, "tables": {}}, items))

    def findings(self, write_spec: bool = False):
        scope = Scope(self.repo, self.root, "p")
        scope.sources = run.collect(self.root, self.repo)
        return spec_docs.check(scope, load_rules(), write_spec)


@pytest.fixture
def copy(tmp_path: Path) -> Copy:
    return Copy(tmp_path)
