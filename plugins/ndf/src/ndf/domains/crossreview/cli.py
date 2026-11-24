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
    ndf cross-review review-scope <worktree> --state-dir D --pr N --purpose P --base B [--reviewer R ...]   # レビューの範囲（#3181）
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
import contextlib
import importlib
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Callable, ContextManager, NamedTuple, Optional

from ndf import cli
from ndf.adapters import agents, git, run_log
from ndf.contracts import limits, result
from ndf.core import route
from ndf.core.converge import location, participants, worktree
from ndf.domains.crossreview import changes, drive, fixer, fixgate_decide, journal, push, repo, report_decide, scope_decide, store, threads
from ndf.domains.crossreview import finish_decide as fin
from ndf.domains.crossreview.settings import (
    MAX_ROUNDS,
    DriveStopped,
    ReviewSettings,
    RunConfig,
    outside_checkout,
    read_settings,
)
from ndf.domains.records import mvv

TOOL = cli.tool_of(__name__)
# 作業の名前（lock の理由に載せる）
CROSS_REVIEW = TOOL
# PR の head を読むアダプター（依存の欠けを import の時点で見分ける。#2447）
GITHUB_MODULE = "ndf.adapters.github"
# 相手役と修正担当の起動（テストは偽物に差し替える）
LAUNCH: fixer.Launcher = agents.run_agent


def _redacted(texts: Sequence[str]) -> list[str]:
    """書き先の公開範囲を知らないときの伏せ字（秘密だけ。`run_log.redact`）。"""
    return [str(run_log.redact(t)) for t in texts]


class Hub(NamedTuple):
    """PR へ書く口と読む口。`comment(PR, 本文, 前に書いたコメントの URL)` は前のコメントがあれば書き換え、無ければ足して URL を返す。
    `reader` は PR のコメントとスレッドを読む口（#3941）。`threads` と `reader` は同じ PR に同じ口を返す（読んだスレッドの
    一覧を書く側で使い回す。PR 3944 の r6-8）。`clean` は書き先に応じた伏せ字（値の並びを 1 回で受ける。記録のコメントの
    末尾の一覧の値を JSON にする前に通す。PR 3944 の r6-4）。`publishing` は同じ PR の公開のロック（`write_gate.pr_lock`。
    同じ PR へ並んだ公開が同じ指摘を二重に書かない。#4016 の不変条件 I4）。"""

    comment: Callable[[int, str, Optional[str]], str]
    threads: Callable[[int], threads.Port]
    reader: Callable[[int], threads.Reader]
    clean: Callable[[Sequence[str]], Sequence[str]] = _redacted
    publishing: Callable[[int], ContextManager[Any]] = lambda pr: contextlib.nullcontext()


def _github_hub(repo_arg: Optional[str], state_repo: Optional[str], cwd: Path) -> Hub:
    """対象のリポジトリ（`repo.resolve_target_repo` の順で決める）の GitHub へ書く口。書く本文は `publish.Publisher` を通す。"""
    slug = repo.resolve_target_repo(repo_arg, state_repo, cwd).slug
    gh = load_github()
    from ndf.adapters import publish, write_gate

    # 書き込みの順番と二次レート制限の待ちの終わりを、マシンの 1 つの記録で共有する（#4016）
    gate = write_gate.machine_gate()
    hub = gh.GitHub(slug, gh.client(gate=gate), gate=gate)
    publisher = publish.Publisher(hub)
    ports: dict[int, threads.GitHubPort] = {}

    def comment(pr: int, body: str, previous: Optional[str]) -> str:
        cid = report_decide.comment_id(previous)
        if cid is None:
            return str(threads.patient(lambda: publisher.comment(pr, body), gh.GitHubError, block=gate.block).url)
        (safe,), _ = publisher.guarded(body)
        path = f"/repos/{slug}/issues/comments/{cid}"
        patched = threads.patient(lambda: hub.rest("PATCH", path, {"body": safe}), gh.GitHubError, block=gate.block)
        return str(patched["html_url"])

    def port(pr: int) -> threads.GitHubPort:
        # PR ごとに 1 つの口を使い回す（読んだスレッドの一覧を resolve で取り直さない。PR 3944 の r6-8）
        if pr not in ports:
            ports[pr] = threads.GitHubPort(hub, publisher, pr, gh.GitHubError, block=gate.block)
        return ports[pr]

    def clean(texts: Sequence[str]) -> list[str]:
        return [str(t) for t in publisher.guarded(*texts)[0]] if texts else []

    def publishing(pr: int) -> ContextManager[Any]:
        return write_gate.pr_lock(slug, pr)

    return Hub(comment, port, port, clean, publishing)


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
        if args.current != ref.current_pr and ref.path.is_file():
            # 番号を変える前に、オーナーの無い旧形式の記録を巻き直しの前の PR に結び付ける（新しい PR のものにしない。PR 3944 の r5-2）
            data = store.read_state(ref)
            if report_decide.bind_pr(data, ref.current_pr, ref.origin_pr):
                store.write_object(ref.path, data)
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
    """`run` と同じマージ（ジャーナルへ反映し、結果ファイルを移す）を `fixer.merge_fix` で行う。"""
    try:
        round_no = fixer.merge_fix(Path(args.state_dir), args.pr, Path(args.file), _handed(args.findings))
    except (store.FixResultMismatch, ValueError) as exc:
        return _stop("invalid", str(exc))
    ref = store.state_pr(Path(args.state_dir), args.pr)
    return _ok(f"修正の結果を round {round_no} へマージした: {args.file}", [_ref_item(ref)])


