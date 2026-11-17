"""代表のリポジトリ 5 種のブランチ戦略の設定（`tests/fixtures/branch_strategy/`）を読み、テストのリポジトリへ置く補助。

5 種の設定は 1 か所にあり、10 系のテスト（`plugins/ndf/scripts/tests/strategy_fixture.py`）も同じファイルを読む。
11 系の project（`domains/project/test_branch_strategy_cmd.py`）と release（`domains/release/_fakes.py`）のテストも、
設定の読み込みを持たずにここの `FIX`・`KINDS`・`section` を使う。
`EXPECTED` は 10 系の `lib/branch_strategy.py` が同じ設定から返す PR のマージ先とスプリントの起点（#3681 の受け入れ条件 1）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "branch_strategy"
KINDS = ("web-develop", "web-trunk", "mobile", "docs", "plugin")
SPRINT_BRANCH = "sprint/s1"
# 種類 → (スプリントの起点, スプリントの PR のマージ先, 設計 PR のマージ先, 版上げの PR のマージ先)
EXPECTED = {
    "web-develop": ("develop", "develop", "develop", "main"),
    "web-trunk": ("main", "main", SPRINT_BRANCH, "main"),
    "mobile": ("main", "develop", SPRINT_BRANCH, "main"),
    "docs": ("main", "main", SPRINT_BRANCH, "main"),
    "plugin": ("main", "main", SPRINT_BRANCH, "main"),
}


def section(kind: str) -> dict:
    """種類の設定のセクション（`branch_strategy` の値）。"""
    return json.loads((FIX / f"{kind}.json").read_text(encoding="utf-8"))


def write(root: Path, kind: Optional[str] = "web-develop", extra: Optional[dict] = None) -> None:
    """`root/.ndf/project.json` へ種類の設定のセクション（と `extra` のセクション）を書く。`kind` が None ならセクションを書かない。"""
    project = dict(extra or {})
    if kind is not None:
        project["branch_strategy"] = section(kind)
    (root / ".ndf").mkdir(parents=True, exist_ok=True)
    (root / ".ndf" / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
