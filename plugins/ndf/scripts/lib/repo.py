"""リポジトリの識別（#1142 の L0）: メインディレクトリ・`owner/repo`・slug・起点（宣言のベースブランチと既定ブランチ）。

git だけで決める（`proc` の上に置く）。`gh repo view` まで使って決めるのは `gh_call.resolve_repo` で、
GitHub を呼ぶのは `gh_*` のモジュールだけにする。
"""

from __future__ import annotations

import json
import os
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


def read_worktree_decl(root, fallback: bool = True) -> tuple[dict, str | None]:
    """`.ndf/worktree.json` の中身と、読めなかった理由。`root` → メインディレクトリの順に探す。

    `fallback=False` なら `root` の直下だけを読み、メインディレクトリへ探しに行かない。
    無ければ `({}, None)`。JSON として読めない・オブジェクトでなければ `({}, 理由)`（理由にファイルを書く）。
    """
    main = main_dir(root) if root and fallback else None
    for base in dict.fromkeys(p for p in (Path(root) if root else None, main) if p):
        f = Path(base) / WORKTREE_DECL
        if not f.is_file():
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            return {}, f"{WORKTREE_DECL}: JSON として読めない（{e}）"
        if not isinstance(data, dict):
            return {}, f"{WORKTREE_DECL}: 最上位がオブジェクトでない"
        return data, None
    return {}, None


def _branch_value(v) -> str | None:
    return v if isinstance(v, str) and v else None


def declared_base(root, remote: bool = False) -> str | None:
    """`.ndf/worktree.json` の `base_branch`。`remote` なら `origin/<名前>`。宣言が無い・読めなければ `None`。"""
    v = _branch_value(read_worktree_decl(root)[0].get("base_branch"))
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


def _ref_exists(root, ref: str) -> bool:
    return proc.git_out(root, "show-ref", "--verify", "--quiet", ref) is not None


def _remote_has_branch(root, remote: str, name: str) -> bool:
    """`git ls-remote --heads` の行の参照名が `refs/heads/<name>` と完全に一致するか。

    `ls-remote` のパターンは参照名の末尾に一致するため、`refs/heads/x/refs/heads/<name>` のような
    別のブランチでも行が返る。問い合わせの成功ではなく、返った行の参照名そのものを照合する。
    """
    want = f"refs/heads/{name}"
    try:
        p = proc.run(
            ["git", "-C", str(root), "ls-remote", "--heads", remote, want],
            check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except OSError:
        return False
    if p.returncode != 0:
        return False
    return any(line.split("\t", 1)[-1].strip() == want for line in p.stdout.splitlines() if "\t" in line)


def existing_base_branch(root, remote: str = "origin") -> tuple[str | None, str]:
    """差分の起点にするベースブランチと、その出所（または決まらない理由）。

    宣言（`.ndf/worktree.json` の `base_branch`）があれば、取得済みの参照 → `remote` への問い合わせの順に
    実在を確かめ、無ければ既定ブランチへ落とさずに `(None, 理由)` を返す。宣言が無ければ `default_branch`。
    """
    name = declared_base(root)
    if name:
        if _ref_exists(root, f"refs/remotes/{remote}/{name}") or _ref_exists(root, f"refs/heads/{name}"):
            return name, "宣言（取得済みの参照）"
        if _remote_has_branch(root, remote, name):
            return name, f"宣言（{remote} へ問い合わせ）"
        return None, f"{name} は {remote} にもローカルにも無い"
    d = default_branch(root)
    if d:
        return d, "既定ブランチ"
    return None, f"宣言も {remote} の HEAD も main / master も無い"


def production_branch(root, decl: dict | None = None) -> tuple[str | None, str]:
    """本番チャネルと、その出所。`.ndf/worktree.json` の `production_branch` → 既定ブランチ（`default_branch`）の順。

    `decl` を渡せばそれを `worktree.json` の中身として使う（読み直さない）。決まらなければ `(None, 理由)`。
    """
    wt = read_worktree_decl(root)[0] if decl is None else decl
    v = _branch_value(wt.get("production_branch"))
    if v:
        return v, f"{WORKTREE_DECL} の production_branch"
    d = default_branch(root)
    if d:
        return d, "既定ブランチ（origin/HEAD → ローカルの main / master）"
    return None, "production_branch も既定ブランチ（origin/HEAD・main・master）も無い"