# --- 範囲と終わり方 ---------------------------------------------------------------------


def _every_section(purpose: str) -> bool:
    """セクションを詳細レビューへ必ず通す PR か（設計のプランのテンプレートの purpose。#3181）。"""
    return purpose == route.templates()["design"].purpose


class _LocalBase(NamedTuple):
    """取得済みの参照だけから決めた、差分の比較元のコミットと、MVV を読む参照（決められなければ None）。"""

    commit: str
    mvv_ref: Optional[str]


def _local_base(g: git.Git, remote: str, base: str, branch: Optional[str]) -> _LocalBase:
    """`run` の起動の始めと同じ決め方で、リモートから取得せずに比較元と MVV を読む参照を決める。

    `--base` がブランチなら、取得済みのリモートのブランチの先頭が両方になる。コミット（SHA・タグなど）なら、MVV は PR の
    マージ先のブランチから読むので、`run` がラウンドの記録に残した `branch` を使う。記録が無ければ MVV を読む参照は決められない。
    """
    tracking = f"refs/remotes/{remote}"
    commit = base if git.FULL_SHA.match(base) else None
    if commit is None:
        try:
            tip = g.rev_parse(f"{tracking}/{base.removeprefix(f'{remote}/')}")
            return _LocalBase(tip, tip)
        except git.GitError:
            commit = g.commit_named(base)
    if commit is None:
        # リモートに無い手元だけのブランチ。`run` は止まるので、MVV を読む先も無い
        return _LocalBase(base, None)
    return _LocalBase(commit, f"{tracking}/{branch}" if branch is not None else None)


def _local_mvv(wt: Path, ref: Optional[str]) -> tuple[str, list[str]]:
    """参照 `ref` の MVV を見分ける値と注意。確かめられなければ `scope_decide.UNKNOWN_MVV`（全差分になる）。"""
    if ref is None:
        why = "MVV を読むブランチを決められない（`--base` は取得済みのリモートのブランチか、`run` に渡したコミットにする）"
    else:
        try:
            return mvv.identity_at(wt, ref), []
        except mvv.MvvError as exc:
            why = str(exc)
    return scope_decide.UNKNOWN_MVV, [f"前のラウンドと MVV が同じかを確かめられないので、全差分を範囲にした: {why}"]


def _local_sections(g: git.Git, base: str, wt: Path) -> tuple[list[str], Optional[str]]:
    """変わった Markdown のセクション。手元のオブジェクトだけで読めなければ、読めない理由（全差分になる）。"""
    try:
        return changes.changed_sections(g, base, wt), None
    except git.GitError as exc:
        return [], f"変わった文書のセクションを手元のオブジェクトだけで読めないので、全差分を範囲にした: {exc}"


