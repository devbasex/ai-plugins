"""プロジェクトの設定（`.ndf/project.json`）の新しさの判定と、判定の入力の指紋（#3569。旧い `project-decl.py check` と
`project_lib/fingerprint.py` の移し先）。

入力の指紋は、入力のパスの表に当たる追跡ファイルの HEAD の blob と、ブランチの構成である。`git ls-tree -r HEAD` と
`git for-each-ref` だけを読み、ファイルを開かない。コミットしていない手元の編集では古くならない。

判定は次のどれかである。

| 種類 | 条件 |
| --- | --- |
| `missing` | 設定が無い |
| `broken` | 設定が読めない・スキーマに合わない |
| `stale` | 設定に旧い形のキーがある・解析の記録（`.ndf/analysis/project.json`）が無い・読めない・書き出しの途中のマーカーがある・記録が今の設定の書いた値の指紋に対応しない・解析器のバージョンが古い・入力の指紋かブランチの構成が変わった・`--force` |
| `fresh` | それ以外 |
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ndf import contracts
from ndf.contracts import facts
from ndf.adapters import git
from ndf.core import branch_strategy as bs
from ndf.domains.project import analysis_record
from ndf.domains.records import files, jsonfile

# 解析器のバージョン。測る項目・入力のパスの表を変えたら上げる（上がると check が古いと判定する。旧い project_lib と同じ値）
ANALYZER = 4
SCHEMA = contracts.schema_path("ndf-settings/project")
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
# ブランチの構成で見る名前（設定の起点と本番の名前を足す）
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


def branch_state(repo: git.Git, declared: tuple[str, ...] = (), remote: Optional[str] = None) -> dict[str, Any]:
    """リモート名のリモートの HEAD の指す先と、既知のブランチ名と定義した名前のうちそのリモートにあるもの。

    `remote` を省けば `repo.remote()`（求め済みなら渡して `git worktree list` を打ち直さない）。
    """
    remote = remote if remote is not None else repo.remote()
    head = repo.remote_default_branch(remote)
    refs_done = repo.attempt("for-each-ref", "--format=%(refname:short)", f"refs/remotes/{remote}")
    refs = refs_done.stdout.split() if refs_done.returncode == 0 else []
    names = set(KNOWN_BRANCHES) | {d for d in declared if d}
    present = sorted(n for n in names if f"{remote}/{n}" in refs)
    return {"head": head, "present": present}


def declared_branches(decl: Optional[dict[str, Any]]) -> tuple[str, ...]:
    """設定のブランチ戦略（`branch_strategy.branches`）のベースブランチと本番ブランチの名前（文字列のものだけ）。"""
    section = decl.get(bs.SECTION) if isinstance(decl, dict) else None
    branches = section.get("branches") if isinstance(section, dict) else None
    if not isinstance(branches, dict):
        return ()
    return tuple(branches[k] for k in ("base", "production") if k in branches and isinstance(branches[k], str))


def diff(recorded: dict[str, Any], current: dict[str, str]) -> list[str]:
    """入力の指紋の違い（変わった・足された・消えたパス）。"""
    keys = sorted(set(recorded) | set(current))
    return [k for k in keys if (recorded[k] if k in recorded else None) != (current[k] if k in current else None)]


def _read(path: Path) -> Optional[dict[str, Any]]:
    """設定の JSON の object。無ければ None、読めなければ `JsonFileError`。"""
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


def load(main: Path) -> Optional[dict[str, Any]]:
    """設定（無ければ None）。旧い形のキーを取り除いたコピーで形を確かめ、取り除く前の値を返す（旧い形を壊れているとしない）。
    読めない・取り除いてもスキーマに合わなければ `JsonFileError`。"""
    decl = _read(files.path(main, "project"))
    if decl is not None:
        errs = contracts.schema_errors(analysis_record.strip_legacy(decl)[0], SCHEMA)
        if errs:
            raise jsonfile.JsonFileError(f"{files.relpath('project')} が形に合わない: {'; '.join(errs[:5])}")
    return decl


def _stale_reasons(repo: git.Git, record: dict[str, Any], decl: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if record["analyzer"] < ANALYZER:
        reasons.append(f"解析器のバージョンが古い（{record['analyzer']} < {ANALYZER}）")
    reasons += [f"変わった入力: {p}" for p in diff(record["inputs"], inputs(repo))]
    now = branch_state(repo, declared_branches(decl))
    if record["branches"] != now:
        reasons.append(f"変わった入力: ブランチの構成（{record['branches']} → {now}）")
    return reasons


def _record_reason(main: Path, decl: dict[str, Any]) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """判定に使う解析の記録か、記録を待たずに古いとする理由（旧い形の設定・記録が無い・読めない・途中のマーカー・設定に対応しない）。"""
    legacy = analysis_record.strip_legacy(decl)[1]
    if legacy:
        return None, f"旧い形の値が設定にある: {'・'.join(legacy)}"
    record, why = analysis_record.load(main)
    if record is None:
        return None, why
    if record.get("pending"):
        return None, "前の書き出しが終わっていない"
    if record["written_digest"] != analysis_record.written_digest(decl):
        return None, "設定が解析の記録と対応しない"
    return record, None


def judge(root: Path, force: bool) -> Judged:
    """設定が新しい・無い・古い・壊れているのどれかを決める。判定できなければ `CheckError`。"""
    repo, main = repo_dirs(root)
    try:
        decl = load(main)
    except jsonfile.JsonFileError as exc:
        return Judged(BROKEN, [f"{exc}。直してから打ち直す"])
    if decl is None:
        return Judged(MISSING, ["設定が無い"])
    record, why = _record_reason(main, decl)
    if record is None:
        return Judged(STALE, [str(why)])
    reasons = _stale_reasons(repo, record, decl)
    if not reasons and force:
        reasons.append("変わった入力: 強制（--force）")
    return Judged(STALE if reasons else FRESH, reasons, len(record["inputs"]))
