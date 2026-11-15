"""プロジェクトの宣言（`.ndf/project.json`）の新しさの判定と、判定の入力の指紋（#3569。旧い `project-decl.py check` と
`project_lib/fingerprint.py` の移し先）。

入力の指紋は、入力のパスの表に当たる追跡ファイルの HEAD の blob と、ブランチの構成である。`git ls-tree -r HEAD` と
`git for-each-ref` だけを読み、ファイルを開かない。コミットしていない手元の編集では古くならない。

判定は次のどれかである。

| 種類 | 条件 |
| --- | --- |
| `missing` | 宣言が無い |
| `broken` | 宣言か `.ndf/worktree.json` が読めない・宣言がスキーマに合わない |
| `stale` | 解析の記録が無い・解析器の版が古い・入力の指紋かブランチの構成が変わった・`--force` |
| `fresh` | それ以外 |
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf import contracts
from ndf.contracts import facts
from ndf.adapters import git
from ndf.domains.records import files, jsonfile

# 解析器の版。測る項目・入力のパスの表を変えたら上げる（上がると check が古いと判定する。旧い project_lib と同じ値）
ANALYZER = 3
SCHEMA = contracts.schema_path("declarations/project")
WORKFLOWS = ".github/workflows/*"
# 根と 1 段下のファイル名で当てる表（依存の定義・テストの設定・コンテナ・デプロイ）。指示書は事実の表 instruction-files の根の名前
INPUT_NAMES = (
    "composer.json",
    "package.json",
    "pyproject.toml",
    "requirements*.txt",
    "Gemfile",
    "go.mod",
    "Cargo.toml",
    "phpunit.xml*",
    "pytest.ini",
    "tox.ini",
    "vitest.config.*",
    "jest.config.*",
    "compose*.y*ml",
    "docker-compose*.y*ml",
    "amplify.yml",
    "samconfig.toml",
    "buildspec.yml",
    "appspec.yml",
)
# ブランチの構成で見る名前（宣言の起点と本番の名前を足す）
KNOWN_BRANCHES = ("main", "master", "develop")
FRESH, MISSING, STALE, BROKEN = "fresh", "missing", "stale", "broken"


class CheckError(Exception):
    """判定できない（git の外・git が失敗した）。"""


class Judged(NamedTuple):
    kind: str
    reasons: list[str]
    inputs: int = 0


def is_input(path: str) -> bool:
    """入力のパスの表に当たるか。ワークフローは `.github/workflows/` の直下、ほかは根と 1 段下。"""
    if fnmatch.fnmatchcase(path, WORKFLOWS) and "/" not in path[len(".github/workflows/") :]:
        return True
    parts = path.split("/")
    names = (*INPUT_NAMES, *(i for i in facts.ids("instruction-files") if "/" not in i))
    return len(parts) <= 2 and any(fnmatch.fnmatchcase(parts[-1], pat) for pat in names)


def tree(repo: git.Git) -> dict[str, str]:
    """HEAD の木の追跡ファイル（パス → blob）。HEAD が無ければ空。"""
    done = repo.attempt("ls-tree", "-r", "HEAD")
    found: dict[str, str] = {}
    for line in done.stdout.splitlines() if done.returncode == 0 else []:
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if len(parts) == 3 and parts[1] == "blob":
            found[path] = parts[2]
    return found


def inputs(repo: git.Git, tracked: Optional[dict[str, str]] = None) -> dict[str, str]:
    """入力のパスの表に当たる追跡ファイルの blob（パスの順）。"""
    got = tree(repo) if tracked is None else tracked
    return {p: got[p] for p in sorted(got) if is_input(p)}


def branch_state(repo: git.Git, declared: tuple[str, ...] = ()) -> dict[str, Any]:
    """リモート名のリモートの HEAD の指す先と、既知のブランチ名と宣言した名前のうちそのリモートにあるもの。"""
    remote = repo.remote()
    head_done = repo.attempt("symbolic-ref", "--short", f"refs/remotes/{remote}/HEAD")
    head = head_done.stdout.strip() if head_done.returncode == 0 else ""
    refs_done = repo.attempt("for-each-ref", "--format=%(refname:short)", f"refs/remotes/{remote}")
    refs = refs_done.stdout.split() if refs_done.returncode == 0 else []
    names = set(KNOWN_BRANCHES) | {d for d in declared if d}
    present = sorted(n for n in names if f"{remote}/{n}" in refs)
    return {"head": head.split("/", 1)[-1] if head else None, "present": present}


def declared_branches(worktree: Optional[dict[str, Any]]) -> tuple[str, ...]:
    """`.ndf/worktree.json` の起点と本番の名前（文字列のものだけ）。"""
    if worktree is None:
        return ()
    return tuple(worktree[k] for k in ("base_branch", "production_branch") if k in worktree and isinstance(worktree[k], str))


def diff(recorded: dict[str, Any], current: dict[str, str]) -> list[str]:
    """入力の指紋の違い（変わった・足された・消えたパス）。"""
    keys = sorted(set(recorded) | set(current))
    return [k for k in keys if (recorded[k] if k in recorded else None) != (current[k] if k in current else None)]


def _read(path: Path) -> Optional[dict[str, Any]]:
    """宣言の JSON の object。無ければ None、読めなければ `JsonFileError`。"""
    return jsonfile.read_object(path) if path.is_file() else None


def repo_dirs(root: Path) -> tuple[git.Git, Path]:
    """作業場所の git とメインディレクトリ。git の外なら `CheckError`。"""
    repo = git.Git(root)
    done = repo.attempt("rev-parse", "--show-toplevel")
    if done.returncode != 0:
        raise CheckError(f"{root} は git の worktree ではない: {done.stderr.strip()}")
    top = git.Git(Path(done.stdout.strip()))
    try:
        main = top.common_dir().parent
    except git.GitError as exc:
        raise CheckError(str(exc)) from exc
    return top, main


def load(main: Path) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    """宣言と `.ndf/worktree.json`。読めない・宣言がスキーマに合わなければ `JsonFileError`。"""
    decl = _read(files.path(main, "project"))
    worktree = _read(files.path(main, "worktree"))
    if decl is not None:
        errs = contracts.schema_errors(decl, SCHEMA)
        if errs:
            raise jsonfile.JsonFileError(f"{files.relpath('project')} が形に合わない: {'; '.join(errs[:5])}")
    return decl, worktree


def _stale_reasons(repo: git.Git, analysis: dict[str, Any], worktree: Optional[dict[str, Any]]) -> list[str]:
    reasons: list[str] = []
    if analysis["analyzer"] < ANALYZER:
        reasons.append(f"解析器の版が古い（{analysis['analyzer']} < {ANALYZER}）")
    reasons += [f"変わった入力: {p}" for p in diff(analysis["inputs"], inputs(repo))]
    now = branch_state(repo, declared_branches(worktree))
    if analysis["branches"] != now:
        reasons.append(f"変わった入力: ブランチの構成（{analysis['branches']} → {now}）")
    return reasons


def judge(root: Path, force: bool) -> Judged:
    """宣言が新しい・無い・古い・壊れているのどれかを決める。判定できなければ `CheckError`。"""
    repo, main = repo_dirs(root)
    try:
        decl, worktree = load(main)
    except jsonfile.JsonFileError as exc:
        return Judged(BROKEN, [f"{exc}。直してから打ち直す"])
    if decl is None:
        return Judged(MISSING, ["宣言が無い"])
    analysis = decl["analysis"] if "analysis" in decl and isinstance(decl["analysis"], dict) else None
    if analysis is None:
        return Judged(STALE, ["解析の記録（analysis）が無い（手で書いた宣言）"])
    reasons = _stale_reasons(repo, analysis, worktree)
    if not reasons and force:
        reasons.append("変わった入力: 強制（--force）")
    return Judged(STALE if reasons else FRESH, reasons, len(analysis["inputs"]))
