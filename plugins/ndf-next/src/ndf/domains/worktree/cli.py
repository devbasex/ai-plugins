"""`ndf worktree` の副命令。

    ndf worktree create <ブランチ> [--from <起点>]   # 作業場所を用意する（結果の outputs に worktree と branch）
    ndf worktree list                               # 置き場 .worktrees/ の下の worktree の一覧
    ndf worktree cleanup [--dry-run]                # マージ済みのブランチの worktree を片付ける
    ndf worktree session                            # メインディレクトリの逸脱を伝え、宣言で有効ならブランチを追従する
    ndf worktree guard [--base <dir>] [--unread] <パス>...   # 書き込み先がメインディレクトリかの案内
    ndf worktree merged <PR番号>... [--flow-record <JSON>]   # マージの後の後片付け（worktree・ブランチ・取り込み）
    ndf worktree trash --ref <コミット> [--yes --only <退避先>...]   # 本番に出た退避先を挙げる（承認した名前だけ消す）
    ndf worktree init [--force] | check             # 宣言を作る・宣言の状態を出す
    ndf worktree deps|localenv|testenv ...          # worktree の環境（cli_env）

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
from ndf.domains.worktree import (
    cleanup,
    cleanup_decide,
    cli_env,
    create,
    declaration,
    deps,
    follow,
    guard_decide,
    localenv,
    merged,
    repo,
    session_decide,
    setup,
    testenv,
    trash,
)

TOOL = cli.tool_of(__name__)
COMMANDS = {
    "create": "作業場所（ブランチの worktree）を用意する",
    "list": "置き場の下の worktree の一覧",
    "cleanup": "マージ済みのブランチの worktree を片付ける",
    "session": "メインディレクトリの逸脱（未コミットの変更）を伝える文を出す",
    "guard": "書き込み先がメインディレクトリかを判断し、案内の文を出す",
    "merged": "マージ済みの PR のブランチの worktree とブランチを外し、メインディレクトリを取り込む",
    "trash": "本番に出たブランチの退避先を挙げる。承認した名前だけを消す",
    "init": "宣言（.ndf/worktree.json）を作る",
    "check": "宣言の状態とベースブランチ・本番チャネルを出す",
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
    m = sub.add_parser("merged", help=COMMANDS["merged"])
    m.add_argument("prs", nargs="+", type=int, metavar="PR番号")
    m.add_argument("--flow-record", type=Path, default=None, help="スプリントの開始時の記録（branch_flow の写しと production_branch）")
    t = sub.add_parser("trash", help=COMMANDS["trash"])
    t.add_argument("--ref", required=True, help="本番に出たコミット（タグ・本番チャネル）")
    t.add_argument("--yes", action="store_true", help="--only の退避先を消す。人が候補を見て承認したときだけ付ける")
    t.add_argument("--only", nargs="+", default=[], metavar="退避先")
    i = sub.add_parser("init", help=COMMANDS["init"])
    i.add_argument("--force", action="store_true", help="既にある宣言を書き直す")
    sub.add_parser("check", help=COMMANDS["check"])
    cli_env.add_parsers(sub)
    return p


def _create(args: argparse.Namespace, where: repo.Location) -> dict[str, Any]:
    try:
        got = create.create(where.main_dir, args.branch, args.start)
    except create.CreateError as exc:
        return result.stopped(TOOL, exc.kind, str(exc))
    outputs = {"worktree": str(got.path), "branch": got.branch}
    item = {"path": str(got.path), "branch": got.branch, "base": got.base, "start_ref": got.start_ref, "new_branch": got.new_branch}
    return result.result(
        TOOL, result.OK, f"worktree を用意した: {got.path}（起点 {got.base}）", [item], outputs=outputs, warnings=got.warnings
    )


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
    moved = None if where.in_worktree else follow.follow(where.main_dir, decl)
    items = [{"context": x} for x in (text, moved) if x is not None]
    summary = "メインディレクトリに未コミットの変更がある" if text is not None else "伝えることは無い"
    return result.result(TOOL, result.OK, summary, items, {"dirty": len(dirty), "followed": moved is not None})


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


def _merged(args: argparse.Namespace, where: repo.Location) -> dict[str, Any]:
    main = Git(where.main_dir)
    hub = github_mod.GitHub(main.remote_repo(), github_mod.client())
    rec = merged.after_merge(where.main_dir, args.prs, hub, merged.read_flow_record(args.flow_record))
    metrics = {k: rec.count(*k.split(":")) for k in ("worktree:removed", "branch:deleted", "worktree:kept", "branch:kept")}
    summary = f"worktree {metrics['worktree:removed']} 件を外し、ブランチ {metrics['branch:deleted']} 件を消した（残した {metrics['worktree:kept'] + metrics['branch:kept']} 件）"
    if rec.stopped is not None:
        return result.stopped(TOOL, "refused", rec.stopped, items=rec.items)
    return result.result(TOOL, result.OK, summary, rec.items, metrics)


def _trash(args: argparse.Namespace, where: repo.Location) -> dict[str, Any]:
    if args.yes != bool(args.only):
        return result.stopped(TOOL, "invalid", "--yes には承認した退避先の名前を --only で渡す")
    main = Git(where.main_dir)
    target = main.rev_parse(args.ref)
    done = trash.sweep(main, target, args.only) if args.yes else []
    items = [{"name": c.name, "branch": c.branch, "released": c.released, "reason": c.reason, "removed": c.name in done}
             for c in trash.candidates(main, target)]  # fmt: skip
    ready = [i["name"] for i in items if i["released"]]
    nxt = result.command(TOOL, "trash", "--ref", args.ref, "--yes", "--only", *ready) if ready and not args.yes else None
    return result.result(TOOL, result.OK, f"消した {len(done)} 件・回収の候補 {len(ready)} 件（候補は人が見て承認してから消す）",
                         items, {"removed": len(done), "candidates": len(ready)}, next=nxt)  # fmt: skip


def _init(args: argparse.Namespace, where: repo.Location) -> dict[str, Any]:
    try:
        got = setup.init(where.main_dir, args.force)
    except setup.SetupError as exc:
        return result.stopped(TOOL, "refused", str(exc))
    summary = f"宣言を作った: {got.path}（コミットする）" if got.written else f"宣言は既にある: {got.path}"
    return result.result(TOOL, result.OK, summary, outputs={"declaration": str(got.path)}, warnings=list(got.warnings))


def _check(args: argparse.Namespace, where: repo.Location) -> dict[str, Any]:
    got = setup.state(where.main_dir)
    items: list[dict[str, Any]] = [{"declaration": got.state, "detail": got.detail}]
    if got.decl is not None:
        items.append({"base_branch": got.decl.base_branch, "production_branch": got.decl.production_branch,
                      "local_ignored": list(got.decl.local_ignored)})  # fmt: skip
    if got.state == setup.UNREADABLE:
        return result.stopped(TOOL, "precondition", got.detail, items=items)
    return result.result(TOOL, result.OK, f"宣言: {got.state}", items, {"present": got.state == setup.PRESENT})


def _env(run: Callable[[str, argparse.Namespace], dict[str, Any]]) -> Callable[[argparse.Namespace, repo.Location], dict[str, Any]]:
    return lambda args, where: run(TOOL, args)


HANDLERS: dict[str, Callable[[argparse.Namespace, repo.Location], dict[str, Any]]] = {
    "create": _create,
    "list": _list,
    "cleanup": _cleanup,
    "session": _session,
    "guard": _guard,
    "merged": _merged,
    "trash": _trash,
    "init": _init,
    "check": _check,
    deps.SECTION: _env(cli_env.run_deps),
    localenv.SECTION: _env(cli_env.run_localenv),
    testenv.SECTION: _env(cli_env.run_testenv),
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
