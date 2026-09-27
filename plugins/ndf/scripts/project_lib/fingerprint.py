"""入力の指紋（決定 6・I9）: 入力のパスの表に当たる追跡ファイルの HEAD の blob と、ブランチの構成。

`git ls-tree -r HEAD` と `git for-each-ref` だけを読み、ファイルを開かない（`check` を 5 秒以内に終えるため）。
コミットしていない手元の編集では古くならない。ロックファイルは入れない。
"""

from __future__ import annotations

import fnmatch
import json
import subprocess
from pathlib import Path

from project_lib import WORKTREE_FILE

WORKFLOWS = ".github/workflows/*"
# 根と 1 段下のファイル名で当てる表
INPUT_NAMES = (
    # 依存の定義
    "composer.json",
    "package.json",
    "pyproject.toml",
    "requirements*.txt",
    "Gemfile",
    "go.mod",
    "Cargo.toml",
    # テストの設定
    "phpunit.xml*",
    "pytest.ini",
    "tox.ini",
    "vitest.config.*",
    "jest.config.*",
    # コンテナ
    "compose*.y*ml",
    "docker-compose*.y*ml",
    # 配布
    "amplify.yml",
    "samconfig.toml",
    "buildspec.yml",
    "appspec.yml",
    # 指示書
    "AGENTS.md",
    "CLAUDE.md",
    "KIRO.md",
    "GEMINI.md",
)
KNOWN_BRANCHES = ("main", "master", "develop")


def _git_text(root, *args) -> str | None:
    try:
        p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout if p.returncode == 0 else None


def is_input(path: str) -> bool:
    """入力のパスの表に当たるか。ワークフローは `.github/workflows/` の直下、ほかは根と 1 段下。"""
    if fnmatch.fnmatchcase(path, WORKFLOWS) and "/" not in path[len(".github/workflows/") :]:
        return True
    parts = path.split("/")
    return len(parts) <= 2 and any(fnmatch.fnmatchcase(parts[-1], pat) for pat in INPUT_NAMES)


def tree(root) -> dict[str, str]:
    """HEAD の木の追跡ファイル（パス → blob）。HEAD が無ければ空。"""
    out = _git_text(root, "ls-tree", "-r", "HEAD")
    files = {}
    for line in (out or "").splitlines():
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if len(parts) == 3 and parts[1] == "blob":
            files[path] = parts[2]
    return files


def inputs(root, files: dict[str, str] | None = None) -> dict[str, str]:
    """入力のパスの表に当たる追跡ファイルの blob（パスの順）。"""
    files = tree(root) if files is None else files
    return {p: files[p] for p in sorted(files) if is_input(p)}


def branch_state(root, declared=()) -> dict:
    """ブランチの構成: origin の HEAD の指す先と、main・master・develop と宣言した名前のうち origin にあるもの。"""
    head = (_git_text(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD") or "").strip()
    refs = (_git_text(root, "for-each-ref", "--format=%(refname:short)", "refs/remotes/origin") or "").split()
    names = set(KNOWN_BRANCHES) | {d for d in declared if d}
    present = sorted(n for n in names if f"origin/{n}" in refs)
    return {"head": head.split("/", 1)[-1] if head else None, "present": present}


def declared_branches(main_dir) -> list[str]:
    """`.ndf/worktree.json` の起点と本番の名前（読めなければ空）。"""
    f = Path(main_dir) / WORKTREE_FILE
    try:
        data = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    return [v for v in branch_names(data) if v]


def branch_names(data: dict) -> list[str]:
    """worktree.json の中身から起点と本番の名前（文字列のものだけ）。"""
    return [v for v in (data.get("base_branch"), data.get("production_branch")) if isinstance(v, str)]


def diff(recorded: dict, current: dict) -> list[str]:
    """入力の指紋の違い（変わった・足された・消えたパス）。"""
    keys = sorted(set(recorded) | set(current))
    return [k for k in keys if recorded.get(k) != current.get(k)]
