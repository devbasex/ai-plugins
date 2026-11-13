"""CD のある場合（`lib/release_case.py` の cd-no-review / cd-review）のリリースの経路（#2773。`release-steps.py` の release・
approval-check・approval-post・release-case から分けた）。

- 本番: 開発版の PR → 本番の PR → CI の待ち → PR の上の承認（証跡かレビュー）と先端の照合 → 管理者の迂回なしのマージ → 公開の後
- 開発版: 宣言 `release.dev_workflow` の workflow に開発版の PR をマージさせ、結果を待つ
- 証跡のチェック: 証跡を判定して PR の先端へ commit status を付ける（同じ先端の PR が並べば落とす。I10）

本番の `ops` は `ProdOps`、開発版の `ops` は `release-steps.py`（find_pr・create_pr・merge_commit_of・_reusable_release_pr・
_release_pr_body）。出す（emit）のはここで、終了コードは `step_result` の値を使う。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import approved_commit
import ci_workflows
import delivery
import gh_parts
import gh_rest
import limits
import release_case
import repo as repo_lib
import waits
from release_lib import approval, approval_trail
from release_lib.approval import HeadMoved
from release_lib.context import ReleaseCtx
from release_lib.names import release_tag
from step_result import EXIT_GATE, EXIT_INFRA_WAIT, EXIT_PRECONDITION, EXIT_UNREADABLE, StepError, emit, gh_json, git, git_root, result

TOOL = approval.TOOL


release_case_or_stop = release_case.case_or_stop  # release-steps.py と merged_lib が同じ止め方を使う


def dev_workflow_of(root) -> str | None:
    """開発版の workflow のファイル名（`.ndf/supervise.json` の release.dev_workflow）。無ければ None（手元でマージする）。"""
    got = (delivery.load_delivery(root).release or {}).get("dev_workflow")
    return got if isinstance(got, str) and got else None


def dev_workflow_ready(root, workflow) -> bool:
    """宣言の開発版の workflow が既定ブランチにあるか。`workflow_dispatch` は既定ブランチの定義とコードで動くため、
    宣言を入れた変更が既定ブランチへ届くまでは起動できない（その間は手元でマージする）。workflow と、それが打つ
    `merge-when-green --no-admin` は同じ変更で入るので、workflow があればコードも揃っている。読めなければ 1 で止める。"""
    repo = repo_or_stop(root)
    p = gh_parts.gh(["api", f"repos/{repo}/contents/{ci_workflows.WORKFLOW_DIR}/{workflow}", "--silent"], cwd=root)
    if p.returncode == 0:
        return True
    if "404" in (p.stderr or ""):
        return False
    raise StepError(f"{workflow} が既定ブランチにあるかを読めない: {(p.stderr or '').strip()[:300]}")


def repo_or_stop(root):
    repo = os.environ.get("GITHUB_REPOSITORY") or repo_lib.owner_repo(root)
    if not repo:
        raise StepError("リポジトリ（origin の owner/repo）を決められない", EXIT_UNREADABLE)
    return repo


@dataclass(frozen=True)
class ProdOps:
    """CD のある場合の本番（`release_prod`）が呼ぶ GitHub とリリースの操作。`release-steps.py` が組んで渡す。"""

    find_pr: Callable  # (root, head, base, states) -> {"number", "state"} | None
    create_pr: Callable  # (root, base, head, title, body) -> int
    merge_commit_of: Callable  # (root, n) -> str | None
    merge_release_pr: Callable  # (root, a, plugin, plugins, branch, base) -> (release_pr, merged_pr, commit)
    published: Callable  # (root, a, plugin, prod, main_pr, merge, items, metrics) 公開の後。結果を出して終わる
    wait_and_merge: Callable  # (root, n, *, expect, ci_wait, gate_approved, no_admin) -> マージコミット
    pr_check_buckets: Callable  # (root, n) -> チェックの並び


def release_prod(ctx: ReleaseCtx, case, ops: ProdOps):
    """場合 1・2 の本番: 開発版の PR → 本番の PR → CI の待ち → 承認の照合 → 迂回なしのマージ → 公開の後（#2773）。
    承認が先端と一致しなければ本番の PR を開いたまま承認ゲート（10）で止める。"""
    root, a, plugin, plugins, branch, base, prod = ctx.root, ctx.a, ctx.plugin, ctx.plugins, ctx.branch, ctx.base, ctx.prod
    ver = a.version
    metrics = {
        "channel": "prod",
        "version": ver,
        "case": case.case,
        "release_pr": None,
        "sync_pr": None,
        "main_pr": None,
        "tag": None,
        "merge_commit": None,
        "plugins": ",".join(plugins),
    }
    items: list[dict] = []
    main_pr = _open_prod_pr(root, a, plugin, plugins, branch, base, prod, items, metrics, ops)
    repo = repo_or_stop(root)

    def waiting(head, judged):
        how = (
            f"release-steps.py approval-post --pr {main_pr} --approval <承認資料> で承認資料を置いて承認を求め、承認を得たら "
            f"release-steps.py approve --pr {main_pr} --approved-sha {head} --by user で証跡を書いて"
            if case.case == release_case.CD_NO_REVIEW
            else f"PR #{main_pr} の先端 {head[:8]} のレビューの承認を得て"
        )
        nxt = f"{how}、release-steps.py release --version {ver} --channel prod を打ち直す"
        return f"{plugin} v{ver} のリリース承認を待つ（本番の PR #{main_pr}・先端 {head[:8]}）: {judged.description}", nxt

    def moved(head):
        nxt = f"承認の後に先端が動いた。新しい先端で承認を取り直し、release-steps.py release --version {ver} --channel prod を打ち直す"
        return f"{plugin} v{ver} のリリースを止めた: 本番の PR #{main_pr} の先端が承認した {head[:8]} から動いた", nxt

    awaiting = [{"kind": "pr", "name": f"#{main_pr}", "result": "awaiting_approval", "base": prod}]
    merge = merge_on_pr_approval(
        root, repo, a, case, main_pr, prod, items, metrics, ops, waiting=waiting, moved=moved, waiting_items=awaiting
    )
    items.append({"kind": "pr", "name": f"#{main_pr}", "result": "merged", "base": prod, "admin": False})
    git(root, "fetch", "-q", "origin")
    ops.published(root, a, plugin, prod, main_pr, merge or git(root, "rev-parse", f"origin/{prod}").stdout.strip(), items, metrics)


def _open_prod_pr(root, a, plugin, plugins, branch, base, prod, items, metrics, ops: ProdOps) -> int:
    """本番の PR を引くか作り、その番号を返す。マージ済みなら公開の後へ進む（再開）。無ければ開発版の PR をマージしてから作る。"""
    ver = a.version
    git(root, "fetch", "-q", "origin", "--tags")
    done = ops.find_pr(root, branch, prod, states=("MERGED", "OPEN"))
    if done and done["state"] == "MERGED":  # 再開: 本番チャネルへのマージは済んでいる
        git(root, "fetch", "-q", "origin")
        merge = ops.merge_commit_of(root, done["number"]) or git(root, "rev-parse", f"origin/{prod}").stdout.strip()
        items.append({"kind": "pr", "name": f"#{done['number']}", "result": "already_merged", "base": prod})
        ops.published(root, a, plugin, prod, done["number"], merge, items, metrics)
    if done:
        return done["number"]
    if git(root, "rev-parse", "-q", "--verify", f"refs/tags/{release_tag(plugin, ver)}", check=False).returncode == 0:
        raise StepError(f"タグ {release_tag(plugin, ver)} は既にある")
    release_pr, merged_pr, _ = ops.merge_release_pr(root, a, plugin, plugins, branch, base)
    metrics.update({"release_pr": release_pr, "sync_pr": merged_pr if merged_pr != release_pr else None})
    repo_lib.restore_branch(root, branch, run_git=git)  # 開発版の PR のマージで消えた版上げのブランチを戻す
    title = f"Release: {plugin} v{ver} を {prod} へ"
    body = f"{plugin} v{ver} を {prod} へ出す（開発版の PR #{release_pr}）。\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)"
    return ops.create_pr(root, prod, branch, title, body)


def merge_on_pr_approval(root, repo, a, case, main_pr, prod, items, metrics, ops, *, waiting, moved, waiting_items=()):
    """CD のある場合（#2773 の決定 7）の照合とマージ: 本番の PR の先端が PR の上の承認（証跡かレビュー）と完全に一致するときだけ、
    迂回なしで 1 回だけマージする。承認が無ければ CI を待って承認ゲート（10）で止め、承認の後に先端が動いても同じゲートで止める。
    `waiting(head, judged)` と `moved(head)` は承認ゲートの (要約, 次の手) を返す。`waiting_items` は承認待ちのときだけ items に足す。
    マージコミット（読めなければ None）を返す。"""
    try:
        head = approval_trail.read_head(repo, main_pr)[0]
        judged = approval.approved_on_pr(repo, main_pr, head, case)
    except approval_trail.TrailError as e:
        raise StepError(str(e)) from e
    metrics.update({"main_pr": main_pr, "approved_sha": head, "compared_head": head, "reason": judged.description})
    if judged.state != "success":
        wait_ci_except(root, main_pr, (approval_trail.STATUS_CONTEXT,), a.ci_wait, ops)
        summary, nxt = waiting(head, judged)
        emit(result(TOOL, "gate", summary, items + list(waiting_items), metrics, None, nxt), EXIT_GATE)
    if case.case == release_case.CD_NO_REVIEW:
        wait_trail_status(root, repo, prod, head, limits.policy("release-steps.trail-status").seconds)
    try:
        return ops.wait_and_merge(root, main_pr, expect=head, ci_wait=a.ci_wait, gate_approved="user", no_admin=True)
    except HeadMoved:
        summary, nxt = moved(head)
        emit(result(TOOL, "gate", summary, items, metrics, None, nxt), EXIT_GATE)


def wait_ci_except(root, n, skip, timeout, ops):
    """`skip` の名前を除く PR のチェックがすべて通るまで待つ。落ちたら 1、上限で 75。"""
    policy = limits.policy("release-steps.ci-wait")._replace(seconds=timeout)
    bad = ("fail", "cancel")

    def done(rows):
        rows = [r for r in rows if r.get("name") not in skip]
        return bool(rows) and (all(r.get("bucket") in ("pass", "skipping") for r in rows) or any(r.get("bucket") in bad for r in rows))

    got = waits.wait_until(lambda: ops.pr_check_buckets(root, n), done, policy=policy)
    rows = [r for r in got.value or [] if r.get("name") not in skip]
    if failed := [r.get("name") for r in rows if r.get("bucket") in bad]:
        raise StepError(f"PR #{n} のチェックが落ちた: {', '.join(map(str, failed))}")
    if not got.done:
        raise StepError(f"PR #{n} のチェックが上限 {timeout:g} 秒までに終わらない", EXIT_INFRA_WAIT)


def trail_required(root, repo, prod) -> bool:
    """`ndf/release-approval` が本番チャネルの ruleset の必須チェックにあるか（決定 5 の ruleset の読み取り）。読めなければ
    必須とみなさない（マージの判定は GitHub が必須チェックで持つ）。"""
    try:
        required = ci_workflows.required_contexts(gh_json(root, ["api", f"repos/{repo}/rules/branches/{prod}"], "gh api rules")) or []
    except (StepError, ValueError):
        return False
    return approval_trail.STATUS_CONTEXT in required


def wait_trail_status(root, repo, prod, head, timeout=None):
    """`ndf/release-approval` が本番チャネルの必須チェックにあるか、先端に既に付いているとき、先端のその状態が success に
    なるまで待つ（必須でなくても付いた failure は merge-when-green を止める）。`timeout` を省くと `release-steps.trail-status`
    の上限（`merged_lib.merge.promote` の場合 1 が使う）。"""
    timeout = limits.policy("release-steps.trail-status").seconds if timeout is None else timeout
    required = trail_required(root, repo, prod)

    def read():
        d = gh_json(root, ["api", f"repos/{repo}/commits/{head}/status"], "gh api status") or {}
        return next((s for s in d.get("statuses") or [] if s.get("context") == approval_trail.STATUS_CONTEXT), {})

    if not required and not read():
        return

    policy = limits.policy("release-steps.trail-status")._replace(seconds=timeout)
    got = waits.wait_until(read, lambda s: s.get("state") == "success", policy=policy)
    if not got.done:
        last = got.value or {}
        raise StepError(
            f"{approval_trail.STATUS_CONTEXT} が success にならない（{last.get('state') or '無い'}）: {last.get('description') or ''}"
        )


def dev_run_title(root, workflow, pr):
    """開発版の workflow の run-name（`release-dev.yml` が持つ形）へ PR の番号を入れた表示名。"""
    text = (root / ci_workflows.WORKFLOW_DIR / workflow).read_text(encoding="utf-8")
    m = re.search(r"^run-name:\s*[\"']?(.+?)[\"']?\s*$", text, re.M)
    if not m:
        raise StepError(f"{workflow} に run-name が無い（起動した実行を見分けられない）", EXIT_PRECONDITION)
    return re.sub(r"\$\{\{\s*inputs\.pr\s*\}\}", str(pr), m.group(1))


def release_dev_by_workflow(root, a, workflow, pr):
    """開発版の workflow を起動し、その実行が終わるまで待つ。成功ならその URL、失敗は 1、上限は 75。"""
    title = dev_run_title(root, workflow, pr)
    fields = ["run", "list", "--workflow", workflow, "--json", "databaseId,displayTitle,status,conclusion,url", "--limit", "50"]
    before = {r.get("databaseId") for r in gh_json(root, fields, "gh run list") or []}
    p = gh_parts.gh(["workflow", "run", workflow, "-f", f"pr={pr}"], cwd=root)
    if p.returncode != 0:
        raise StepError(f"{workflow} を起動できない: {p.stderr.strip()[:300]}")

    def latest():
        runs = [
            r for r in gh_json(root, fields, "gh run list") or [] if r.get("displayTitle") == title and r.get("databaseId") not in before
        ]
        return runs[0] if runs else {}

    got = waits.wait_until(latest, lambda r: r.get("status") == "completed", policy=limits.policy("release-steps.dev-workflow"))
    run_ = got.value or {}
    if not got.done:
        raise StepError(f"{workflow} の実行（{run_.get('url') or '未検出'}）が上限までに終わらない", EXIT_INFRA_WAIT)
    if run_.get("conclusion") != "success":
        raise StepError(f"{workflow} の実行が {run_.get('conclusion')} で終わった: {run_.get('url')}")
    return run_.get("url") or ""


def release_dev(ctx: ReleaseCtx, workflow, ops):
    """宣言 release.dev_workflow のある開発版: PR を作るか使い回し、開いていれば workflow にマージさせる（#2773 の E10）。"""
    root, a, plugin, plugins, branch, base = ctx.root, ctx.a, ctx.plugin, ctx.plugins, ctx.branch, ctx.base
    pr = ops._reusable_release_pr(root, branch, base)
    new = pr is None
    if new:
        pr = {
            "number": ops.create_pr(root, base, branch, f"Release: {plugin} v{a.version}", ops._release_pr_body(root, a, plugin, plugins)),
            "state": "OPEN",
        }
    n = merged = pr["number"]
    items = [{"kind": "pr", "name": f"#{n}", "result": "already_merged", "base": base}]
    if pr["state"] == "OPEN":
        merged, run_url = _dev_merge_by_workflow(ctx, workflow, ops, n, new)
        items[0].update({"result": "merged" if merged == n else "compared", "run": run_url})
        if merged != n:  # 衝突したので -sync の PR が取り込んだ。元の PR は照合にだけ使う（_merge_release_pr と同じ形）
            items.append({"kind": "pr", "name": f"#{merged}", "result": "merged", "base": base, "head": f"{branch}-sync"})
    metrics = {
        "channel": "dev",
        "version": a.version,
        "release_pr": n,
        "sync_pr": merged if merged != n else None,
        "merge_commit": ops.merge_commit_of(root, merged),
        "plugins": ",".join(plugins),
    }
    metrics["dev_workflow"] = workflow
    emit(result(TOOL, "ok", f"{plugin} v{a.version} を {base} へ出した（#{merged}・{workflow}）", items, metrics))


