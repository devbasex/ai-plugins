"""副命令 `cross-review`。どの命令も結果 JSON を 1 行出して終える。

    ndf cross-review target-repo [--repo R] [--state-dir D --pr N]       # 対象のリポジトリ（#1707）
    ndf cross-review head --pr N [--repo R]                        # PR の head（依存の欠けは deps_missing。#2447）
    ndf cross-review lock <worktree> --pr N [--tool T]             # 作った直後の lock（#3106）
    ndf cross-review check-worktree <worktree>                     # 管理情報の確かめ（#3109）
    ndf cross-review sync <worktree> --branch B                    # 流し直しの初めの origin への同期（#2440）
    ndf cross-review push-fix <worktree> --branch B [--commit C]   # 修正の push。拒まれたら 1 回だけ取り込む（#2440）
    ndf cross-review release <worktree> --repo-dir D               # NDF の lock だけを外して片付ける（#3106）
    ndf cross-review state-file --state-dir D --pr N [--current M]            # 巻き直しの後の番号から元の状態ファイル（#2770）
    ndf cross-review fix-gate --state-dir D --pr N                 # ラウンドの前の修正の記録の要否（#2770）
    ndf cross-review merge-fix --state-dir D --pr N --file F       # 修正の結果を状態ファイルへ取り込む（#2770）
    ndf cross-review review-scope <worktree> --state-dir D --pr N --purpose P --base B   # レビューの範囲（#3181）
    ndf cross-review finish --summary F [--findings F2]            # 終わり方から結果（#2370・#2444）
    ndf cross-review run --pr N --state-dir D --base B [--worktree W] [--branch B2] [--purpose P]
        [--reviewer R ...] [--max-rounds 3] [--findings F]         # 収束ループを 1 回の起動で回して終わり方から結果

状態ファイルの形: `{"pr": 元の番号, "current_pr": 今の番号, "target_repo": "<所有者>/<リポジトリ>",
"rounds": [{"round": n, "verdict": ..., "head": <SHA>, "fix": {...}}], "reviewed_sections": [...]}`。
"""

from __future__ import annotations

import argparse
import importlib
from pathlib import Path
from typing import Any, Callable, Optional

from ndf import cli
from ndf.adapters import agents, git
from ndf.contracts import result
from ndf.core import route
from ndf.core.converge import participants, worktree
from ndf.domains.crossreview import drive, fixgate_decide, push, repo, scope_decide, store
from ndf.domains.crossreview import finish_decide as fin

TOOL = cli.tool_of(__name__)
# 作業の名前（lock の理由に載せる）
CROSS_REVIEW = TOOL
# PR の head を読むアダプター（依存の欠けを import の時点で見分ける。#2447）
GITHUB_MODULE = "ndf.adapters.github"
# 相手役と修正担当の起動（テストは偽物に差し替える）
LAUNCH: drive.Launcher = agents.run_agent


def _ok(summary: str, items: Optional[list[dict[str, Any]]] = None, **optional: Any) -> int:
    return result.emit(result.result(TOOL, result.OK, summary, items, **optional))


def _stop(kind: str, detail: str, **extra: Any) -> int:
    return result.emit(result.stopped(TOOL, kind, detail, **extra))


# --- 対象のリポジトリと PR の head --------------------------------------------------------


def _state_repo(args: argparse.Namespace) -> Optional[str]:
    if args.state_dir is None or args.pr is None:
        return None
    return store.state_repo(store.state_pr(Path(args.state_dir), args.pr))


def cmd_repo(args: argparse.Namespace) -> int:
    try:
        target = repo.resolve_target_repo(args.repo, _state_repo(args), Path.cwd())
    except repo.RepoUnresolved as exc:
        return _stop("precondition", str(exc))
    return _ok(f"対象のリポジトリ {target.slug}（{target.source}）", [{"kind": "repo", "slug": target.slug, "source": target.source}])


def load_github() -> Any:
    """GitHub のアダプターを読む。依存が欠けていれば `ImportError` をそのまま返す（head を読めないと言い換えない）。"""
    return importlib.import_module(GITHUB_MODULE)


