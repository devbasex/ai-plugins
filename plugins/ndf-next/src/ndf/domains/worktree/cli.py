"""`ndf worktree` の副命令。

    ndf worktree create <ブランチ> [--from <起点>]   # 作業場所を用意する（結果の outputs に worktree と branch）
    ndf worktree list                               # 置き場 .worktrees/ の下の worktree の一覧
    ndf worktree cleanup [--dry-run]                # マージ済みのブランチの worktree を片付ける
    ndf worktree session                            # メインディレクトリの逸脱（未コミットの変更）を伝える文
    ndf worktree guard [--base <dir>] [--unread] <パス>...   # 書き込み先がメインディレクトリかの案内

どの副命令も今の位置（`--cwd`。既定はカレントディレクトリ）のリポジトリを対象にし、宣言はメインディレクトリから読む。
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any, Callable

from ndf import cli
from ndf.adapters import github as github_mod
from ndf.adapters.git import Git, GitError
from ndf.contracts import result
from ndf.domains.worktree import cleanup, cleanup_decide, create, declaration, guard_decide, repo, session_decide

TOOL = cli.tool_of(__name__)
COMMANDS = {
    "create": "作業場所（ブランチの worktree）を用意する",
    "list": "置き場の下の worktree の一覧",
    "cleanup": "マージ済みのブランチの worktree を片付ける",
    "session": "メインディレクトリの逸脱（未コミットの変更）を伝える文を出す",
    "guard": "書き込み先がメインディレクトリかを判断し、案内の文を出す",
}


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=f"ndf {TOOL}")
    p.add_argument("--cwd", type=Path, default=None, help="対象のリポジトリの中の位置（既定はカレントディレクトリ）")
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("create", help=COMMANDS["create"])
    c.add_argument("branch")
    c.add_argument("--from", dest="start", default=None, help="ベースブランチ（既定はスプリントの起点）")
    sub.add_parser("list", help=COMMANDS["list"])
    k = sub.add_parser("cleanup", help=COMMANDS["cleanup"])
    k.add_argument("--dry-run", action="store_true", help="判断だけを出し、何も消さない")
    sub.add_parser("session", help=COMMANDS["session"])
    g = sub.add_parser("guard", help=COMMANDS["guard"])
    g.add_argument("paths", nargs="*")
    g.add_argument("--base", type=Path, default=None, help="相対パスの起点（既定は --cwd）")
    g.add_argument("--unread", action="store_true", help="書き込み先を読み切れなかった（構文を解けないコマンド）")
    return p


def _create(args: argparse.Namespace, where: repo.Location) -> dict[str, Any]:
    try:
        got = create.create(where.main_dir, args.branch, args.start)
    except create.CreateError as exc:
        return result.stopped(TOOL, exc.kind, str(exc))
    outputs = {"worktree": str(got.path), "branch": got.branch}
    item = {"path": str(got.path), "branch": got.branch, "base": got.base, "start_ref": got.start_ref, "new_branch": got.new_branch}
    return result.result(TOOL, result.OK, f"worktree を用意した: {got.path}（起点 {got.base}）", [item], outputs=outputs, warnings=got.warnings)


def _list(args: argparse.Namespace, where: repo.Location) -> dict[str, Any]:
    items = repo.dev_worktrees(where.main_dir, repo.worktrees(Git(where.main_dir)))
    rows = [{"path": str(w.path), "branch": w.branch, "head": w.head, "prunable": w.prunable, "locked": w.locked} for w in items]
    return result.result(TOOL, result.OK, f"worktree は {len(rows)} 件", rows, {"count": len(rows)})


def _cleanup(args: argparse.Namespace, where: repo.Location) -> dict[str, Any]:
    main = Git(where.main_dir)
    hub = github_mod.GitHub(main.remote_repo(), github_mod.client())
    got = cleanup.cleanup(where.main_dir, args.cwd, hub, dry_run=args.dry_run, git=main)
    removed = sum(1 for o in got if o.removed)
    metrics = {"worktrees": len(got), "removed": removed, "branches_deleted": sum(1 for o in got if o.branch_deleted)}
    warnings = [f"{o.path}: {o.note}" for o in got if o.note]
    verb = "消す候補" if args.dry_run else "消した worktree"
    return result.result(TOOL, result.OK, f"{verb}: {sum(1 for o in got if o.verdict == cleanup_decide.REMOVE)} 件（見た {len(got)} 件）",
                         [o.item() for o in got], metrics, warnings=warnings)  # fmt: skip


def _session(args: argparse.Namespace, where: repo.Location) -> dict[str, Any]:
    decl = declaration.load(where.main_dir)
    dirty = repo.dirty_paths(Git(where.main_dir))
    text = session_decide.deviation(dirty, where.in_worktree, decl is not None, decl is not None and decl.follow_branch)
    items = [{"context": text}] if text is not None else []
    summary = "メインディレクトリに未コミットの変更がある" if text is not None else "伝えることは無い"
    return result.result(TOOL, result.OK, summary, items, {"dirty": len(dirty)})


def _guard(args: argparse.Namespace, where: repo.Location) -> dict[str, Any]:
    decl = declaration.load(where.main_dir)
    if decl is None:
        return result.result(TOOL, result.OK, "宣言が無いリポジトリでは案内しない")
    wdir = repo.worktrees_dir(where.main_dir).relative_to(where.main_dir).as_posix()
    place = guard_decide.Place(str(where.main_dir), where.in_worktree, decl.allow_paths, wdir)
    base = os.path.realpath(args.base if args.base is not None else args.cwd)
    paths = guard_decide.flagged(args.paths, base, place)
    got = guard_decide.notice(paths, wdir, unread=args.unread and not where.in_worktree)
    if got is None:
        return result.result(TOOL, result.OK, "案内は要らない")
    return result.result(TOOL, result.OK, got.summary, [{"path": p} for p in paths] + [{"context": got.context}])


HANDLERS: dict[str, Callable[[argparse.Namespace, repo.Location], dict[str, Any]]] = {
    "create": _create,
    "list": _list,
    "cleanup": _cleanup,
    "session": _session,
    "guard": _guard,
}


def _run(args: argparse.Namespace) -> dict[str, Any]:
    args.cwd = Path(os.path.realpath(args.cwd if args.cwd is not None else Path.cwd()))
    try:
        where = repo.locate(Git(args.cwd))
    except GitError as exc:
        return result.stopped(TOOL, "precondition", f"git のリポジトリの中で打つ: {exc}")
    try:
        return HANDLERS[args.command](args, where)
    except declaration.DeclarationError as exc:
        return result.stopped(TOOL, "precondition", str(exc))
    except GitError as exc:
        return result.stopped(TOOL, "unknown", str(exc))
    except github_mod.GitHubError as exc:
        kind = "infra_wait" if exc.retry_after is not None else "unknown"
        return result.stopped(TOOL, kind, str(exc), retry_after=exc.retry_after)


def main(argv: list[str]) -> int:
    return result.emit(_run(_parser().parse_args(argv)))
