"""開発版の `bump` の後も、指示書の読み込み量の判定が Pull Request の時点と変わらない（#1739）。

`release-steps.py bump` は版数を持つ箇所を書き換える。指示書（`AGENTS.md` / `CLAUDE.md`）を
書き換えると、Pull Request の CI で上限ちょうどだった読み込み量が、配布の `sync-check` で初めて
上限を超える。ここでは ai-plugins の形の一時リポジトリで `bump` → `instructions-check.py` を順に
走らせ、指示書の内容と判定が `bump` の前後で同じことを確かめる。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
RELEASE_STEPS = SCRIPTS / "release-steps.py"
INSTRUCTIONS_CHECK = SCRIPTS / "instructions-check.py"
# instructions-check.py が上限を超えたときに出す文（read-size-budget）
OVER_BUDGET = "読み込みの量が上限を超えた"
INSTRUCTIONS = ("AGENTS.md", "CLAUDE.md")


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)


def write(root: Path, rel: str, body: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def plugin_rel(plugin: str) -> str:
    return f"plugins/{plugin}" if plugin in ("ndf", "playwright-kit") else f"plugins/mcp/{plugin}"


def make_repo(tmp_path: Path, version: str, *, plugin: str = "ndf", agents: str | None = None) -> Path:
    """ai-plugins の形のリポジトリ。`AGENTS.md` は既定で前の形の版数の行（「主要プラグインです（v…）」）を持つ。"""
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    git(root, "config", "gc.auto", "0")
    write(root, ".ndf/worktree.json", json.dumps({"version": 1, "base_branch": "develop", "production_branch": "main"}))
    write(root, f"{plugin_rel(plugin)}/.claude-plugin/plugin.json", json.dumps({"version": version}, indent=2))
    write(root, "README.md", f"**NDFプラグイン v{version}** の概要\n\n| **{plugin}** | {version} | 説明 |\n")
    if agents is None:
        agents = f"# 指示書\n\n**NDFプラグイン**は主要プラグインです（v{version}）。\n"
    write(root, "AGENTS.md", agents)
    write(root, "CLAUDE.md", "@AGENTS.md\n")
    return root


def declare(root: Path, budget: int | None) -> None:
    decl: dict = {"version": 1, "files": list(INSTRUCTIONS), "imports": {"CLAUDE.md": {"AGENTS.md": "規約そのもの"}}}
    if budget is not None:
        decl["budget"] = {"bytes": budget}
    write(root, ".ndf/instructions.json", json.dumps(decl, ensure_ascii=False))


def read_size(root: Path) -> int:
    """`CLAUDE.md` の読み込み量（`@AGENTS.md` の取り込み先を含む）。"""
    return sum(len((root / name).read_bytes()) for name in INSTRUCTIONS)


def commit(root: Path) -> None:
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "t")
    git(root, "update-ref", "refs/remotes/origin/develop", "HEAD")


def check(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(INSTRUCTIONS_CHECK), "--root", str(root)], capture_output=True, text=True)


def bump(root: Path, plugin: str, to: str) -> dict:
    p = subprocess.run(
        [sys.executable, str(RELEASE_STEPS), "bump", "--root", str(root), "--plugin", plugin, "--to", to],
        capture_output=True,
        text=True,
    )
    assert p.returncode == 0, p.stdout + p.stderr
    return json.loads(p.stdout.strip().splitlines()[-1])


def at_budget(root: Path) -> None:
    """上限を読み込み量ちょうどにし、Pull Request の CI と同じ検査が通る状態でコミットする。"""
    declare(root, read_size(root))
    commit(root)
    pr = check(root)
    assert pr.returncode == 0, pr.stderr
    assert OVER_BUDGET not in pr.stderr


def snapshot(root: Path) -> dict[str, bytes]:
    return {name: (root / name).read_bytes() for name in INSTRUCTIONS}


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("1.2.3", "1.2.4-dev.1"),
        ("1.2.4-dev.9", "1.2.4-dev.10"),
        ("1.2.99", "1.2.100-dev.1"),
        ("1.2.3", "1.3.0-rc.1"),
    ],
)
def test_bump_keeps_instructions_within_the_budget_passed_in_the_pr(tmp_path, old, new):
    """受け入れ条件 1・2: PR の CI で上限ちょうどで通った指示書は、開発版の bump の後も上限で落ちない。"""
    root = make_repo(tmp_path, old)
    at_budget(root)
    before = snapshot(root)

    res = bump(root, "ndf", new)

    assert json.loads((root / "plugins/ndf/.claude-plugin/plugin.json").read_text())["version"] == new
    assert f"**NDFプラグイン v{new}**" in (root / "README.md").read_text(encoding="utf-8"), res
    assert snapshot(root) == before
    after = check(root)
    assert after.returncode == 0, after.stderr
    assert OVER_BUDGET not in after.stderr


def test_growth_unrelated_to_the_version_still_fails(tmp_path):
    """受け入れ条件 3: 版数と無関係に 100 バイト伸びた指示書は、これまでどおり上限で終了コード 1 になる。"""
    root = make_repo(tmp_path, "1.2.3")
    at_budget(root)
    bump(root, "ndf", "1.2.4-dev.1")

    with (root / "AGENTS.md").open("a", encoding="utf-8") as f:
        f.write("x" * 99 + "\n")
    proc = check(root)
    assert proc.returncode == 1
    assert OVER_BUDGET in proc.stderr


def test_other_plugin_bump_and_instructions_without_a_version_line(tmp_path):
    """受け入れ条件 5: 版数の行を持たない指示書と、ndf 以外のプラグインの bump でも同じく判定する。"""
    root = make_repo(tmp_path, "0.4.2", plugin="mcp-serena", agents="# 指示書\n\n版数は plugin.json を見る。\n")
    at_budget(root)
    before = snapshot(root)

    bump(root, "mcp-serena", "0.4.3-dev.1")

    assert snapshot(root) == before
    proc = check(root)
    assert proc.returncode == 0, proc.stderr
    assert OVER_BUDGET not in proc.stderr


@pytest.mark.parametrize("declared", [False, True])
def test_no_budget_means_no_size_finding(tmp_path, declared):
    """受け入れ条件 4: 宣言が無い・宣言に budget が無いリポジトリでは、読み込み量で落とさない。"""
    root = make_repo(tmp_path, "1.2.3", agents="# 指示書\n\n" + "規約の本文。\n" * 3000)
    if declared:
        declare(root, None)
    commit(root)
    proc = check(root)
    assert OVER_BUDGET not in proc.stderr
