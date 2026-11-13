"""CD のある場合（`lib/release_case.py` の cd-no-review / cd-review）のリリースの経路（#2773。`release-steps.py` の release・
approval-check・approval-post・release-case から分けた）。

- 本番: 開発版の PR → 本番の PR → CI の待ち → PR の上の承認（証跡かレビュー）と先端の照合 → 管理者の迂回なしのマージ → 公開の後
- 開発版: 宣言 `release.dev_workflow` の workflow に開発版の PR をマージさせ、結果を待つ
- 証跡のチェック: 証跡を判定して PR の先端へ commit status を付ける（同じ先端の PR が並べば落とす。I10）

`ops` は `release-steps.py`（find_pr・create_pr・wait_and_merge・merge_commit_of・pr_check_buckets・_merge_release_pr・
_reusable_release_pr・_release_pr_body・_published）。出す（emit）のはここで、終了コードは `step_result` の値を使う。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

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
from release_lib.names import release_tag
from step_result import EXIT_GATE, EXIT_INFRA_WAIT, EXIT_PRECONDITION, EXIT_UNREADABLE, StepError, emit, gh_json, git, git_root, result

TOOL = approval.TOOL


def release_case_or_stop(root):
    try:
        return release_case.case_of(root)
    except release_case.CaseUnreadable as e:
        raise StepError(f"リリースの場合を決められない: {e}", EXIT_PRECONDITION)


def dev_workflow_of(root) -> str | None:
    """開発版の workflow のファイル名（`.ndf/supervise.json` の release.dev_workflow）。無ければ None（手元でマージする）。"""
    got = (delivery.load_delivery(root).release or {}).get("dev_workflow")
    return got if isinstance(got, str) and got else None


def repo_or_stop(root):
    repo = os.environ.get("GITHUB_REPOSITORY") or repo_lib.owner_repo(root)
    if not repo:
        raise StepError("リポジトリ（origin の owner/repo）を決められない", EXIT_UNREADABLE)
    return repo


def release_prod(root, a, case, plugin, plugins, branch, base, prod, ops):
    """場合 1・2 の本番: 開発版の PR → 本番の PR → CI の待ち → 承認の照合 → 迂回なしのマージ → 公開の後（#2773）。
    承認が先端と一致しなければ本番の PR を開いたまま承認ゲート（10）で止める。"""
    ver = a.version
    metrics = {"channel": "prod", "version": ver, "case": case.case, "release_pr": None, "sync_pr": None, "main_pr": None}
    metrics.update({"tag": None, "merge_commit": None, "plugins": ",".join(plugins)})
    items: list[dict] = []
    git(root, "fetch", "-q", "origin", "--tags")
    done = ops.find_pr(root, branch, prod, states=("MERGED", "OPEN"))
    if done and done["state"] == "MERGED":  # 再開: 本番チャネルへのマージは済んでいる
        git(root, "fetch", "-q", "origin")
        merge = ops.merge_commit_of(root, done["number"]) or git(root, "rev-parse", f"origin/{prod}").stdout.strip()
        items.append({"kind": "pr", "name": f"#{done['number']}", "result": "already_merged", "base": prod})
        ops._published(root, a, plugin, prod, done["number"], merge, items, metrics)
    if done:
        main_pr = done["number"]
    else:
        if git(root, "rev-parse", "-q", "--verify", f"refs/tags/{release_tag(plugin, ver)}", check=False).returncode == 0:
            raise StepError(f"タグ {release_tag(plugin, ver)} は既にある")
        release_pr, merged_pr, _ = ops._merge_release_pr(root, a, plugin, plugins, branch, base)
        metrics.update({"release_pr": release_pr, "sync_pr": merged_pr if merged_pr != release_pr else None})
        repo_lib.restore_branch(root, branch, run_git=git)  # 開発版の PR のマージで消えた版上げのブランチを戻す
        title = f"Release: {plugin} v{ver} を {prod} へ"
        body = f"{plugin} v{ver} を {prod} へ出す（開発版の PR #{release_pr}）。\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)"
        main_pr = ops.create_pr(root, prod, branch, title, body)
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
    ops._published(root, a, plugin, prod, main_pr, merge or git(root, "rev-parse", f"origin/{prod}").stdout.strip(), items, metrics)


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


def wait_trail_status(root, repo, prod, head, timeout):
    """`ndf/release-approval` が本番チャネルの必須チェックにあるとき、先端のその状態が success になるまで待つ。"""
    try:  # 読めなければ待たない（マージの判定は GitHub が必須チェックで持つ）
        required = ci_workflows.required_contexts(gh_json(root, ["api", f"repos/{repo}/rules/branches/{prod}"], "gh api rules")) or []
    except (StepError, ValueError):
        required = []
    if approval_trail.STATUS_CONTEXT not in required:
        return

    def read():
        d = gh_json(root, ["api", f"repos/{repo}/commits/{head}/status"], "gh api status") or {}
        return next((s for s in d.get("statuses") or [] if s.get("context") == approval_trail.STATUS_CONTEXT), {})

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


def release_dev(root, a, plugin, plugins, branch, base, workflow, ops):
    """宣言 release.dev_workflow のある開発版: PR を作るか使い回し、開いていれば workflow にマージさせる（#2773 の E10）。"""
    pr = ops._reusable_release_pr(root, branch, base)
    if pr is None:
        pr = {
            "number": ops.create_pr(root, base, branch, f"Release: {plugin} v{a.version}", ops._release_pr_body(root, a, plugin, plugins)),
            "state": "OPEN",
        }
    n = pr["number"]
    item = {"kind": "pr", "name": f"#{n}", "result": "already_merged", "base": base}
    if pr["state"] == "OPEN":
        item.update({"result": "merged", "run": release_dev_by_workflow(root, a, workflow, n)})
    metrics = {
        "channel": "dev",
        "version": a.version,
        "release_pr": n,
        "merge_commit": ops.merge_commit_of(root, n),
        "plugins": ",".join(plugins),
    }
    metrics["dev_workflow"] = workflow
    emit(result(TOOL, "ok", f"{plugin} v{a.version} を {base} へ出した（#{n}・{workflow}）", [item], metrics))


