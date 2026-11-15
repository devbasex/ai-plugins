"""本番ブランチへマージする前の PR を集めた検証用ブランチを組み直し、検証環境へ配る（workflow が本番ブランチ宛ての PR の出来事で打つ。
#3572 の決定 9・21）。

1. 宣言から検証環境の種類と値を読む（`declaration.verify_of`）
2. 本番ブランチ宛ての開いた PR のうち、含めるもの（`verify_decide.included`）を番号の順に並べる
3. 一時の worktree に本番ブランチの先端を取り出し、PR の head を順に取り込みのマージで入れる。衝突すればそこで止め、ref を動かさない
4. 種類 `marketplace` なら、`marketplace.json` の名前を検証用マーケットプレイスの名前へ、プラグインの版を検証の版へ書き換えたコミットを足す
5. 前の検証用ブランチの先端と今のコミットを親にし、今の木を持つコミットを作って fast-forward で push する（強制 push を使わない。I12）
6. 種類 `deploy` なら、push の後に宣言のコマンド（`{ref}`・`{head}` を置き換えたもの）を流す。`marketplace.json` を読まない

組み立て（含める PR・fast-forward・衝突の止め方）は種類によらず共通で、違うのは配り方だけである。

取り込んだ PR の木のスクリプト・依存の導入・hook を実行しない（git のマージとファイルの書き換えだけ）。一時の worktree を作る・
使う git は起動ごとに `core.hooksPath` を無効にする（`NO_HOOKS`。`core.hooksPath` が追跡されたディレクトリを指す環境で、PR が
変えた hook が `--no-verify` の効かない場面でも走らず、渡した認証情報を読めない）。検証用ブランチは main へ戻さない（I9）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
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


# 起動ごとに hook を無効にする git の設定（存在しないディレクトリを hook の置き場にする）
NO_HOOKS = ("-c", "core.hooksPath=/dev/null")


def _without_hooks(repo_git: git_mod.Git) -> git_mod.Git:
    """`repo_git` と同じ場所で、起動ごとに `NO_HOOKS` を付けて git を動かす `Git`。"""
    runner = repo_git.runner
    return git_mod.Git(repo_git.cwd, lambda args, cwd: runner([*NO_HOOKS, *args], cwd))


class VerifyConflict(Exception):
    """PR の取り込みが衝突した（`number` が衝突した PR）。"""

    def __init__(self, number: int, detail: str) -> None:
        super().__init__(detail)
        self.number = number


class VerifyDeployFailed(Exception):
    """種類 `deploy` のコマンドが落ちた（`code` は終了コード。時間切れ・起動できなければ None）。"""

    def __init__(self, code: int | None, detail: str) -> None:
        super().__init__(detail)
        self.code = code


class VerifyEscape(Exception):
    """PR を取り込んだ木の書き換え先（定義・マーケットプレイス・`version_files`）が一時の worktree の外を指す。"""


class Verified(NamedTuple):
    """組んだ検証用ブランチ。`included` は含めた PR の (番号, head)、`version` は検証の版（`deploy` では None）、`kind` は種類。"""

    ref: str
    head: str
    version: str | None
    included: tuple[tuple[int, str], ...]
    kind: str = decl_mod.VERIFY_KINDS[0]


def _open_pull(raw: dict) -> verify_decide.OpenPull:
    """GitHub の PR の行 1 件を `OpenPull` にする（head の repo が読めなければリポジトリの名前は空）。"""
    head = raw["head"]
    repo = head.get("repo") if isinstance(head, dict) else None
    full = text_of(repo["full_name"]) if isinstance(repo, dict) and "full_name" in repo else ""
    return verify_decide.OpenPull(int(raw["number"]), text_of(head["sha"]), text_of(head["ref"]), full, bool(raw.get("draft")))


def _open_pulls(hub: GitHub, production: str) -> list[verify_decide.OpenPull]:
    """main 宛ての開いた PR を全ページ読む。"""
    out: list[verify_decide.OpenPull] = []
    page = 1
    while True:
        rows = hub.rest("GET", f"/repos/{hub.repo}/pulls", params={"state": "open", "base": production, "per_page": PER_PAGE, "page": page})
        found = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
        out.extend(_open_pull(r) for r in found)
        if len(found) < PER_PAGE:
            return out
        page += 1


def _merge_pulls(work: git_mod.Git, pulls: Sequence[verify_decide.OpenPull]) -> None:
    """PR の先端を順に取り込みのマージで入れる。衝突すれば `merge --abort` の後に `VerifyConflict`。"""
    for p in pulls:
        ref = gather.fetch_pull(work, p.number)
        if not gather.merge_or_abort(work, ("--quiet", "--no-edit", "--no-verify", git_mod.END_OF_OPTIONS, ref)):
            raise VerifyConflict(p.number, f"#{p.number} を検証用ブランチへ取り込めない（ほかの PR と衝突する）")


def _commit_on_top(work: git_mod.Git, previous: str | None, message: str) -> str:
    """今の木を持ち、前の検証用ブランチの先端 `previous` と HEAD を親にするコミット（初回は HEAD をそのまま使う）。"""
    head = work.rev_parse("HEAD")
    if previous is None:
        return head
    return work.run("commit-tree", "-p", previous, "-p", head, "-m", message, "HEAD^{tree}").strip()


def _previous(repo_git: git_mod.Git, ref: str) -> str | None:
    """origin の検証用ブランチの先端（無ければ None）。"""
    if not gather.remote_has_branch(repo_git, ref):
        return None
    gather.fetch(repo_git, ref)
    return repo_git.rev_parse(f"{git_mod.ORIGIN}/{ref}")


def _contained(work_root: Path) -> None:
    """書き換える前に、書き換え先の実体（絶対パス・`..`・シンボリックリンクを解いたもの）が一時の worktree の中にあるかを
    確かめる。外を指せば `VerifyEscape`（承認前の PR の宣言で、利用者のファイルを書き換えない）。"""
    top = work_root.resolve()
    decl = decl_mod.load(work_root)
    targets = [work_root / plugins_mod.MARKETPLACE, *(work_root / rel for rel in decl.version_files)]
    for p in plugins_mod.load(work_root):
        targets += [work_root / p.dir / m for m in plugins_mod.MANIFESTS]
    outside = [str(r) for t in targets if not (r := t.resolve()).is_relative_to(top)]
    if outside:
        raise VerifyEscape(f"検証用ブランチの書き換え先が一時の worktree の外を指す（書き換えない）: {', '.join(outside)}")


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


def _deploy(root: Path, decl: decl_mod.VerifyDecl, head: str) -> None:
    """種類 `deploy` のコマンドを `timeout_seconds` まで流す。終了コードが 0 でなければ `VerifyDeployFailed`（標準エラーの末尾 20 行）。"""
    argv = verify_decide.command_of(decl.command, decl.ref, head)
    try:
        done = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=decl.timeout_seconds, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VerifyDeployFailed(None, f"検証環境へ配るコマンドを流せない: {exc}") from None
    if done.returncode != 0:
        tail = "\n".join(done.stderr.strip().splitlines()[-20:])
        raise VerifyDeployFailed(done.returncode, f"検証環境へ配るコマンドが終了コード {done.returncode} で落ちた: {tail}")


def build(root: Path, hub: GitHub, decl: decl_mod.VerifyDecl, now: datetime) -> Verified:
    """一時の worktree で検証用ブランチを組み直し、fast-forward で push し、種類の配り方を打つ（I9・I12）。

    衝突すれば ref を動かさず `VerifyConflict`。`deploy` のコマンドが落ちれば、検証用ブランチは push のまま `VerifyDeployFailed`。
    """
    production = decl_mod.production_branch(root)
    repo_git = git_mod.Git(root)
    gather.fetch(repo_git, production)
    previous = _previous(repo_git, decl.ref)
    pulls = verify_decide.included(_open_pulls(hub, production), decl.ref, hub.repo)
    place = Path(tempfile.mkdtemp(prefix="ndf-verify-"))
    path = place / "work"
    _without_hooks(repo_git).run("worktree", "add", "--quiet", "--detach", str(path), f"{git_mod.ORIGIN}/{production}")
    try:
        work = _without_hooks(git_mod.Git(path, repo_git.runner))
        _merge_pulls(work, pulls)
        version: str | None = None
        listed = ", ".join(f"#{p.number}" for p in pulls) or "無し"
        message = f"Verify: {decl.ref} を組み直した（含めた PR: {listed}）"
        if decl.kind == decl_mod.VERIFY_KINDS[0]:
            _contained(path)
            version = _rewrite(path, str(decl.marketplace), now)
            message = f"Verify: {decl.ref} を組み直した（{version}。含めた PR: {listed}）"
            work.run("add", "--all")
            work.run("commit", "--quiet", "--no-verify", "-m", message)
        head = _commit_on_top(work, previous, message)
        if previous is not None and not work.is_ancestor(previous, head):
            raise git_mod.GitError(f"組んだコミット {head[:12]} が前の検証用ブランチ {previous[:12]} を祖先に持たない（push しない）")
        work.run("push", "--quiet", "--", git_mod.ORIGIN, f"{head}:refs/heads/{decl.ref}")
    finally:
        repo_git.attempt("worktree", "remove", "--force", "--", str(path))
        shutil.rmtree(place, ignore_errors=True)
    if decl.kind != decl_mod.VERIFY_KINDS[0]:
        _deploy(root, decl, head)
    return Verified(decl.ref, head, version, tuple((p.number, p.head_sha) for p in pulls), decl.kind)
