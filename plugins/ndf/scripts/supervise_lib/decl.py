"""プロジェクトごとの宣言（リポジトリの根の `.ndf/`）の読み取りと、雛形の引数への当てはめ（#1142 の C1）。"""
from __future__ import annotations

import json
from pathlib import Path


# プロジェクトごとの宣言（リポジトリの根の .ndf/）。形は DECLARATIONS の節にある
WORKTREE_DECL = "worktree.json"    # base_branch（起点のブランチ）・production_branch（本番のブランチ）
SUPERVISE_DECL = "supervise.json"  # test・sync_checks・release


class DeclError(Exception):
    """宣言が読めない・形が違う。"""


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
        if not isinstance(d, dict):
            raise DeclError(f"{f}: 最上位はオブジェクトで書く")
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
    checks = decl.get("sync_checks") or []
    if not isinstance(checks, list) or not all(
            isinstance(c, dict) and isinstance(c.get("name"), str) and isinstance(c.get("command"), str)
            for c in checks):
        raise DeclError("supervise.json: sync_checks は {\"name\", \"command\"} の並びで書く")
    return [(c["name"], c["command"]) for c in checks]


# 雛形が宣言から受けるもの。引数が宣言より先に効く
# mission はリリースの形を要らない（雛形の無い形ならリリースの段を書かず、/ndf:release で行うと返す）
NEEDS = {"impl": ("base", "test"), "fix": ("base", "test"), "check": ("base", "test"), "release": ("base", "release"),
         "mission": ("base", "test"), "close": ("base", "test", "release")}


def apply_decls(a) -> None:
    """引数に無いものを .ndf/ の宣言から埋める。雛形に要るのにどちらにも無ければ DeclError。

    - 起点のブランチ（a.base）: --base → worktree.json の base_branch
    - 本番のブランチ（a.production_branch）: --production-branch → worktree.json の production_branch
    - テスト（a.test_cmd・a.test_all・a.no_reports）: --test-cmd・--test-all → supervise.json の test
    - 同期とチェック（a.sync_checks）: supervise.json の sync_checks（無ければ計画に sync のステップを置かない）
    - 配布（a.release）: supervise.json の release
    """
    roots = decl_roots(a.worktree, getattr(a, "repo", None))
    wt = read_decl(roots, WORKTREE_DECL)
    sv = read_decl(roots, SUPERVISE_DECL)
    test = sv.get("test") or {}
    if not isinstance(test, dict):
        raise DeclError("supervise.json: test はオブジェクトで書く")
    a.base = getattr(a, "base", None) or wt.get("base_branch") or None
    a.production_branch = getattr(a, "production_branch", None) or wt.get("production_branch") or None
    a.test_cmd = getattr(a, "test_cmd", None) or test.get("command") or None
    a.test_all = getattr(a, "test_all", None) or test.get("all") or "."
    a.no_reports = test.get("no_reports") or ""
    a.sync_checks = sync_checks_of(sv)
    a.release = sv.get("release") or None
    missing = {
        "base": (not a.base, f"起点のブランチ（--base か .ndf/{WORKTREE_DECL} の base_branch）"),
        "test": (not a.test_cmd, f"テストのコマンド（--test-cmd か .ndf/{SUPERVISE_DECL} の test.command）"),
        "release": (not isinstance(a.release, dict) or not a.release.get("form"),
                    f"配布の形（.ndf/{SUPERVISE_DECL} の release.form）"),
    }
    lack = [missing[k][1] for k in NEEDS[a.kind] if missing[k][0]]
    if lack:
        raise DeclError(f"new {a.kind} に要る宣言が無い: " + "・".join(lack))


def decl_fields(a) -> dict:
    """ミッションの雛形が各計画へ引き継ぐ、宣言から埋めた値。"""
    return {k: getattr(a, k) for k in ("base", "production_branch", "test_cmd", "test_all", "no_reports",
                                       "sync_checks", "release")}


def with_decls(plan: dict, a) -> dict:
    """計画に起点のブランチと、テストに成果物を作らせない指定を書く（run のステップと pr のステップが読む）。"""
    plan["base_branch"] = a.base
    if getattr(a, "no_reports", ""):
        plan["no_reports"] = a.no_reports
    return plan