def cmd_scope(args: argparse.Namespace) -> int:
    ref = store.state_pr(Path(args.state_dir), args.pr)
    data = store.read_state(ref)
    rounds = store.list_of(data, store.ROUNDS)
    wt = Path(args.worktree)
    try:
        # `run` と同じ担当の指定と既定・重点で決める（担当を足した打ち直しで `run` と違う範囲を返さない。重点を変えた打ち直しは
        # 全差分。PR 3944 の r3-4・r4-3）
        resolved = _resolve(wt, args.reviewer)
    except ValueError as exc:
        return _stop("invalid", str(exc))
    except (participants.FixerNotAllowed, PolicyUnresolved) as exc:
        return _stop("precondition", str(exc))
    except worktree.WorktreeUnregistered as exc:
        return _unregistered(exc, str(exc.worktree))
    g = git.Git.offline(wt)
    # `_resolve` が求めたメインディレクトリから読む（`g.remote()` は `git worktree list` を打ち直す）
    based = _local_base(g, git.remote_of(resolved.policy.main), args.base, scope_decide.mvv_branch(data))
    # ラウンドの記録の比較基点は merge-base の SHA（PR 3944 の r2-1）。`--base` の誤りは、セクションを読む前にここで止まる
    merge_base = g.merge_base(based.commit, "HEAD")
    sections, unread = _local_sections(g, based.commit, wt)
    if unread is not None:
        # 全差分に決まっているので、範囲を変えない MVV を読まない
        got, warnings = scope_decide.Scope(scope_decide.FULL, None, ()), [unread]
    else:
        now, unsure = _local_mvv(wt, based.mvv_ref)
        warnings = unsure if rounds else []
        basis = scope_decide.Basis(merge_base, resolved.reviewers, resolved.settings.focus, now)
        got = drive.descended(g, scope_decide.state_scope(data, _every_section(args.purpose), len(rounds) + 1, sections, basis), "HEAD")
    item = {"kind": "scope", "scope": got.kind, "base": got.base, "unreviewed": list(got.unreviewed)}
    summary = f"round {len(rounds) + 1} の範囲は {got.kind}（詳細レビューを通らないセクション {len(got.unreviewed)} 件）"
    return _ok(summary, [item], **({"warnings": warnings} if warnings else {}))


def _handed(path: Optional[str]) -> tuple[fin.Handed, ...]:
    if path is None:
        return ()
    return fin.as_handed(store.list_of(store.read_object(Path(path)), "items"))


def _review(path: str) -> fin.Review:
    return fin.as_review(store.read_object(Path(path)))


def _emit_verdict(verdict: fin.Verdict, mvv: Optional[dict[str, Any]] = None, **extra: Any) -> int:
    """終わり方の結果を出す。outputs の gaps は欠けを後のステップ（CI）の後まで引き継ぐ（空なら前の欠けを消す）。結果 JSON の最上位の
    gaps は incomplete だけが持てるので、ほかの理由で止まったときの欠けは outputs にだけ載せる。指摘の本文や理由を含む値は、結果を
    集めるログへ秘密を写さないよう、ここで伏せる。`mvv` は outputs の mvv（自由記述を伏せ終えたもの。承認済みの MVV が無ければ None）。"""
    warnings = [*extra.pop("warnings", ()), *verdict.warnings]
    summary, decisions, gaps, warnings = run_log.redact([verdict.summary, verdict.decisions, verdict.gaps, warnings])
    shared = {"decisions": decisions, "warnings": warnings, "outputs": {"gaps": gaps, **({"mvv": mvv} if mvv is not None else {})}, **extra}
    if verdict.status == fin.OK:
        return _ok(summary, **shared)
    return _stop(str(verdict.kind), summary, gaps=gaps if verdict.kind == "incomplete" else None, **shared)


def cmd_finish(args: argparse.Namespace) -> int:
    try:
        verdict = fin.finish(_review(args.summary), _handed(args.findings))
    except ValueError as exc:
        return _stop("invalid", str(exc))
    return _emit_verdict(verdict)


def _branch_of(wt: Path) -> str:
    return git.Git(wt).run("rev-parse", "--abbrev-ref", "HEAD").strip()


class PolicyUnresolved(Exception):
    """ランタイムの設定（認可）の保存先を決められない（前提の欠け。どの担当も起動する前に precondition で止める）。"""


class Policy(NamedTuple):
    """実行者のメインディレクトリと、その `.ndf/runtimes.json` の `allowed`（設定が無ければ None。制限しない）。"""

    main: Path
    allowed: Optional[frozenset[str]]


