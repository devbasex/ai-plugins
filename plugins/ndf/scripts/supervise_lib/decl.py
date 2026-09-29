"""プロジェクトごとの宣言（リポジトリの根の `.ndf/`）の読み取りと、雛形の引数への当てはめ（#1142 の C1）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import delivery
import project_decl
import schema
from pydantic import ConfigDict


# プロジェクトごとの宣言（リポジトリの根の .ndf/）。形は DECLARATIONS の節にある
WORKTREE_DECL = "worktree.json"  # base_branch（起点のブランチ）・production_branch（本番のブランチ）
SUPERVISE_DECL = "supervise.json"  # test・sync_checks・release


class DeclError(Exception):
    """宣言が読めない・形が違う。"""


class Decl(schema.Shape):
    """宣言の最上位（オブジェクトであることだけを見る。項目はプロジェクトごとに違うので拒まない）。"""

    model_config = ConfigDict(extra="allow")


class SyncCheck(schema.Shape):
    model_config = ConfigDict(extra="allow")
    name: str
    command: str


class SuperviseDecl(Decl):
    """.ndf/supervise.json のうち、形を見る項目（test・sync_checks）。"""

    test: dict = {}
    sync_checks: list[SyncCheck] = []


def supervise_shape(decl: dict) -> SuperviseDecl:
    """supervise.json の test と sync_checks の形を見る（空・null は無いとみなす）。違えば DeclError。"""
    try:
        return schema.load_shape(SuperviseDecl, {"test": decl.get("test") or {}, "sync_checks": decl.get("sync_checks") or []})
    except schema.ShapeError as e:
        raise DeclError(f'supervise.json: {e}（test はオブジェクト、sync_checks は {{"name", "command"}} の並びで書く）') from e


def read_decl(roots, name: str) -> dict:
    """roots の順に .ndf/<name> を探し、最初に見つかった宣言を返す。どこにも無ければ {}。"""
    for r in roots:
        f = Path(r) / ".ndf" / name
        if not f.is_file():
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except ValueError as e:
            raise DeclError(f"{f}: JSON として読めない: {e}") from e
        try:
            schema.load_shape(Decl, d, str(f))
        except schema.ShapeError as e:
            raise DeclError(f"{e}（最上位はオブジェクトで書く）") from e
        return d
    return {}


def decl_roots(worktree: str, repo: str | None = None) -> list[Path]:
    """宣言を探す場所: 作業場所 → 元のリポジトリ（--repo か作業場所の /.worktrees/ より前）→ 今のディレクトリ。"""
    roots = [Path(worktree)]
    if repo:
        roots.append(Path(repo))
    if "/.worktrees/" in str(worktree):
        roots.append(Path(str(worktree).split("/.worktrees/")[0]))
    roots.append(Path.cwd())
    return roots


def declared_base_of(roots) -> str | None:
    """.ndf/worktree.json の base_branch。無ければ None。"""
    try:
        v = read_decl(roots, WORKTREE_DECL).get("base_branch")
    except DeclError:
        return None
    return v if isinstance(v, str) and v else None


def sync_checks_of(decl: dict) -> list[tuple[str, str]]:
    """.ndf/supervise.json の sync_checks を [(名前, コマンド)] で返す。"""
    return [(c.name, c.command) for c in supervise_shape(decl).sync_checks]


# 雛形が宣言から受けるもの。引数が宣言より先に効く
# sprint と close はリリースの形を要らない（リリースの経路を delivery から導く。雛形で組む経路だけが版数を要する。#1336）
NEEDS = {
    "impl": ("base", "test"),
    "fix": ("base", "test"),
    "check": ("base", "test"),
    "release": ("base", "release"),
    "sprint": ("base", "test"),
    "close": ("base", "test"),
}


def test_kind(a) -> str:
    """`--test-cmd` の雛形の種別（`--test-kind`。既定はテスト。#1483 決定 3）。"""
    return getattr(a, "test_kind", None) or "test"


def scope_paths(a) -> list[str]:
    """静的解析の雛形の全体テストで `{paths}` に入れる範囲 = 明示した `--tests`。`.`（sprint の既定）は数えない（I4）。"""
    return [str(t) for t in getattr(a, "tests", None) or [] if str(t) not in (".", "")]


