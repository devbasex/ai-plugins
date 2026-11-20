"""サブコマンド `cross-review`。どの命令も結果 JSON を 1 行出して終える。

    ndf cross-review target-repo [--repo R] [--state-dir D --pr N]       # 対象のリポジトリ（#1707）
    ndf cross-review head --pr N [--repo R]                        # PR の head（依存の欠けは deps_missing。#2447）
    ndf cross-review lock <worktree> --pr N [--tool T]             # 作った直後の lock（#3106）
    ndf cross-review check-worktree <worktree>                     # 管理情報の確かめ（#3109）
    ndf cross-review sync <worktree> --branch B                    # 流し直しの初めの origin への同期（#2440）
    ndf cross-review push-fix <worktree> --branch B [--commit C]   # 修正の push。拒まれたら 1 回だけマージする（#2440）
    ndf cross-review release <worktree> --repo-dir D               # NDF の lock だけを外して片付ける（#3106）
    ndf cross-review state-file --state-dir D --pr N [--current M]            # 巻き直しの後の番号から元の状態ファイル（#2770）
    ndf cross-review fix-gate --state-dir D --pr N [--findings F]  # ラウンドの前の修正の記録の要否（#2770）
    ndf cross-review merge-fix --state-dir D --pr N --file F [--findings F] # 修正の結果を状態ファイルへ読み込む（#2770）
    ndf cross-review review-scope <worktree> --state-dir D --pr N --purpose P --base B   # レビューの範囲（#3181）
    ndf cross-review finish --summary F [--findings F2]            # 終わり方から結果（#2370・#2444）
    ndf cross-review run --pr N --state-dir D --base B [--worktree W] [--branch B2] [--purpose P]
        [--reviewer R ...] [--max-rounds 3] [--findings F] [--reviewed-out F3]
                                                                   # 収束ループを 1 回の起動で回して終わり方から結果。指摘のスレッドと記録を PR へ書く（#3939）
    ndf cross-review report --state-dir D --pr N [--repo R]       # 状態ファイルから指摘のスレッドと記録を PR へ書く（#3939）

状態ファイルの形: `{"pr": 元の番号, "current_pr": 今の番号, "target_repo": "<所有者>/<リポジトリ>",
"rounds": [{"round": n, "verdict": ..., "head": <SHA>, "fix": {...}}], "reviewed_sections": [...]}`。
"""

from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
from typing import Any, Callable, NamedTuple, Optional

from ndf import cli
from ndf.adapters import agents, git
from ndf.contracts import facts, result
from ndf.core import route
from ndf.core.converge import location, participants, worktree
from ndf.domains.crossreview import criteria_decide, drive, fixgate_decide, push, repo, report_decide, scope_decide, store, threads
from ndf.domains.crossreview import finish_decide as fin

TOOL = cli.tool_of(__name__)
# 作業の名前（lock の理由に載せる）
CROSS_REVIEW = TOOL
# PR の head を読むアダプター（依存の欠けを import の時点で見分ける。#2447）
GITHUB_MODULE = "ndf.adapters.github"
# 相手役と修正担当の起動（テストは偽物に差し替える）
LAUNCH: drive.Launcher = agents.run_agent


class Hub(NamedTuple):
    """PR へ書く口と読む口。`comment(PR, 本文, 前に書いたコメントの URL)` は前のコメントがあれば書き換え、無ければ足して URL を返す。
    `reader` は PR のコメントとスレッドを読む口（#3941）。"""

    comment: Callable[[int, str, Optional[str]], str]
    threads: Callable[[int], threads.Port]
    reader: Callable[[int], threads.Reader]