def cmd_head(args: argparse.Namespace) -> int:
    try:
        gh = load_github()
    except ImportError as exc:
        missing = exc.name if exc.name else str(exc)
        return _stop("deps_missing", f"依存の仮想環境に {missing} が無い（PR の head は読んでいない）: {exc}")
    try:
        slug = repo.resolve_target_repo(args.repo, None, Path.cwd()).slug
    except repo.RepoUnresolved as exc:
        return _stop("precondition", str(exc))
    try:
        pull = gh.GitHub(slug, gh.client()).pull(args.pr)
    except gh.GitHubError as exc:
        return _stop("transient" if exc.unreached else "precondition", f"PR #{args.pr} の head を読めない: {exc}")
    return _ok(
        f"PR #{args.pr} の head {pull.head_sha}", [{"kind": "pr", "number": args.pr, "head": pull.head_sha, "branch": pull.head_ref}]
    )


# --- worktree --------------------------------------------------------------------------


def _unregistered(exc: worktree.WorktreeUnregistered, path: str) -> int:
    """管理情報の無い worktree（`path` は結果に載せる worktree のパス）で止まった結果を出す。"""
    return _stop("precondition", str(exc), items=[{"kind": "worktree", "path": path, "points_to": str(exc.points_to)}])


def _registered(path: str) -> Optional[int]:
    """管理情報が無ければ止まった結果の終了コード。あれば None。"""
    try:
        worktree.registration(Path(path))
    except worktree.WorktreeUnregistered as exc:
        return _unregistered(exc, path)
    return None


def cmd_check_worktree(args: argparse.Namespace) -> int:
    stopped = _registered(args.worktree)
    return stopped if stopped is not None else _ok(f"worktree の管理情報がある: {args.worktree}")


def cmd_lock(args: argparse.Namespace) -> int:
    wt = Path(args.worktree)
    try:
        reason = worktree.lock_review_worktree(git.Git(wt), wt, args.tool, args.pr)
    except worktree.LockFailed as exc:
        return _stop("precondition", str(exc))
    return _ok(f"worktree を lock した（{reason}）", [{"kind": "worktree", "path": args.worktree, "locked": reason}])


def cmd_release(args: argparse.Namespace) -> int:
    wt = Path(args.worktree)
    done = worktree.release(git.Git(Path(args.repo_dir)), wt)
    if not done.removed:
        return _stop("refused", f"worktree を外さない: {done.reason}", items=[{"kind": "worktree", "path": args.worktree}])
    return _ok(f"worktree を外した: {args.worktree}")


def cmd_sync(args: argparse.Namespace) -> int:
    stopped = _registered(args.worktree)
    if stopped is not None:
        return stopped
    try:
        got = worktree.sync_worktree(git.Git(Path(args.worktree)), args.branch)
    except worktree.SyncRefused as exc:
        return _stop("precondition", str(exc))
    item = {"kind": "sync", **got._asdict(), "conflicts": list(got.conflicts)}
    if got.conflicts:
        return _stop("precondition", worktree.conflict_detail(got), items=[item])
    return _ok(f"origin の先頭 {got.after} へ合わせ、自分のコミット {got.replayed} 件を載せ直した", [item])


def cmd_push_fix(args: argparse.Namespace) -> int:
    stopped = _registered(args.worktree)
    if stopped is not None:
        return stopped
    got = push.push_fix(git.Git(Path(args.worktree)), args.branch, args.commit)
    item = {"kind": "push", **got._asdict(), "conflicts": list(got.conflicts)}
    if got.ok:
        return _ok(f"修正を送った（取り込み {'あり' if got.rebased else '無し'}）: {got.head}", [item])
    if got.reason == push.REJECTED_TWICE:
        return _stop("transient", "取り込んだ後の送り直しも拒まれた（取り込みの間に別の push が入った）", items=[item])
    if got.reason == push.DIVERGED:
        again = result.command(TOOL, "sync", args.worktree, "--branch", args.branch)
        return _stop("precondition", f"取り込みで衝突したので取り消した: {', '.join(got.conflicts)}", items=[item], next=again)
    return _stop("precondition", f"報告された修正のコミット {args.commit} が HEAD の祖先でない", items=[item])


# --- 状態ファイル -----------------------------------------------------------------------


def _ref_item(ref: store.StateRef) -> dict[str, Any]:
    return {"kind": "state", "origin_pr": ref.origin_pr, "current_pr": ref.current_pr, "path": str(ref.path)}


def cmd_state(args: argparse.Namespace) -> int:
    ref = store.state_pr(Path(args.state_dir), args.pr)
    if args.current is not None:
        ref = store.record_current(ref, args.current)
    fix = store.fix_result_path(Path(args.state_dir), ref)
    return _ok(
        f"状態ファイル {ref.path.name}（元の番号 {ref.origin_pr}・今の番号 {ref.current_pr}）",
        [_ref_item(ref), {"kind": "fix_result", "path": str(fix)}],
    )


