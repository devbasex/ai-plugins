"""例外リストが増えないことを、ベースブランチとの merge-base と比べて確かめる（設計の決定 4・不変条件 I4）。"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

WORKFLOW = ".github/workflows/script-structure.yml"
CHECKER = "check-ndf-contracts.py"


class RatchetError(Exception):
    """起点の ref を決められない（CI の中では終了コード 3）。"""


def _git(root: Path, *args: str) -> str | None:
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def base_ref(root: Path, given: str | None) -> str:
    """比べる起点の merge-base（`<ref>@<sha>` の ref と sha）。決められなければ `RatchetError`。"""
    ref = given
    if not ref:
        name = os.environ.get("GITHUB_BASE_REF")
        if not name:
            try:
                name = json.loads((root / ".ndf" / "worktree.json").read_text(encoding="utf-8")).get("base_branch")
            except (OSError, ValueError, AttributeError):
                name = None
        if not name:
            raise RatchetError("起点のブランチが分からない（--base・GITHUB_BASE_REF・.ndf/worktree.json の base_branch）")
        ref = f"origin/{name}"
    sha = _git(root, "merge-base", ref, "HEAD")
    if not sha:
        raise RatchetError(f"{ref} と HEAD の merge-base が無い（fetch-depth: 0 で取得したか）")
    return f"{ref}@{sha}"


def _names_at(root: Path, sha: str, allow_rel: str) -> set[str]:
    out = _git(root, "ls-tree", "-r", "--name-only", sha, "--", allow_rel + "/")
    return {Path(p).name for p in (out or "").splitlines() if p.endswith(".json")}


def _row_at(root: Path, sha: str, allow_rel: str, name: str) -> dict:
    try:
        r = json.loads(_git(root, "show", f"{sha}:{allow_rel}/{name}") or "")
    except ValueError:
        return {}
    return r if isinstance(r, dict) else {}


def initial(root: Path, sha: str) -> bool:
    """merge-base の CI が構造チェックを走らせていなければ初版（増加を数えない）。"""
    text = _git(root, "show", f"{sha}:{WORKFLOW}") or ""
    return CHECKER not in text


def added(root: Path, sha: str, allow_rel: str, now: list[dict], name_of) -> list[dict]:
    """merge-base に無く今の木にある項目。同じ差分で同じ kind・name の項目を消した付け替えは数えない。"""
    before = _names_at(root, sha, allow_rel)
    now_names = {name_of(r) for r in now}
    gone = [_row_at(root, sha, allow_rel, f) for f in sorted(before - now_names)]
    removed = {(r.get("kind"), r.get("name")) for r in gone}
    out = []
    for r in now:
        if name_of(r) in before:
            continue
        if (r["kind"], r["name"]) in removed:
            removed.discard((r["kind"], r["name"]))
            continue
        out.append(r)
    return out