def _github_hub(repo_arg: Optional[str], state_repo: Optional[str], cwd: Path) -> Hub:
    """対象のリポジトリ（`repo.resolve_target_repo` の順で決める）の GitHub へ書く口。書く本文は `publish.Publisher` を通す。"""
    slug = repo.resolve_target_repo(repo_arg, state_repo, cwd).slug
    gh = load_github()
    from ndf.adapters import publish

    hub = gh.GitHub(slug, gh.client())
    publisher = publish.Publisher(hub)

    def comment(pr: int, body: str, previous: Optional[str]) -> str:
        cid = report_decide.comment_id(previous)
        if cid is None:
            return str(threads.patient(lambda: publisher.comment(pr, body), gh.GitHubError).url)
        (safe,), _ = publisher.guarded(body)
        patched = threads.patient(lambda: hub.rest("PATCH", f"/repos/{slug}/issues/comments/{cid}", {"body": safe}), gh.GitHubError)
        return str(patched["html_url"])

    def port(pr: int) -> threads.GitHubPort:
        return threads.GitHubPort(hub, publisher, pr, gh.GitHubError)

    return Hub(comment, port, port)


# PR へ書く口を作る（テストは偽物に差し替える。#3939）
HUB: Callable[[Optional[str], Optional[str], Path], Hub] = _github_hub


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
        return _ok(f"修正を送った（マージ {'あり' if got.rebased else '無し'}）: {got.head}", [item])
    if got.reason == push.REJECTED_TWICE:
        return _stop("transient", "マージした後の送り直しも拒まれた（マージの間に別の push が入った）", items=[item])
    if got.reason == push.DIVERGED:
        again = result.command(TOOL, "sync", args.worktree, "--branch", args.branch)
        return _stop("precondition", f"マージで衝突したので取り消した: {', '.join(got.conflicts)}", items=[item], next=again)
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
        handed = ("--findings", args.findings) if args.findings else ()
        again = result.command(TOOL, "merge-fix", "--state-dir", args.state_dir, "--pr", str(args.pr), "--file", str(gate.pending), *handed)
        return _ok(
            f"修正の記録は無いが結果ファイルが残っている: {gate.pending}",
            [_ref_item(ref), {"kind": "ingest", "path": gate.pending}],
            next=again,
        )
    return _ok("次のラウンドへ進める", [_ref_item(ref)])


def cmd_ingest(args: argparse.Namespace) -> int:
    """`run` と同じマージ（ジャーナルへ反映し、結果ファイルを移す）を `drive.merge_fix` で行う。"""
    try:
        round_no = drive.merge_fix(Path(args.state_dir), args.pr, Path(args.file), _handed(args.findings))
    except (store.FixResultMismatch, ValueError) as exc:
        return _stop("invalid", str(exc))
    ref = store.state_pr(Path(args.state_dir), args.pr)
    return _ok(f"修正の結果を round {round_no} へマージした: {args.file}", [_ref_item(ref)])


# --- 範囲と終わり方 ---------------------------------------------------------------------


def _every_section(purpose: str) -> bool:
    """セクションを詳細レビューへ必ず通す PR か（設計のプランのテンプレートの purpose。#3181）。"""
    return purpose == route.templates()["design"].purpose


def cmd_scope(args: argparse.Namespace) -> int:
    ref = store.state_pr(Path(args.state_dir), args.pr)
    data = store.read_state(ref)
    rounds = store.list_of(data, store.ROUNDS)
    wt = Path(args.worktree)
    sections = drive.changed_sections(git.Git(wt), args.base, wt)
    got = scope_decide.state_scope(data, _every_section(args.purpose), len(rounds) + 1, sections, args.base)
    item = {"kind": "scope", "scope": got.kind, "base": got.base, "unreviewed": list(got.unreviewed)}
    return _ok(f"round {len(rounds) + 1} の範囲は {got.kind}（詳細レビューを通らないセクション {len(got.unreviewed)} 件）", [item])


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


def _settings_path(root: Path, file_id: str) -> Path:
    """設定のファイル（表 ndf-files の行）のパス（`root` の下）。"""
    where = facts.value("ndf-files", file_id, "location")
    return root / facts.value("locations", where, "path") / facts.value("ndf-files", file_id, "path")


