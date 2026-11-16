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


def declare(root: Path, base: str = "main", production: str | None = None, release: dict | None = None, deliveries: list | None = None, **edits) -> dict:
    """ブランチ名とリリースの形から種類を選んで宣言を書く（旧い `worktree.json` のブランチ名・`supervise.json` の `release`・
    `project.json` の `delivery` を入力にしていたテストの移し先）。

    ベースブランチと本番ブランチが同じなら `web-trunk`（リリースの形が `package-plugin` なら `plugin`）、違えば `web-develop`
    の写しを使う。`package-plugin` のリリースの値（版数の置き場・タグ）は `plugin` の種類のものを土台にし、渡した値で上書きする。"""
    production = production or base
    plugin = (release or {}).get("form") == "package-plugin"
    kind = ("plugin" if plugin else "web-trunk") if production == base else "web-develop"
    rel = copy.deepcopy(section("plugin" if plugin else kind)["release"])
    rel.update(release or {})
    if deliveries is not None:
        rel["deliveries"] = deliveries
    return write_strategy(root, kind, base=base, production=production, release=rel, **edits)


def pop_release(supervise: dict) -> dict | None:
    """`supervise.json` の中身から旧いキー `release` を外し、宣言の `release` へ移せる値（形・プラグイン・ランタイム）を返す。

    ai-plugins の `.ndf/supervise.json` を写すテストが使う（ai-plugins の移行は手順 2 で、それまで旧いキーが残る）。
    開発版のワークフローはチャネルの値なので移さない。"""
    old = supervise.pop("release", None)
    return {k: old[k] for k in ("form", "plugin", "runtimes") if k in old} if isinstance(old, dict) else None