def _policy(wt: Path) -> Policy:
    """メインディレクトリと `allowed`（#3941）。メインディレクトリは 1 回の起動で 1 回だけ求め、レビューの設定もここから読む
    （PR 3944 の r4-4）。管理情報の無い worktree は `worktree.WorktreeUnregistered`。保存先（メインディレクトリ）を決められなければ、
    設定の無いときと分けて `PolicyUnresolved`（認可を外したまま担当を起動しない。PR 3944 の r2-7）。`wt` がメインディレクトリ
    そのものでも `PolicyUnresolved`（同期が設定を書き換える。PR 3944 の r5-1）。"""
    worktree.registration(wt)
    try:
        # Git が報告する主 worktree から決める（共通ディレクトリの親とみなさない。PR 3944 の r6-2）
        main = location.main_worktree(git.Git(wt))
        same = main.resolve() == wt.resolve()
    except (git.GitError, OSError) as exc:
        raise PolicyUnresolved(f"{wt} のメインディレクトリを決められず、ランタイムの設定（allowed）を読めない: {exc}") from exc
    if same:
        # 同期が PR のコミットの `.ndf/review.json`・`.ndf/runtimes.json` をメインディレクトリへ写すため、打ち直すと PR の
        # 作成者の設定を実行者の設定として読む。ほかに信頼できる設定元が無いので同期の前に止める（PR 3944 の r5-1）
        raise PolicyUnresolved(
            f"{wt} はリポジトリのメインディレクトリそのもので、同期が PR の設定で書き換えるため実行者の設定（allowed・重点）を"
            "読む場所にできない。メインディレクトリから PR の head ブランチの worktree を作り、--worktree に渡す"
        )
    return Policy(main, participants.declared_allowed(main))


def _reviewers(requested: Optional[list[str]], policy: Policy) -> tuple[str, ...]:
    """今回の相手役（`--reviewer` の名指しと、メインディレクトリの `allowed` の中の既定）。`run` と `review-scope` が同じに決める。"""
    return participants.assign(requested if requested else (), allowed=policy.allowed)  # noqa: NDF501 利用者の入力: --reviewer の無い呼び出しは既定の担当に任せる


class Resolved(NamedTuple):
    """実行者の設定から決まる値（`run` と `review-scope` が同じに決める。PR 3944 の r3-4・r4-3）。"""

    policy: Policy
    reviewers: tuple[str, ...]
    settings: ReviewSettings


def _resolve(wt: Path, requested: Optional[list[str]]) -> Resolved:
    """メインディレクトリと `allowed`、今回の相手役、レビューの設定を求める。"""
    policy = _policy(wt)
    reviewers = _reviewers(requested, policy)
    # 同期の前に読む（`--worktree` がメインディレクトリそのものでも、PR のコミットの設定を実行者の設定にしない。PR 3944 の r4-1）
    return Resolved(policy, reviewers, read_settings(policy.main, wt))


def _run_config(args: argparse.Namespace) -> RunConfig:
    # 子へ渡すパスの基準を打った場所の cwd にそろえる（相対の --state-dir と別の --worktree で基準が割れない）
    wt = (Path(args.worktree) if args.worktree else Path.cwd()).absolute()
    got = _resolve(wt, args.reviewer)
    return RunConfig(
        worktree=wt,
        branch=args.branch or _branch_of(wt),
        base=args.base,
        pr=args.pr,
        state_dir=Path(args.state_dir).absolute(),
        every_section=_every_section(args.purpose),
        reviewers=got.reviewers,
        max_rounds=args.max_rounds,
        handed=_handed(args.findings),
        main=got.policy.main,
        settings=got.settings,
    )


class ReportFailed(Exception):
    """記録のコメントを書けなかった。"""


def _post_report(state_dir: Path, pr: int, final: Optional[str], warnings: Any, hub_of: Callable[[], Hub]) -> tuple[str, list[str]]:
    """指摘をインラインのスレッドにして閉じた指摘を resolve し、記録を今の PR 番号へコメントする（#3939）。

    返すのはコメントの URL とスレッドの注意。コメントを書けなければ `ReportFailed`。済んだスレッドとコメントの URL は状態ファイルへ書く。
    `hub_of` は PR へ書く口を返す（`run` は読むのに作った口を使い回し、認証を取り直さない）。
    """
    ref = store.state_pr(state_dir, pr)
    if not ref.path.is_file():
        raise ReportFailed(f"PR #{pr} の状態ファイルが {state_dir} に無い")
    try:
        hub = hub_of()
        # 同じ PR の公開のロックの中で状態ファイルを読み直す（後から取った公開は先の記録を見て二重に書かない。#4016）
        with hub.publishing(ref.current_pr):
            return _publish(ref, final, warnings, hub)
    except Exception as exc:
        raise ReportFailed(f"PR #{ref.current_pr} へ cross-review の記録を書けなかった: {type(exc).__name__}: {exc}") from exc


