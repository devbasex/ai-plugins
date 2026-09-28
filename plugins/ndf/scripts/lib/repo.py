"""リポジトリの識別（#1142 の L0）: メインディレクトリ・`owner/repo`・slug・起点（宣言のベースブランチと既定ブランチ）。

git だけで決める（`proc` の上に置く）。`gh repo view` まで使って決めるのは `gh_call.resolve_repo` で、
GitHub を呼ぶのは `gh_*` のモジュールだけにする。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import proc

ORIGIN_URL = re.compile(r"[:/](?P<owner>[^/:]+)/(?P<name>[^/]+?)(?:\.git)?/?$")
WORKTREE_DECL = Path(".ndf") / "worktree.json"


def main_dir(root) -> Path | None:
    """`root` の worktree が属するメインディレクトリ（`git --git-common-dir` の親）。git でなければ `None`。"""
    common = proc.git_out(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return Path(common).parent if common else None


def owner_repo_from_url(url: str) -> str | None:
    """remote の URL（https・ssh・scp の形）から `owner/repo`。読めなければ `None`。"""
    m = ORIGIN_URL.search((url or "").strip())
    return f"{m.group('owner')}/{m.group('name')}" if m else None


def owner_repo(root, remote: str = "origin") -> str | None:
    """`root` の `remote` の URL から `owner/repo`。"""
    return owner_repo_from_url(proc.git_out(root, "remote", "get-url", remote) or "")


def slug(owner_repo_value: str | None) -> str | None:
    """`owner/repo` → `owner--repo`（ディレクトリ名に使う形）。"""
    return owner_repo_value.replace("/", "--") if owner_repo_value else None


def read_worktree_decl(root) -> tuple[dict, str | None]:
    """`.ndf/worktree.json` の中身と、読めなかった理由。`root` → メインディレクトリの順に探す。

    無ければ `({}, None)`。JSON として読めない・オブジェクトでなければ `({}, 理由)`（理由にファイルを書く）。
    """
    main = main_dir(root) if root else None
    for base in dict.fromkeys(p for p in (Path(root) if root else None, main) if p):
        f = Path(base) / WORKTREE_DECL
        if not f.is_file():
            continue
        return read_json_object(f, WORKTREE_DECL)
    return {}, None


def read_json_object(f: Path, label: str) -> tuple[dict, str | None]:
    """`f` を JSON のオブジェクトとして読む。読めない・オブジェクトでなければ `({}, 理由)`（理由の先頭は `label`）。"""
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {}, f"{label}: JSON として読めない（{e}）"
    if not isinstance(data, dict):
        return {}, f"{label}: 最上位がオブジェクトでない"
    return data, None


def branch_value(v) -> str | None:
    return v if isinstance(v, str) and v else None


def declared_base(root, remote: bool = False) -> str | None:
    """`.ndf/worktree.json` の `base_branch`。`remote` なら `origin/<名前>`。宣言が無い・読めなければ `None`。"""
    v = branch_value(read_worktree_decl(root)[0].get("base_branch"))
    if v is None:
        return None
    return f"origin/{v}" if remote else v


def default_branch(root) -> str | None:
    """既定ブランチ。origin の HEAD の指す先 → ローカルの `main` → `master` の順。どれも無ければ `None`。"""
    head = proc.git_out(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if head:
        return head.split("/", 1)[-1]
    for b in ("main", "master"):
        if proc.git_out(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{b}") is not None:
            return b
    return None


def base_branch(root) -> str | None:
    """開発の起点。`.ndf/worktree.json` の `base_branch` → 既定ブランチ（`default_branch`）の順。決まらなければ `None`。"""
    return declared_base(root) or default_branch(root)