def _dev_merge_by_workflow(ctx: ReleaseCtx, workflow, ops, n, new):
    """開いた開発版の PR を workflow でマージし、(取り込んだ PR, workflow の実行の URL) を返す。ベースブランチと衝突していれば
    手元の経路と同じく同期のブランチ（<開発版のブランチ>-sync）で取り込み（`_merge_sync_pr`）、その PR のマージも workflow に任せる。
    再開で -sync が取り込み済みなら（`_merged_sync_pr`）起動しない。"""
    root, a, branch, base = ctx.root, ctx.a, ctx.branch, ctx.base
    if not new and (synced := ops._merged_sync_pr(root, branch, base)):
        return synced, None
    if not _pr_conflicting(root, n, ops):
        try:
            return n, release_dev_by_workflow(root, a, workflow, n)
        except StepError as e:  # workflow の中の merge-when-green が衝突で落ちたときだけ -sync へ回す
            if e.code == EXIT_INFRA_WAIT or not _pr_conflicting(root, n, ops):
                raise
    runs = []

    def merge(m):
        runs.append(release_dev_by_workflow(root, a, workflow, m))
        return ops.merge_commit_of(root, m)

    sync_n, _ = ops._merge_sync_pr(root, a, ctx.plugin, branch, base, n, merge=merge)
    return sync_n, runs[-1] if runs else None