def cmd_fix_gate(args: argparse.Namespace) -> int:
    state_dir = Path(args.state_dir)
    ref = store.state_pr(state_dir, args.pr)
    last = store.last_round(store.read_state(ref))
    pending = store.pending_fix_result(state_dir, ref)
    round_no = last.get("round", 0)  # noqa: NDF502 ファイル: ラウンドの記録が無ければ 0 ラウンド目
    gate = fixgate_decide.require_fix_for_changes(round_no, last.get("verdict"), last.get("fix"), str(pending) if pending else None)
    if gate.kind == fixgate_decide.STOP:
        return _stop("work_stopped", str(gate.reason), items=[_ref_item(ref)])
    if gate.kind == fixgate_decide.INGEST:
        again = result.command(TOOL, "merge-fix", "--state-dir", args.state_dir, "--pr", str(args.pr), "--file", str(gate.pending))
        return _ok(
            f"修正の記録は無いが結果ファイルが残っている: {gate.pending}",
            [_ref_item(ref), {"kind": "ingest", "path": gate.pending}],
            next=again,
        )
    return _ok("次のラウンドへ進める", [_ref_item(ref)])


def cmd_ingest(args: argparse.Namespace) -> int:
    """`run` と同じ取り込み（台帳へ反映し、結果ファイルを移す）を `drive.merge_fix` で行う。"""
    try:
        round_no = drive.merge_fix(Path(args.state_dir), args.pr, Path(args.file))
    except store.FixResultMismatch as exc:
        return _stop("invalid", str(exc))
    ref = store.state_pr(Path(args.state_dir), args.pr)
    return _ok(f"修正の結果を round {round_no} へ取り込んだ: {args.file}", [_ref_item(ref)])


# --- 範囲と終わり方 ---------------------------------------------------------------------


def _every_section(purpose: str) -> bool:
    """節を詳細レビューへ必ず通す PR か（設計のプランの雛形の purpose。#3181）。"""
    return purpose == route.templates()["design"].purpose


def cmd_scope(args: argparse.Namespace) -> int:
    ref = store.state_pr(Path(args.state_dir), args.pr)
    data = store.read_state(ref)
    rounds = store.list_of(data, store.ROUNDS)
    wt = Path(args.worktree)
    sections = drive.changed_sections(git.Git(wt), args.base, wt)
    got = scope_decide.state_scope(data, _every_section(args.purpose), len(rounds) + 1, sections)
    item = {"kind": "scope", "scope": got.kind, "base": got.base, "unreviewed": list(got.unreviewed)}
    return _ok(f"round {len(rounds) + 1} の範囲は {got.kind}（詳細レビューを通らない節 {len(got.unreviewed)} 件）", [item])


def _handed(path: Optional[str]) -> tuple[fin.Handed, ...]:
    if path is None:
        return ()
    return fin.as_handed(store.list_of(store.read_object(Path(path)), "items"))


def _review(path: str) -> fin.Review:
    return fin.as_review(store.read_object(Path(path)))


def _emit_verdict(verdict: fin.Verdict, **extra: Any) -> int:
    """終わり方の結果を出す。outputs の gaps は欠けを後のステップ（CI）の後まで引き継ぐ（空なら前の欠けを消す）。"""
    warnings = [*extra.pop("warnings", ()), *verdict.warnings]
    shared = {"decisions": list(verdict.decisions), "warnings": warnings, "outputs": {"gaps": list(verdict.gaps)}, **extra}
    if verdict.status == fin.OK:
        return _ok(verdict.summary, **shared)
    return _stop(str(verdict.kind), verdict.summary, gaps=list(verdict.gaps), **shared)


def cmd_finish(args: argparse.Namespace) -> int:
    try:
        verdict = fin.finish(_review(args.summary), _handed(args.findings))
    except ValueError as exc:
        return _stop("invalid", str(exc))
    return _emit_verdict(verdict)


def _branch_of(wt: Path) -> str:
    return git.Git(wt).run("rev-parse", "--abbrev-ref", "HEAD").strip()


def _run_config(args: argparse.Namespace) -> drive.RunConfig:
    # 子へ渡すパスの基準を打った場所の cwd にそろえる（相対の --state-dir と別の --worktree で基準が割れない）
    wt = (Path(args.worktree) if args.worktree else Path.cwd()).absolute()
    return drive.RunConfig(
        worktree=wt,
        branch=args.branch or _branch_of(wt),
        base=args.base,
        pr=args.pr,
        state_dir=Path(args.state_dir).absolute(),
        every_section=_every_section(args.purpose),
        reviewers=participants.assign(args.reviewer if args.reviewer else ()),
        max_rounds=args.max_rounds,
        handed=_handed(args.findings),
    )