def judge_pr(repo, pr, head, prod):
    """証跡のチェックの判定（I1〜I4・I10）。同じ先端の開いた PR がほかにあれば落とす。"""
    others = [n for n in approval_trail.open_prs_at(repo, head, prod) if n != pr]
    if others:
        return approval_trail.Judgment("failure", f"同じ先端の PR がほかにある: {', '.join(f'#{n}' for n in others)}")
    return approval_trail.judge_trails_on(repo, pr, head)


def approval_check(a):
    """PR の承認の証跡を判定し、先端へ commit status を付ける（証跡のチェックの workflow が打つ。#2773 の F4）。"""
    root = git_root(a.root)
    prod = delivery.load_delivery(root).production
    repo, head = repo_or_stop(root), None
    try:
        head, base, state = approval_trail.read_head(repo, a.pr)
        if base != prod:
            emit(result(TOOL, "ok", f"PR #{a.pr} の宛先 {base} が本番チャネルでない（何も付けない）", [], {"pr": a.pr, "base": base}))
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
    """承認資料を承認資料のコメントとしてリリースの PR に置く。同じ先端のものがあれば置かない（#2773 の E4）。"""
    root = git_root(a.root)
    path = Path(a.approval) if Path(a.approval).is_absolute() else root / a.approval
    if not path.is_file():
        raise StepError(f"承認資料が無い: {path}", EXIT_PRECONDITION)
    repo = repo_or_stop(root)
    try:
        head = approval_trail.read_head(repo, a.pr)[0]
        kept = approval_trail.has_request(approval_trail.read_comments(repo, a.pr), head)
    except approval_trail.TrailError as e:
        raise StepError(str(e))
    metrics = {"pr": a.pr, "head": head, "approval": str(path)}
    if kept:
        emit(result(TOOL, "ok", f"PR #{a.pr} に先端 {head[:8]} の承認資料が既にある", [], metrics))
    got = gh_rest.comment(repo, a.pr, approval_trail.render_request(path.read_text(encoding="utf-8"), head))
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