def _pr_conflicting(root, n, ops) -> bool:
    """PR がベースブランチと衝突しているか（mergeStateStatus が DIRTY）。"""
    return (ops.pr_view(root, n, "mergeStateStatus") or {}).get("mergeStateStatus") == "DIRTY"


def judge_pr(repo, pr, head, prod):
    """証跡のチェックの判定（I1〜I4・I10）。同じ先端の開いた PR がほかにあれば落とす。"""
    others = [n for n in approval_trail.open_prs_at(repo, head, prod) if n != pr]
    if others:
        return approval_trail.Judgment("failure", f"同じ先端の PR がほかにある: {', '.join(f'#{n}' for n in others)}")
    return approval_trail.judge_trails_on(repo, pr, head)


def approval_check(a):
    """PR の承認の証跡を判定し、先端へ commit status を付ける（証跡のチェックの workflow が打つ。#2773 の F4）。
    レビューが必須の場合（cd-review）は証跡を書かないため何も付けない（I8。付けると証跡が無い failure でマージが止まる）。
    ただし本番チャネルの ruleset が証跡のチェックを必須にしていれば、レビュー必須でも判定して付ける（決定 12 の手順 3。
    承認数 1 のまま確認用の PR で success が付くことを確かめる移行の途中）。"""
    root = git_root(a.root)
    prod = delivery.load_delivery(root).production
    repo, head = repo_or_stop(root), None
    try:
        head, base, state = approval_trail.read_head(repo, a.pr)
        if base != prod:
            emit(result(TOOL, "ok", f"PR #{a.pr} の宛先 {base} が本番チャネルでない（何も付けない）", [], {"pr": a.pr, "base": base}))
        if (case := release_case_or_stop(root)).case == release_case.CD_REVIEW and not trail_required(root, repo, prod):
            msg = f"リリースの場合が {case.case}（レビューで承認する）で、証跡のチェックを付けない"
            emit(result(TOOL, "ok", msg, [], {"pr": a.pr, "case": case.case}))
        pr = a.pr
        if state != "open":  # 閉じた PR の先端を持つ開いた PR を判定し直す（I10）
            same = approval_trail.open_prs_at(repo, head, prod)
            if not same:
                emit(result(TOOL, "ok", f"PR #{a.pr} は閉じていて、同じ先端の開いた PR が無い（何も付けない）", [], {"pr": a.pr}))
            pr = same[0]
        j = judge_pr(repo, pr, head, prod)
        approval_trail.post_status(repo, head, j)
        if j.state == "success":  # 並んだ実行が同じ先端の PR を開いていないかを読み直す（I10）
            again = judge_pr(repo, pr, head, prod)
            if again.state != "success":
                approval_trail.post_status(repo, head, again)
                j = again
    except approval_trail.TrailError as e:
        if head:
            try:
                approval_trail.post_status(repo, head, approval_trail.Judgment("error", str(e)))
            except approval_trail.TrailError:
                pass  # 付けられなければ結果 JSON の stopped だけで知らせる
        raise StepError(str(e))
    metrics = {"pr": pr, "head": head, "state": j.state, "description": j.description, "approver": j.approver, "at": j.at}
    emit(result(TOOL, "ok", f"PR #{pr} の {approval_trail.STATUS_CONTEXT}: {j.state}（{j.description}）", [], metrics))