def _publish(ref: store.StateRef, final: Optional[str], warnings: Any, hub: Hub) -> tuple[str, list[str]]:
    """同じ PR の公開のロックの中で、状態ファイルの読み取りから記録のコメントと状態の保存までを行う。"""
    data = store.read_state(ref)
    # 巻き直しの後は旧 PR のコメントとスレッドを使わず、今の PR へ書き直す（PR 3944 の r4-5）
    report_decide.bind_pr(data, ref.current_pr, ref.origin_pr)
    if journal.with_occurrences(data):
        # スレッドを作る前に、本文に入れる発生の値を残す（作った直後に落ちた打ち直しが同じ値で PR から戻す）
        store.write_object(ref.path, data)
    notes = threads.sync(data, hub.threads(ref.current_pr))
    store.write_object(ref.path, data)
    body = report_decide.comment(ref.current_pr, data, final, (*warnings, *notes), hub.clean)
    previous = data.get(report_decide.COMMENT_KEY)
    url = hub.comment(ref.current_pr, body, previous if isinstance(previous, str) else None)
    # 1 つの PR の記録は 1 つのコメントに保つ（打ち直し・次の起動は同じコメントを書き換える）
    data[report_decide.COMMENT_KEY] = url
    # 公開できた状態を残す。スレッドを書き切れなかったときは残さず、打ち直しで書き直す（PR 3944 の r3-2）
    if notes:
        data.pop(report_decide.PUBLISHED_KEY, None)
    else:
        data[report_decide.PUBLISHED_KEY] = report_decide.published_state(ref.current_pr, data, final)
    store.write_object(ref.path, data)
    return url, notes


def cmd_report(args: argparse.Namespace) -> int:
    """状態ファイルの記録（ラウンド・修正・指摘）を PR へコメントする。消える前の状態ファイルから書き足すのに使う。"""
    state_dir = Path(args.state_dir)
    ref = store.state_pr(state_dir, args.pr)
    data = store.read_state(ref)
    try:
        url, notes = _post_report(
            state_dir, args.pr, report_decide.final_of(data), (), lambda: HUB(args.repo, store.state_repo(ref), Path.cwd())
        )
    except ReportFailed as exc:
        return _stop("precondition", str(exc))
    return _ok(f"PR #{args.pr} へ cross-review の記録を書いた: {url}", [{"kind": "comment", "number": args.pr, "url": url}], warnings=notes)


def _ready(args: argparse.Namespace) -> tuple[RunConfig, Optional[str]] | int:
    """起動の前の準備（設定・保存先の検査・状態の対象のリポジトリ）。止めるなら結果を出した終了コードを返す。"""
    try:
        cfg = _run_config(args)
    except ValueError as exc:
        # 相手役の名指しの誤り・ランタイムの設定の外の名前（participants.UnknownParticipant）、足すべき内容の形の誤り、
        # レビューの設定の形の誤り（criteria_decide.ReviewDeclInvalid）
        return _stop("invalid", str(exc))
    except (participants.FixerNotAllowed, PolicyUnresolved) as exc:
        # 修正担当を起動してよいと設定されていない・認可の設定の保存先を決められない（前提の欠け）。どの担当も起動する前に
        # 止める（PR 3944 の r1-4・r2-7）
        return _stop("precondition", str(exc))
    except worktree.WorktreeUnregistered as exc:
        return _unregistered(exc, str(exc.worktree))
    try:
        # 保存先の検査は状態を最初に読む前に（worktree の中の状態ファイルやリンク先を開かない。PR 3944 の r8-2）
        cfg = outside_checkout(cfg)
    except DriveStopped as exc:
        return _stop(exc.kind, str(exc))
    try:
        state_repo = store.state_repo(store.state_pr(cfg.state_dir, cfg.pr))
    except OSError as exc:
        # 保存先を最初に参照する箇所。親に入れない保存先などでも、トレースバックでなく前提の欠けとして返す（PR 3944 の r14-1）
        return _stop("precondition", f"状態の保存先 {cfg.state_dir} を参照できない: {exc}")
    return cfg, state_repo


