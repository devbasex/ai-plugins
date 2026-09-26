"""リポジトリの識別（#1142 の L0）: メインディレクトリ・`owner/repo`・slug・宣言のベースブランチ。

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


def declared_base(root, remote: bool = False) -> str | None:
    """`.ndf/worktree.json` の `base_branch`。`remote` なら `origin/<名前>`。宣言が無い・読めなければ `None`。"""
    f = Path(root) / WORKTREE_DECL
    try:
        v = json.loads(f.read_text(encoding="utf-8")).get("base_branch") if f.is_file() else None
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(v, str) or not v:
        return None
    return f"origin/{v}" if remote else v
