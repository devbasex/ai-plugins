"""テストが一時のリポジトリへブランチ戦略の宣言を置く補助（#3672 の D37）。

代表のリポジトリ 5 種の宣言は `fixtures/branch_strategy/<種類>.json` の 1 か所にあり、ここはそれを読んでブランチ名だけを
差し替え、`.ndf/project.json` のセクション `branch_strategy` へ書く。旧い宣言（`.ndf/worktree.json` のブランチ名など）を
入力にするテストは、この補助で新しいセクションを置く形へ移す。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

FIX = Path(__file__).resolve().parent / "fixtures" / "branch_strategy"
KINDS = ("web-develop", "web-trunk", "mobile", "docs", "plugin")


def section(kind: str = "web-develop", base: str | None = None, production: str | None = None) -> dict:
    """種類の宣言の写し。`base`・`production` を渡せばブランチ名とチャネルの ref をその名前にする。

    `trunk` の種類（`web-trunk`・`docs`・`plugin`）ではベースブランチと本番ブランチが同じ（R1）なので、片方だけ渡せば
    もう片方も同じ名前にする。"""
    sec = copy.deepcopy(json.loads((FIX / f"{kind}.json").read_text(encoding="utf-8")))
    trunk = sec["release_strategy"] == "trunk"
    prod = production or (base if trunk else None) or sec["branches"]["production"]
    b = base or (prod if trunk else None) or sec["branches"]["base"]
    sec["branches"] = {"production": prod, "base": b}
    for ch in sec["channels"]:
        ch["ref"] = prod if ch["name"] == "stable" else b
    return sec


def write_strategy(root: Path, kind: str = "web-develop", base: str | None = None, production: str | None = None, **edits) -> dict:
    """`root/.ndf/project.json` にセクションを書き、書いたセクションを返す。ほかのキーは残す。

    `edits` はセクションの最上位のキーを置き換える（`release={...}` など）。"""
    sec = section(kind, base, production)
    sec.update(edits)
    p = Path(root) / ".ndf" / "project.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {"version": 1}
    doc["branch_strategy"] = sec
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return sec