def apply_decls(a) -> None:
    """引数に無いものを .ndf/ の宣言から埋める。雛形に要るのにどちらにも無ければ DeclError。

    - 起点のブランチ（a.base）: --base → worktree.json の base_branch
    - 本番のブランチ（a.production_branch）: --production-branch → worktree.json の production_branch
    - テスト（a.strategy・a.test_limits・a.test_cmd・a.no_reports）: --test-cmd（雛形）→ project.json の test →
      supervise.json の test.command（project.json に test が無いときだけ。読んだことを a.test_note に残す。#1334 決定 3）。
      --test-all は廃止（知らせて無視する）
    - 同期とチェック（a.sync_checks）: supervise.json の sync_checks（無ければ計画に sync のステップを置かない）
    - 配布（a.release）: supervise.json の release

    リリースの経路（a.routes）は sprint と close だけが使い、`sprint_routes.apply_routes` が delivery_decl（宣言。a.delivery）と require_versions で組む。
    """
    import test_strategy as ts

    roots = decl_roots(a.worktree, getattr(a, "repo", None))
    wt = read_decl(roots, WORKTREE_DECL)
    sv = read_decl(roots, SUPERVISE_DECL)
    sv_test = supervise_shape(sv).test
    a.base = getattr(a, "base", None) or wt.get("base_branch") or None
    a.production_branch = getattr(a, "production_branch", None) or wt.get("production_branch") or None
    a.test_cmd = getattr(a, "test_cmd", None) or None
    if getattr(a, "test_all", None):
        print("⚠ --test-all は廃止しました（#1334）。全体テストは宣言の suites[].command で決まります", file=sys.stderr, flush=True)
    a.test_all = None
    a.no_reports = sv_test.get("no_reports") or ""
    a.sync_checks = sync_checks_of(sv)
    a.release = sv.get("release") or None
    decl, a.test_note = _project_decl(roots, sv)
    a.strategy, a.test_limits = None, None
    # 静的解析の雛形の全体テストの範囲。スプリントが各計画（検査の計画を含む）へ引き継ぐ（#1483）
    a.test_paths = getattr(a, "test_paths", None) or scope_paths(a)
    if decl.get("test") is not None or a.test_cmd:
        try:
            strategy = ts.resolve(decl, baseline_test=a.test_cmd, template_kind=test_kind(a), scope_paths=scope_paths(a))
        except ts.StrategyError as e:
            raise DeclError(str(e)) from e
        w, w_source = ts.whole_seconds(decl)
        c = ts.ci_wall_seconds(decl, (strategy.ci or {}).get("check") if strategy.ci else None)
        a.strategy = strategy.as_state()
        a.test_limits = ts.limits(strategy, None, whole_seconds_value=w, whole_source=w_source, ci_seconds=c)
    missing = {
        "base": (not a.base, f"起点のブランチ（--base か .ndf/{WORKTREE_DECL} の base_branch）"),
        "test": (
            a.strategy is None,
            f"テストの宣言（.ndf/{project_decl.DECL.name} の test か --test-cmd、または .ndf/{SUPERVISE_DECL} の test.command）",
        ),
        "release": (not isinstance(a.release, dict) or not a.release.get("form"), f"配布の形（.ndf/{SUPERVISE_DECL} の release.form）"),
    }
    lack = [missing[k][1] for k in NEEDS[a.kind] if missing[k][0]]
    if lack:
        raise DeclError(f"new {a.kind} に要る宣言が無い: " + "・".join(lack))


def delivery_decl(a) -> "delivery.DeliveryDecl":
    """配布の判定に使う宣言（lib/delivery.py の DeliveryDecl）。起点と本番のブランチは引数（a.base・a.production_branch）が
    宣言より先に効く。読めない宣言は `problems` に書き、例外を上げない。"""
    import repo

    roots = decl_roots(a.worktree, getattr(a, "repo", None))
    problems = []
    try:
        wt = read_decl(roots, WORKTREE_DECL)
    except DeclError as e:
        wt, problems = {}, [str(e)]
    try:
        project = read_decl(roots, project_decl.DECL.name)
    except DeclError as e:
        project, problems = {}, [*problems, str(e)]
    return delivery.build(
        wt,
        project,
        a.release,
        default_branch=repo.default_branch(roots[0]),
        problems=problems,
        base=a.base,
        production=a.production_branch,
    )


def require_versions(a) -> None:
    """版数（--version・close は --prod も）は、リリースの経路が雛形（template）で組むときだけ要る（#1336 の I6）。"""
    if not delivery.needs_version(a.routes):
        return
    need = [("version", "--version", "版数"), *([("prod", "--prod", "本番の版")] if a.kind == "close" else [])]
    gaps = [f"{what}（{flag}。release.form の雛形は版数で組む）" for attr, flag, what in need if not getattr(a, attr, None)]
    if gaps:
        raise DeclError(f"new {a.kind} に要る宣言が無い: " + "・".join(gaps))


def _project_decl(roots, sv: dict) -> tuple[dict, str | None]:
    """roots の順に `.ndf/project.json` を探し、`test` を持つ最初の宣言。無ければ supervise.json の test を 1 つの suite として読む。"""
    import test_strategy as ts

    last: tuple[dict, str | None] = ({}, None)
    for r in roots:
        decl, note = ts.decl_of(r, sv)
        if note is None and decl.get("test") is not None:
            return decl, None
        last = (decl, note)
    return last


def decl_fields(a) -> dict:
    """スプリントの雛形が各計画へ引き継ぐ、宣言から埋めた値。"""
    keys = (
        "base",
        "production_branch",
        "test_cmd",
        "test_kind",
        "test_paths",
        "test_all",
        "no_reports",
        "sync_checks",
        "release",
        "strategy",
        "test_limits",
        "test_note",
        "routes",
    )
    return {k: getattr(a, k, None) for k in keys}


def with_decls(plan: dict, a) -> dict:
    """計画に起点のブランチと、テストに成果物を作らせない指定を書く（run のステップと pr のステップが読む）。"""
    plan["base_branch"] = a.base
    if getattr(a, "no_reports", ""):
        plan["no_reports"] = a.no_reports
    return plan