def cmd_run(args: argparse.Namespace) -> int:
    """収束ループを回して終わり方から結果を出す（`{{review}}` が打つ 1 本）。

    `--reviewed-out` を渡せば、今回の起動で相手役の全員が読めた最後のラウンドの HEAD をそのファイルへ書く（#3879。一部が
    読めなかったラウンドの HEAD は書かない。PR 3944 の r2-3）。最後のラウンドの
    修正のコミットはレビューを経ていないため、作業場所の HEAD でなくこの値を「レビューした HEAD」にする。始めに消すので、
    書けなかった起動の後に前の起動の値が残らない。
    """
    out = Path(args.reviewed_out) if args.reviewed_out else None
    if out is not None:
        out.unlink(missing_ok=True)
    ready = _ready(args)
    if isinstance(ready, int):
        return ready
    cfg, state_repo = ready
    made: list[Hub] = []

    def hub_once() -> Hub:
        # 読む口と書く口で同じクライアントを使う（`gh auth token` の起動などの認証を 1 回の run で 1 回にする）
        if not made:
            made.append(HUB(None, state_repo, cfg.worktree))
        return made[0]

    try:
        got = drive.drive(cfg, LAUNCH, lambda pr: hub_once().reader(pr), lambda texts: hub_once().clean(texts))
    except worktree.WorktreeUnregistered as exc:
        return _unregistered(exc, str(exc.worktree))
    except worktree.SyncRefused as exc:
        return _stop("precondition", str(exc))
    except DriveStopped as exc:
        return _stop(exc.kind, str(exc))
    verdict = fin.finish(got.review, cfg.handed)
    if out is not None and got.reviewed_head is not None:
        out.write_text(got.reviewed_head + "\n", encoding="utf-8")
    items, warnings = _report(cfg, got, hub_once)
    metrics = {"rounds": got.review.rounds, "findings": len(got.review.findings)}
    return _emit_verdict(verdict, got.mvv, items=items, metrics=metrics, warnings=warnings, process=list(got.process))


def _report(cfg: RunConfig, got: drive.Outcome, hub_of: Callable[[], Hub]) -> tuple[list[dict[str, Any]], list[str]]:
    """結果の items（指摘と記録のコメント）と注意。記録を PR へ書く。前回の収束を返した起動は、今の PR へ今と同じ状態の
    記録を公開できていれば書き直さない（PR 3944 の r2-10・r3-2。URL があるだけでは、最新のラウンドの公開に失敗した後の
    古い記録かもしれない）。"""
    items: list[dict[str, Any]] = [{"kind": "finding", "id": f.id, "outcome": f.outcome} for f in got.review.findings]
    warnings = list(got.warnings)
    ref = store.state_pr(cfg.state_dir, cfg.pr)
    data = store.read_state(ref)
    report_decide.bind_pr(data, ref.current_pr, ref.origin_pr)
    previous = data.get(report_decide.COMMENT_KEY)
    published = data.get(report_decide.PUBLISHED_KEY) == report_decide.published_state(ref.current_pr, data, got.review.final)
    if got.reused and isinstance(previous, str) and published:
        items.append({"kind": "comment", "number": cfg.pr, "url": previous})
        return items, warnings
    try:
        # 状態ファイルは消える作業場所にあるため、記録を PR へ残す。書けなくても終わり方は変えず、注意に残す（#3939）
        url, notes = _post_report(cfg.state_dir, cfg.pr, got.review.final, got.warnings, hub_of)
        items.append({"kind": "comment", "number": cfg.pr, "url": url})
        warnings += notes
    except ReportFailed as exc:
        warnings.append(f"{exc}（ndf cross-review report --state-dir {cfg.state_dir} --pr {cfg.pr} で書き足す）")
    return items, warnings


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
    r.add_argument("--max-rounds", type=int, default=int(limits.value(MAX_ROUNDS, "count")))
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
    scope.add_argument("--reviewer", action="append")
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