def _focus(wt: Path) -> tuple[str, ...]:
    """worktree の `.ndf/review.json` の重点（基準 3。#3941）。無ければ空。読めない・形が違えば `ReviewDeclInvalid`。"""
    path = _settings_path(wt, "review")  # 表 ndf-files の行
    if not path.is_file():
        return ()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise criteria_decide.ReviewDeclInvalid(f"{path} を読めない: {exc}") from exc
    return criteria_decide.focus_of(raw, str(path))


def _allowed(wt: Path) -> Optional[frozenset[str]]:
    """メインディレクトリの `.ndf/runtimes.json` の `allowed`（#3941）。worktree でなければ（テストの一時の場所など）読まない。"""
    try:
        main = location.main_dir(git.Git(wt))
    except (git.GitError, OSError):  # noqa: NDF401 ファイル: git の作業場所でなければ設定を読む場所が無い（run は後で worktree の確かめで止まる）
        return None
    return participants.declared_allowed(main)


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
        reviewers=participants.assign(args.reviewer if args.reviewer else (), allowed=_allowed(wt)),  # noqa: NDF501 利用者の入力: --reviewer の無い呼び出しは既定の担当に任せる
        max_rounds=args.max_rounds,
        handed=_handed(args.findings),
        focus=_focus(wt),
    )


class ReportFailed(Exception):
    """記録のコメントを書けなかった。"""


def _post_report(
    state_dir: Path,
    pr: int,
    final: Optional[str],
    warnings: Any,
    repo_arg: Optional[str],
    cwd: Path,
    hub_of: Optional[Callable[[], Hub]] = None,
) -> tuple[str, list[str]]:
    """指摘をインラインのスレッドにして閉じた指摘を resolve し、記録を今の PR 番号へコメントする（#3939）。

    返すのはコメントの URL とスレッドの注意。コメントを書けなければ `ReportFailed`。済んだスレッドとコメントの URL は状態ファイルへ書く。
    `hub_of` を渡せばその口を使う（`run` が読むのに作った口を使い回し、認証を取り直さない）。
    """
    ref = store.state_pr(state_dir, pr)
    if not ref.path.is_file():
        raise ReportFailed(f"PR #{pr} の状態ファイルが {state_dir} に無い")
    data = store.read_state(ref)
    try:
        hub = hub_of() if hub_of is not None else HUB(repo_arg, store.state_repo(ref), cwd)
        notes = threads.sync(data, hub.threads(ref.current_pr))
        store.write_object(ref.path, data)
        body = report_decide.comment(ref.current_pr, data, final, (*warnings, *notes))
        previous = data.get(report_decide.COMMENT_KEY)
        url = hub.comment(ref.current_pr, body, previous if isinstance(previous, str) else None)
        # 1 つの PR の記録は 1 つのコメントに保つ（打ち直し・次の起動は同じコメントを書き換える）
        data[report_decide.COMMENT_KEY] = url
        store.write_object(ref.path, data)
        return url, notes
    except Exception as exc:
        raise ReportFailed(f"PR #{ref.current_pr} へ cross-review の記録を書けなかった: {type(exc).__name__}: {exc}") from exc


def cmd_report(args: argparse.Namespace) -> int:
    """状態ファイルの記録（ラウンド・修正・指摘）を PR へコメントする。消える前の状態ファイルから書き足すのに使う。"""
    state_dir = Path(args.state_dir)
    data = store.read_state(store.state_pr(state_dir, args.pr))
    try:
        url, notes = _post_report(state_dir, args.pr, report_decide.final_of(data), (), args.repo, Path.cwd())
    except ReportFailed as exc:
        return _stop("precondition", str(exc))
    return _ok(f"PR #{args.pr} へ cross-review の記録を書いた: {url}", [{"kind": "comment", "number": args.pr, "url": url}], warnings=notes)