def approval_post(a):
    """承認資料を承認資料のコメントとしてリリースの PR に置く。資料の「承認したコミット」が PR の先端と違えば置かずに止め、
    同じ先端・同じ本文のものがあれば置かない（#2773 の E4。提示した資料と承認の対象を食い違わせない）。"""
    root = git_root(a.root)
    path = Path(a.approval) if Path(a.approval).is_absolute() else root / a.approval
    if not path.is_file():
        raise StepError(f"承認資料が無い: {path}", EXIT_PRECONDITION)
    try:
        material_sha = approved_commit.material_sha(path)
    except ValueError as e:
        raise StepError(str(e), EXIT_PRECONDITION) from e
    repo = repo_or_stop(root)
    try:
        head = approval_trail.read_head(repo, a.pr)[0]
        comments = approval_trail.read_comments(repo, a.pr) if material_sha == head.lower() else []
    except approval_trail.TrailError as e:
        raise StepError(str(e))
    metrics = {"pr": a.pr, "head": head, "approval": str(path), "approved_sha": material_sha}
    if material_sha != head.lower():
        nxt = f"release-steps.py release --version <版> --channel prod を打ち直して先端 {head[:8]} で承認資料を作り直す"
        raise StepError(f"承認資料の承認したコミット {material_sha[:8]} が PR #{a.pr} の先端 {head[:8]} と違う（{nxt}）", EXIT_PRECONDITION)
    body = approval_trail.render_request(path.read_text(encoding="utf-8"), head)
    if any((c.get("body") or "").strip() == body.strip() for c in comments if isinstance(c, dict)):
        emit(result(TOOL, "ok", f"PR #{a.pr} に先端 {head[:8]} の同じ承認資料が既にある", [], metrics))
    got = gh_rest.comment(repo, a.pr, body)
    if not got.ok:
        raise StepError(f"承認資料のコメントを置けない: {got.error[:300]}")
    metrics["comment_url"] = (got.value or {}).get("url")
    emit(
        result(
            TOOL,
            "ok",
            f"PR #{a.pr} に先端 {head[:8]} の承認資料を置いた",
            [{"kind": "comment", "name": metrics["comment_url"], "result": "written"}],
            metrics,
        )
    )


def release_case_out(a):
    """リリースの場合（#2773 の F1）を metrics に出す。"""
    c = release_case_or_stop(git_root(a.root))
    emit(result(TOOL, "ok", f"リリースの場合: {c.case}（{c.reason}）", [], c._asdict()))
