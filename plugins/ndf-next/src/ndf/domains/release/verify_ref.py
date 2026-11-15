"""main へマージする前の PR を集めた検証の ref を組み直す（workflow が main 宛ての PR の出来事で打つ。#3572 の決定 9）。

1. 宣言から検証の ref の名前と 2 つ目のマーケットプレイスの名前を読む（`declaration.verify_of`）
2. main 宛ての開いた PR のうち、含めるもの（`verify_decide.included`）を番号の順に並べる
3. 一時の worktree に main の先端を取り出し、PR の先端を順に取り込みのマージで入れる。衝突すればそこで止め、ref を動かさない
4. `marketplace.json` の名前を 2 つ目のマーケットプレイスの名前へ、プラグインの版を検証の版へ書き換えたコミットを足す
5. 前の検証の ref の先端と 4 のコミットを親にし、4 の木を持つコミットを作って fast-forward で push する（強制 push を使わない。I12）

取り込んだ PR の木のスクリプト・依存の導入・hook を実行しない（git のマージとファイルの書き換えだけ）。検証の ref は main へ戻さない（I9）。
"""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from ndf.adapters import git as git_mod
from ndf.adapters.github import PER_PAGE, GitHub, text_of
from ndf.domains.release import gather, verify_decide
from ndf.domains.release import declaration as decl_mod
from ndf.domains.release import plugins as plugins_mod


class VerifyConflict(Exception):
    """PR の取り込みが衝突した（`number` が衝突した PR）。"""

    def __init__(self, number: int, detail: str) -> None:
        super().__init__(detail)
        self.number = number


class Verified(NamedTuple):
    """組んだ検証の ref。`included` は含めた PR の (番号, 先端)。"""

    ref: str
    head: str
    version: str
    included: tuple[tuple[int, str], ...]


def _open_pulls(hub: GitHub, production: str) -> list[verify_decide.OpenPull]:
    """main 宛ての開いた PR を全ページ読む。"""
    out: list[verify_decide.OpenPull] = []
    page = 1
    while True:
        rows = hub.rest(
            "GET", f"/repos/{hub.repo}/pulls", params={"state": "open", "base": production, "per_page": PER_PAGE, "page": page}
        )
        found = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
        for r in found:
            head = r["head"]
            repo = head.get("repo") if isinstance(head, dict) else None
            full = text_of(repo["full_name"]) if isinstance(repo, dict) and "full_name" in repo else ""
            out.append(verify_decide.OpenPull(int(r["number"]), text_of(head["sha"]), text_of(head["ref"]), full, bool(r.get("draft"))))
        if len(found) < PER_PAGE:
            return out
        page += 1


def _merge_pulls(work: git_mod.Git, pulls: Sequence[verify_decide.OpenPull]) -> None:
    """PR の先端を順に取り込みのマージで入れる。衝突すれば `merge --abort` の後に `VerifyConflict`。"""
    for p in pulls:
        ref = gather.fetch_pull(work, p.number)
        if work.attempt("merge", "--quiet", "--no-edit", "--no-verify", git_mod.END_OF_OPTIONS, ref).returncode != 0:
            work.attempt("merge", "--abort")
            raise VerifyConflict(p.number, f"#{p.number} を検証の ref へ取り込めない（ほかの PR と衝突する）")


def _commit_on_top(work: git_mod.Git, previous: str | None, message: str) -> str:
    """今の木を持ち、前の検証の ref の先端 `previous` と HEAD を親にするコミット（初回は HEAD をそのまま使う）。"""
    head = work.rev_parse("HEAD")
    if previous is None:
        return head
    return work.run("commit-tree", "-p", previous, "-p", head, "-m", message, "HEAD^{tree}").strip()


def _previous(repo_git: git_mod.Git, ref: str) -> str | None:
    """origin の検証の ref の先端（無ければ None）。"""
    if repo_git.attempt("ls-remote", "--exit-code", "--heads", "--", git_mod.ORIGIN, ref).returncode != 0:
        return None
    gather.fetch(repo_git, ref)
    return repo_git.rev_parse(f"{git_mod.ORIGIN}/{ref}")


def _rewrite(work_root: Path, marketplace: str, now: datetime) -> str:
    """`marketplace.json` の名前と、プラグインの版を検証の版へ書き換え、先頭のプラグインの検証の版を返す。"""
    found = plugins_mod.load(work_root)
    decl = decl_mod.load(work_root)
    versions = []
    for p in found:
        version = verify_decide.version_of(plugins_mod.version(work_root, p), now)
        plugins_mod.bump(work_root, p, version, decl.version_files)
        versions.append(version)
    path = work_root / plugins_mod.MARKETPLACE
    raw = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(verify_decide.marketplace_of(raw, marketplace), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return versions[0] if versions else verify_decide.version_of("0.0.0", now)


def build(root: Path, hub: GitHub, decl: decl_mod.VerifyDecl, now: datetime) -> Verified:
    """一時の worktree で検証の ref を組み直し、fast-forward で push する（I9・I12）。衝突すれば ref を動かさず `VerifyConflict`。"""
    decl_mod.require_github_flow(root)
    production = decl_mod.production_branch(root)
    repo_git = git_mod.Git(root)
    gather.fetch(repo_git, production)
    previous = _previous(repo_git, decl.ref)
    pulls = verify_decide.included(_open_pulls(hub, production), decl.ref, hub.repo)
    place = Path(tempfile.mkdtemp(prefix="ndf-verify-"))
    path = place / "work"
    repo_git.run("worktree", "add", "--quiet", "--detach", str(path), f"{git_mod.ORIGIN}/{production}")
    try:
        work = git_mod.Git(path)
        _merge_pulls(work, pulls)
        version = _rewrite(path, decl.marketplace, now)
        message = f"Verify: {decl.ref} を組み直した（{version}。含めた PR: {', '.join(f'#{p.number}' for p in pulls) or '無し'}）"
        work.run("add", "--all")
        work.run("commit", "--quiet", "--no-verify", "-m", message)
        head = _commit_on_top(work, previous, message)
        if previous is not None and not work.is_ancestor(previous, head):
            raise git_mod.GitError(f"組んだコミット {head[:12]} が前の検証の ref {previous[:12]} を祖先に持たない（push しない）")
        work.run("push", "--quiet", "--", git_mod.ORIGIN, f"{head}:refs/heads/{decl.ref}")
    finally:
        repo_git.attempt("worktree", "remove", "--force", "--", str(path))
        shutil.rmtree(place, ignore_errors=True)
    return Verified(decl.ref, head, version, tuple((p.number, p.head_sha) for p in pulls))
