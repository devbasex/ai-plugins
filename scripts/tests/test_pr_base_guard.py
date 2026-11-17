"""既定ブランチ宛の Pull Request の分岐元をチェックする（issue #202・#3672 の D27）。

判定は宛先のブランチの `.ndf/project.json` のセクション `branch_strategy` の `release_strategy` で分ける。
宣言は宛先のブランチから読み、作業ツリー（Pull Request の head 側）の宣言では判定が変わらない。
宣言が無い・壊れている・旧いキーが残るときは、既定の判定へ倒さずに落とす。
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path


from branch_repo_helpers import push_branch

ROOT = Path(__file__).resolve().parents[2]
GUARD = ROOT / "scripts" / "check-pr-base.sh"
PRESETS = ROOT / "plugins" / "ndf-next" / "tests" / "fixtures" / "branch_strategy"
LIB = ROOT / "plugins" / "ndf" / "scripts" / "lib"


def preset(kind: str) -> dict:
    return json.loads((PRESETS / f"{kind}.json").read_text(encoding="utf-8"))


def run_git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def write_section(repo: Path, section: dict) -> None:
    ndf = repo / ".ndf"
    ndf.mkdir(exist_ok=True)
    (ndf / "project.json").write_text(json.dumps({"branch_strategy": section}), encoding="utf-8")


def publish(repo: Path, section: dict | None = None, files: dict[str, dict] | None = None) -> None:
    """main に宣言を置いて origin の main へ push する（宛先のブランチの宣言）。"""
    run_git(repo, "checkout", "-q", "main")
    if section is not None:
        write_section(repo, section)
    for name, body in (files or {}).items():
        (repo / ".ndf").mkdir(exist_ok=True)
        (repo / ".ndf" / name).write_text(json.dumps(body), encoding="utf-8")
    run_git(repo, "add", ".ndf")
    run_git(repo, "commit", "-q", "-m", "decl")
    run_git(repo, "push", "-q", "origin", "main")


def guard(repo: Path, head_ref: str, sha: str = "") -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(GUARD), head_ref, *([sha] if sha else [])], cwd=str(repo), capture_output=True, text=True)


def guard_main(repo: Path, head_ref: str, sha: str = "") -> subprocess.CompletedProcess:
    env = {**os.environ, "PR_BASE_REF": "main"}
    return subprocess.run(["bash", str(GUARD), head_ref, *([sha] if sha else [])], cwd=str(repo), capture_output=True, text=True, env=env)


def commit_on(repo: Path, name: str) -> str:
    (repo / name).write_text(name, encoding="utf-8")
    run_git(repo, "add", name)
    run_git(repo, "commit", "-q", "-m", name)
    return run_git(repo, "rev-parse", "HEAD")


def test_missing_argument_fails(origin_repo: Path) -> None:
    """分岐元を渡し忘れたときは、通さずに使い方を出す。"""
    got = subprocess.run(["bash", str(GUARD)], cwd=str(origin_repo), capture_output=True, text=True)
    assert got.returncode == 2
    assert "使い方" in got.stdout + got.stderr


# --- 宣言を読めないときは落とす（I3・I4） ------------------------------------------------


def test_without_section_fails(origin_repo: Path) -> None:
    """宛先のブランチにセクションが無ければ、既定の判定へ倒さずに作るコマンドを示して落とす。"""
    got = guard(origin_repo, "feature/x")
    assert got.returncode == 1
    assert "branch_strategy" in got.stdout


def test_broken_section_fails(origin_repo: Path) -> None:
    section = preset("web-develop")
    del section["branches"]
    publish(origin_repo, section)
    got = guard(origin_repo, "develop")
    assert got.returncode == 1
    assert "スキーマ" in got.stdout


def legacy_worktree_key() -> str:
    """移す行の表から、`.ndf/worktree.json` の最上位にある旧いキーの名前を 1 つ引く（キーの名前を写さない）。"""
    if str(LIB) not in sys.path:
        sys.path.insert(0, str(LIB))
    import branch_strategy_legacy as legacy

    return next(m.old[1:] for m in legacy.load_moves() if m.file == "worktree" and m.old.count("/") == 1)


def test_legacy_keys_on_the_target_fail(origin_repo: Path) -> None:
    """旧いキーが宛先に残れば、キーの位置と移行のコマンドを示して落とす。"""
    publish(origin_repo, preset("web-develop"), {"worktree.json": {"version": 1, legacy_worktree_key(): "develop"}})
    push_branch(origin_repo, "develop")
    got = guard(origin_repo, "develop")
    assert got.returncode == 1
    assert "worktree.json" in got.stdout and "migrate" in got.stdout


# --- trunk ----------------------------------------------------------------------------


def test_trunk_passes_any_head(origin_repo: Path) -> None:
    """trunk ではベースブランチと本番ブランチが同じなので、既定ブランチ宛でも通す。"""
    publish(origin_repo, preset("plugin"))
    assert guard(origin_repo, "feature/x").returncode == 0


# --- merge_base_branch ----------------------------------------------------------------


def test_absent_base_branch_passes(origin_repo: Path) -> None:
    """ベースブランチをまだ作っていない間は、すべての Pull Request を通す。"""
    publish(origin_repo, preset("web-develop"))
    assert guard(origin_repo, "feature/x").returncode == 0


def test_branch_with_matching_tail_does_not_enable_the_guard(origin_repo: Path) -> None:
    """origin への問い合わせは完全な参照名で照合する（`refs/heads/feature/develop` を develop と取り違えない）。"""
    publish(origin_repo, preset("web-develop"))
    push_branch(origin_repo, "feature/develop")
    assert guard(origin_repo, "feature/x").returncode == 0


def test_from_base_branch_passes(origin_repo: Path) -> None:
    publish(origin_repo, preset("web-develop"))
    push_branch(origin_repo, "develop")
    assert guard(origin_repo, "develop").returncode == 0


def test_from_other_branch_fails(origin_repo: Path) -> None:
    publish(origin_repo, preset("web-develop"))
    push_branch(origin_repo, "develop")
    got = guard(origin_repo, "feature/x")
    assert got.returncode == 1
    message = got.stdout + got.stderr
    assert "develop" in message and "--base" in message


def test_working_tree_declaration_does_not_change_the_verdict(origin_repo: Path) -> None:
    """宣言は宛先のブランチから読む。作業ツリーの宣言を trunk へ書き換えても feature/x は通らない。"""
    publish(origin_repo, preset("web-develop"))
    push_branch(origin_repo, "develop")
    write_section(origin_repo, preset("plugin"))
    assert guard(origin_repo, "feature/x").returncode == 1


def release_world(repo: Path) -> tuple[str, str]:
    """origin の develop の祖先にある版上げのコミットと、develop に無いコミットを返す。"""
    publish(repo, preset("web-develop"))
    bumped = commit_on(repo, "bump.txt")
    after = commit_on(repo, "later.txt")
    run_git(repo, "push", "-q", "origin", f"{after}:refs/heads/develop")
    run_git(repo, "checkout", "-q", "-b", "side", bumped)
    stray = commit_on(repo, "stray.txt")
    run_git(repo, "push", "-q", "origin", f"{stray}:refs/heads/release/v10.28.0")
    return bumped, stray


def test_release_branch_inside_the_base_passes(origin_repo: Path) -> None:
    """先頭が origin/develop の祖先の release/v* は通す（develop が先へ進んでいても）。"""
    bumped, _ = release_world(origin_repo)
    assert guard(origin_repo, "release/v10.28.0", bumped).returncode == 0


def test_release_branch_outside_the_base_fails(origin_repo: Path) -> None:
    """develop に含まれないコミットを持つ release/v* は落とす。"""
    _, stray = release_world(origin_repo)
    got = guard(origin_repo, "release/v10.28.0", stray)
    assert got.returncode == 1 and "develop" in got.stdout + got.stderr


def test_release_branch_without_the_head_sha_fails(origin_repo: Path) -> None:
    """head の SHA を渡さない release/v* は、名前だけでは通さない。"""
    release_world(origin_repo)
    assert guard(origin_repo, "release/v10.28.0").returncode == 1


def test_other_branch_inside_the_base_fails(origin_repo: Path) -> None:
    """develop に含まれていても、release/v* でも develop でもない head は落とす。"""
    bumped, _ = release_world(origin_repo)
    assert guard(origin_repo, "feature/x", bumped).returncode == 1


# --- release_branch（選んだスプリントブランチを集める。#3121 の決定 12・14） ---------------------


def selected_world(repo: Path) -> dict[str, str]:
    """main に release_branch の宣言を置き、main から切った sprint/a・sprint/b と、develop にだけある別のスプリントの PR を作る。"""
    publish(repo, preset("mobile"))
    out = {}
    for name in ("a", "b"):
        run_git(repo, "checkout", "-q", "-b", f"sprint/{name}", "main")
        out[name] = commit_on(repo, f"{name}.txt")
        run_git(repo, "push", "-q", "origin", f"sprint/{name}")
    run_git(repo, "checkout", "-q", "-b", "develop", "main")
    run_git(repo, "merge", "-q", "--no-ff", "--no-edit", "sprint/a")
    out["stray"] = commit_on(repo, "pr-3078.txt")  # 2026-11-12 のリリース 9 で develop にだけ入った別のスプリントの PR
    out["dev-bump"] = commit_on(repo, "version-dev.txt")
    run_git(repo, "push", "-q", "origin", "develop")
    return out


def release(repo: Path, *merge: str) -> str:
    run_git(repo, "checkout", "-q", "-B", "release/v2.0.0", "origin/main")
    for ref in merge:
        run_git(repo, "merge", "-q", "--no-ff", "--no-edit", ref)
    sha = commit_on(repo, "bump-2.0.0.txt")
    run_git(repo, "push", "-q", "-f", "origin", "release/v2.0.0")
    return sha


def test_selected_rejects_pull_requests_from_the_base_branch(origin_repo: Path) -> None:
    """develop から main への Pull Request は断る。"""
    selected_world(origin_repo)
    got = guard_main(origin_repo, "develop", run_git(origin_repo, "rev-parse", "origin/develop"))
    assert got.returncode == 1 and "release/v" in got.stdout + got.stderr


def test_selected_passes_a_release_branch_of_approved_sprints(origin_repo: Path) -> None:
    """main の子孫で、スプリントブランチと組むコミットだけの release/v<版> は通す。"""
    selected_world(origin_repo)
    assert guard_main(origin_repo, "release/v2.0.0", release(origin_repo, "origin/sprint/a")).returncode == 0


def test_selected_rejects_commits_that_only_the_base_branch_has(origin_repo: Path) -> None:
    """develop にだけある別のスプリントの PR・開発版の版上げを含む head は断る。"""
    w = selected_world(origin_repo)
    got = guard_main(origin_repo, "release/v2.0.0", release(origin_repo, "origin/sprint/a", "origin/develop"))
    assert got.returncode == 1
    assert w["stray"][:12] in got.stdout and w["dev-bump"][:12] in got.stdout


def test_selected_rejects_a_release_branch_not_cut_from_main(origin_repo: Path) -> None:
    selected_world(origin_repo)
    run_git(origin_repo, "checkout", "-q", "-B", "release/v2.0.0", "origin/main~1")  # 宣言を置く前の main から切った
    sha = commit_on(origin_repo, "old.txt")
    assert guard_main(origin_repo, "release/v2.0.0", sha).returncode == 1


def test_selected_cannot_be_loosened_by_the_worktree_declaration(origin_repo: Path) -> None:
    """宣言は宛先のブランチから読む。作業ツリーの宣言を merge_base_branch へ書き換えても develop は通らない。"""
    selected_world(origin_repo)
    write_section(origin_repo, preset("web-develop"))
    assert guard_main(origin_repo, "develop", run_git(origin_repo, "rev-parse", "origin/develop")).returncode == 1


def test_selected_passes_any_sprint_branch_and_leaves_approval_to_the_gate(origin_repo: Path) -> None:
    """どのスプリントを承認したかは CI では分からない。承認していないスプリントブランチを足しても通し、
    承認したコミットとの照合はマージのステップが持つ。"""
    selected_world(origin_repo)
    assert guard_main(origin_repo, "release/v2.0.0", release(origin_repo, "origin/sprint/a", "origin/sprint/b")).returncode == 0


def test_unknown_release_strategy_is_rejected_by_the_schema(origin_repo: Path) -> None:
    section = copy.deepcopy(preset("web-develop"))
    section["release_strategy"] = "whole"
    publish(origin_repo, section)
    assert guard(origin_repo, "develop").returncode == 1