def cmd_run(args: argparse.Namespace) -> int:
    """収束ループを回して終わり方から結果を出す（`{{review}}` が打つ 1 本）。"""
    try:
        cfg = _run_config(args)
    except ValueError as exc:
        # 相手役の名指しの誤り（participants.UnknownParticipant）と、足すべき内容の形の誤り
        return _stop("invalid", str(exc))
    try:
        got = drive.drive(cfg, LAUNCH)
    except worktree.WorktreeUnregistered as exc:
        return _unregistered(exc, str(exc.worktree))
    except worktree.SyncRefused as exc:
        return _stop("precondition", str(exc))
    except drive.DriveStopped as exc:
        return _stop(exc.kind, str(exc))
    verdict = fin.finish(got.review, cfg.handed)
    items = [{"kind": "finding", "id": f.id, "outcome": f.outcome} for f in got.review.findings]
    metrics = {"rounds": got.review.rounds, "findings": len(got.review.findings)}
    return _emit_verdict(verdict, items=items, metrics=metrics, warnings=got.warnings, process=list(got.process))


# --- 引数 --------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=f"{result.ENTRY} {TOOL}")
    sub = p.add_subparsers(dest="command", required=True)
    add = sub.add_parser
    r = add("target-repo")
    r.add_argument("--repo")
    r.add_argument("--state-dir")
    r.add_argument("--pr", type=int)
    h = add("head")
    h.add_argument("--pr", type=int, required=True)
    h.add_argument("--repo")
    lk = add("lock")
    lk.add_argument("worktree")
    lk.add_argument("--pr", type=int, required=True)
    lk.add_argument("--tool", default=CROSS_REVIEW)
    add("check-worktree").add_argument("worktree")
    rl = add("release")
    rl.add_argument("worktree")
    rl.add_argument("--repo-dir", required=True)
    _with_branch(add("sync"))
    _with_branch(add("push-fix")).add_argument("--commit")
    _state_parsers(add)
    f = add("finish")
    f.add_argument("--summary", required=True)
    f.add_argument("--findings")
    _run_parser(add)
    return p


def _run_parser(add: Callable[..., argparse.ArgumentParser]) -> None:
    r = add("run")
    r.add_argument("--pr", type=int, required=True)
    r.add_argument("--state-dir", required=True)
    r.add_argument("--base", required=True)
    r.add_argument("--worktree")
    r.add_argument("--branch")
    r.add_argument("--purpose", default=TOOL)
    r.add_argument("--reviewer", action="append")
    r.add_argument("--max-rounds", type=int, default=drive.DEFAULT_MAX_ROUNDS)
    r.add_argument("--findings")


def _with_branch(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """worktree と PR のブランチを受ける副命令の共通の引数を足す。"""
    parser.add_argument("worktree")
    parser.add_argument("--branch", required=True)
    return parser


def _with_state(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """状態ファイルを読む副命令の共通の引数（--state-dir・--pr）を足す。"""
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--pr", type=int, required=True)
    return parser


def _state_parsers(add: Callable[..., argparse.ArgumentParser]) -> None:
    _with_state(add("state-file")).add_argument("--current", type=int)
    _with_state(add("fix-gate"))
    _with_state(add("merge-fix")).add_argument("--file", required=True)
    scope = add("review-scope")
    scope.add_argument("worktree")
    scope.add_argument("--purpose", required=True)
    scope.add_argument("--base", required=True)
    _with_state(scope)


HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "target-repo": cmd_repo,
    "head": cmd_head,
    "lock": cmd_lock,
    "check-worktree": cmd_check_worktree,
    "release": cmd_release,
    "sync": cmd_sync,
    "push-fix": cmd_push_fix,
    "state-file": cmd_state,
    "fix-gate": cmd_fix_gate,
    "merge-fix": cmd_ingest,
    "review-scope": cmd_scope,
    "finish": cmd_finish,
    "run": cmd_run,
}


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    try:
        return HANDLERS[args.command](args)
    except store.StateBroken as exc:
        return _stop("invalid", str(exc))
    except git.GitError as exc:
        return _stop("precondition", f"git の操作が失敗した: {exc}")
