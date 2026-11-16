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


def snapshot(kind: str = "web-trunk", base: str | None = "main", production: str | None = None, **edits) -> dict:
    """スプリントの開始時のスナップショット（`branch_strategy.snapshot_section` の形）。引数の名前空間を直に組むテストが
    `flow` に渡す（部品は `a.flow` だけを読み、無ければ止まる。I3）。`edits` はセクションの最上位のキーを置き換える。"""
    import branch_strategy

    sec = section(kind, base, production)
    sec.update(edits)
    return branch_strategy.snapshot_section(branch_strategy.Strategy(sec), "宣言")


def write_strategy(root: Path, kind: str = "web-develop", base: str | None = None, production: str | None = None, **edits) -> dict:
    """`root/.ndf/project.json` にセクションを書き、書いたセクションを返す。ほかのキーは残す。

    `edits` はセクションの最上位のキーを置き換える（`release={...}` など）。`release.cd` が `null` なら R10 に合わせて
    タグを `null`・GitHub Release を偽にする。"""
    sec = section(kind, base, production)
    sec.update(edits)
    if sec["release"].get("cd") is None:
        # CD の無い宣言はタグも GitHub Release も持たない（R10。タグと Release を作るのは CD だけ）
        sec["release"] = {**sec["release"], "tag": None, "github_release": False}
    p = Path(root) / ".ndf" / "project.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {"version": 1}
    doc["branch_strategy"] = sec
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return sec


def declare(
    root: Path, base: str = "main", production: str | None = None, release: dict | None = None, deliveries: list | None = None, **edits
) -> dict:
    """ブランチ名とリリースの形から種類を選んで宣言を書く（旧い `worktree.json` のブランチ名・`supervise.json` の `release`・
    `project.json` の `delivery` を入力にしていたテストの移し先）。

    ベースブランチと本番ブランチが同じなら `web-trunk`（リリースの形が `package-plugin` なら `plugin`）、違えば `web-develop`
    の写しを使う。`package-plugin` のリリースの値（版数の置き場・タグ）は `plugin` の種類のものを土台にし、渡した値で上書きする。

    旧い宣言では CD は `delivery` の行だけで決まった。`deliveries` も `release` の `cd` も渡さなければ CD の無い宣言にする
    （種類の写しの `release.cd` を残すと、`release.cd` から CD の行を組む読み手が CD の有る場合として扱う）。CD の無い宣言は
    R10 に合わせてタグを `null`・GitHub Release を偽にする。"""
    production = production or base
    plugin = (release or {}).get("form") == "package-plugin"
    kind = ("plugin" if plugin else "web-trunk") if production == base else "web-develop"
    rel = copy.deepcopy(section("plugin" if plugin else kind)["release"])
    if deliveries is None:
        rel["cd"] = None  # タグと GitHub Release は write_strategy が R10 に合わせて外す
    rel.update(release or {})
    if deliveries is not None:
        rel["deliveries"] = deliveries
    return write_strategy(root, kind, base=base, production=production, release=rel, **edits)


AI_PLUGINS_DEVELOP = Path(__file__).resolve().parent / "fixtures" / "ai-plugins-develop.json"
# 移行（#3672 の手順 2）の前の ai-plugins の宣言（develop → main・開発版のチャネル release-dev.yml・管理者の迂回）を、
# 移行のコマンドが書いた値のまま凍結した写し。ai-plugins は trunk へ移ったため、develop → main の経路と開発版を
# 確かめるテストはこの写しを読む
AI_PLUGINS_DEVELOP_VERIFY = "python3 plugins/ndf/scripts/release-verification-steps.py verify-install --ref develop --runtimes claude,codex"


def ai_plugins_develop_section() -> dict:
    """移行の前の ai-plugins のブランチ戦略の宣言（`AI_PLUGINS_DEVELOP`）の写し。"""
    return json.loads(AI_PLUGINS_DEVELOP.read_text(encoding="utf-8"))


def ai_plugins_release() -> dict:
    """移行の前の ai-plugins のリリースの値（形・プラグイン・ランタイム）。`declare` の `release` に渡す。

    ai-plugins の `.ndf/supervise.json` を写して develop → main の宣言を置くテストが使う（旧いキー `release` は移行で
    無くなった）。開発版のワークフローはチャネルの値なので含めない。"""
    rel = ai_plugins_develop_section()["release"]
    return {k: rel[k] for k in ("form", "plugin", "runtimes")}


ROOT = Path(__file__).resolve().parents[4]  # このリポジトリ（ai-plugins）の根


def migrated_ndf(src: Path = ROOT) -> dict[str, dict]:
    """`src/.ndf/` の JSON（ファイル名 → 中身）を、旧い宣言を移行のコマンド（`branch-strategy migrate`）の値へ移して返す。

    旧いキーの残る `.ndf/` でも、部品は旧いキーが残ると止まる（I4）ので、移した写しで打てる。旧いキーが無ければ中身のまま。"""
    import branch_strategy_legacy as legacy
    from project_lib import branch_strategy_migrate as bmig

    plan, questions = bmig.plan_migration(src, legacy.load_moves())
    assert not questions and not plan.failed, (questions, plan.failed)
    files = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in sorted((Path(src) / ".ndf").glob("*.json"))}
    files.update(plan.files)
    return files


def repo_like(dest: Path, src: Path = ROOT) -> Path:
    """`migrated_ndf` の `.ndf/` を持ち、ブランチ戦略を移行の前の ai-plugins の宣言（`AI_PLUGINS_DEVELOP`。develop → main・
    開発版のチャネル）に置き換えた git のリポジトリを `dest/repo` に作って返す。`pace.json` の導入の確認も移行の前の値
    （`--ref develop`）にする。

    ベースブランチ develop を checkout し、bare の origin（`dest/origin.git`）へ develop と main を送る（種別リリースが
    本番ブランチの先頭の宣言を読めるように）。"""
    import subprocess

    from git_fixture import init_with_origin

    root = init_with_origin(Path(dest))
    ndf = root / ".ndf"
    ndf.mkdir()
    files = migrated_ndf(src)
    files["project.json"] = {**files.get("project.json", {"version": 1}), "branch_strategy": ai_plugins_develop_section()}
    pace = files.get("pace.json")
    for name in ("fast", "auto"):
        if isinstance(pace, dict) and isinstance(pace.get(name), dict) and pace[name].get("verify"):
            pace[name]["verify"] = AI_PLUGINS_DEVELOP_VERIFY
    for name, doc in files.items():
        (ndf / name).write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md = Path(src) / ".ndf" / "mvv.md"
    if md.is_file():
        (ndf / "mvv.md").write_text(md.read_text(encoding="utf-8"), encoding="utf-8")
    for args in (
        ("add", "-A"),
        ("commit", "-q", "-m", "init"),
        ("push", "-q", "-u", "origin", "develop"),
        ("push", "-q", "origin", "develop:main"),
    ):
        subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)
    subprocess.run(["git", "-C", str(root), "fetch", "-q", "origin"], capture_output=True, text=True, check=True)
    return root


_LIKE: list[Path] = []


def like_root() -> Path:
    """`repo_like` のリポジトリ（移行の前の ai-plugins の形）をプロセスに 1 つだけ作って返す（このリポジトリの根を今の
    ディレクトリにしていたテストの移し先）。
    プロセスの終わりに消す。テストは中身を書き換えない。"""
    if not _LIKE:
        import atexit
        import shutil
        import tempfile

        d = Path(tempfile.mkdtemp(prefix="ndf-like-"))
        atexit.register(shutil.rmtree, d, True)
        _LIKE.append(repo_like(d))
    return _LIKE[0]