def cmd_run(args: argparse.Namespace) -> int:
    """収束ループを回して終わり方から結果を出す（`{{review}}` が打つ 1 本）。

    `--reviewed-out` を渡せば、最後のラウンドでレビュー担当が読んだ HEAD をそのファイルへ書く（#3879）。最後のラウンドの
    修正のコミットはレビューを経ていないため、作業場所の HEAD でなくこの値を「レビューした HEAD」にする。始めに消すので、
    書けなかった起動の後に前の起動の値が残らない。
    """
    out = Path(args.reviewed_out) if args.reviewed_out else None
    if out is not None:
        out.unlink(missing_ok=True)
    try:
        cfg = _run_config(args)
    except ValueError as exc:
        # 相手役の名指しの誤り・ランタイムの設定の外の名前（participants.UnknownParticipant）、足すべき内容の形の誤り、
        # レビューの設定の形の誤り（criteria_decide.ReviewDeclInvalid）
        return _stop("invalid", str(exc))
    state_repo = store.state_repo(store.state_pr(cfg.state_dir, cfg.pr))
    made: list[Hub] = []

    def hub_once() -> Hub:
        # 読む口と書く口で同じクライアントを使う（`gh auth token` の起動などの認証を 1 回の run で 1 回にする）
        if not made:
            made.append(HUB(None, state_repo, cfg.worktree))
        return made[0]

    try:
        got = drive.drive(cfg, LAUNCH, lambda pr: hub_once().reader(pr))
    except worktree.WorktreeUnregistered as exc:
        return _unregistered(exc, str(exc.worktree))
    except worktree.SyncRefused as exc:
        return _stop("precondition", str(exc))
    except drive.DriveStopped as exc:
        return _stop(exc.kind, str(exc))
    verdict = fin.finish(got.review, cfg.handed)
    if out is not None and got.reviewed_head is not None:
        out.write_text(got.reviewed_head + "\n", encoding="utf-8")
    items: list[dict[str, Any]] = [{"kind": "finding", "id": f.id, "outcome": f.outcome} for f in got.review.findings]
    warnings = list(got.warnings)
    try:
        # 状態ファイルは消える作業場所にあるため、記録を PR へ残す。書けなくても終わり方は変えず、注意に残す（#3939）
        url, notes = _post_report(cfg.state_dir, cfg.pr, got.review.final, got.warnings, None, cfg.worktree, hub_once)
        items.append({"kind": "comment", "number": cfg.pr, "url": url})
        warnings += notes
    except ReportFailed as exc:
        warnings.append(f"{exc}（ndf cross-review report --state-dir {cfg.state_dir} --pr {cfg.pr} で書き足す）")
    metrics = {"rounds": got.review.rounds, "findings": len(got.review.findings)}
    return _emit_verdict(verdict, items=items, metrics=metrics, warnings=warnings, process=list(got.process))


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
    _with_state(add("report")).add_argument("--repo")
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
    r.add_argument("--reviewed-out")


def _with_branch(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """worktree と PR のブランチを受けるサブコマンドの共通の引数を足す。"""
    parser.add_argument("worktree")
    parser.add_argument("--branch", required=True)
    return parser


def _with_state(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """状態ファイルを読むサブコマンドの共通の引数（--state-dir・--pr）を足す。"""
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--pr", type=int, required=True)
    return parser


def _state_parsers(add: Callable[..., argparse.ArgumentParser]) -> None:
    _with_state(add("state-file")).add_argument("--current", type=int)
    # --findings は run へ渡した足すべき内容。merge-fix がその id の決定もジャーナルの行にする
    _with_state(add("fix-gate")).add_argument("--findings")
    merge = _with_state(add("merge-fix"))
    merge.add_argument("--file", required=True)
    merge.add_argument("--findings")
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
    "report": cmd_report,
}


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    try:
        return HANDLERS[args.command](args)
    except store.StateBroken as exc:
        return _stop("invalid", str(exc))
    except git.GitError as exc:
        return _stop("precondition", f"git の操作が失敗した: {exc}")
