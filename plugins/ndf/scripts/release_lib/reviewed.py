"""CD のある場合（`lib/release_case.py` の cd-no-review / cd-review / cd-admin）のリリースの経路（#2773。`release-steps.py` の release・
approval-check・approval-post・release-case から分けた）。

- 本番: 開発版の PR → 本番の PR → CI の待ち → PR の上の承認（証跡かレビュー）と先端の照合 → 本番の取り込み → 公開の後。
  本番の取り込み（`prod_land.land`）が宣言の主体と方式で入れ、迂回とその記録は `land_approved` が持つ（#3703 の決定 10）
- 開発版: ブランチ戦略の宣言の開発版のチャネル（`channels` の `dev`）の workflow に開発版の PR をマージさせ、結果を待つ
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
from release_lib.land_read import merged_or_tip
from release_lib.included import RELEASE_PREFIX
from release_lib.names import require_untagged
from release_lib.port_mark import NO_PORT_LINE, ensure_no_port_line
from step_result import EXIT_GATE, EXIT_INFRA_WAIT, EXIT_PRECONDITION, EXIT_UNREADABLE, StepError, emit, gh_json, git, git_root, result

TOOL = approval.TOOL
# 迂回の記録の行（items の `record`。#3506 の I7）
ADMIN_RECORD = "admin_merge"
# 迂回の記録を書けなかったときに、本番の完了と分けて報告する文（#3506 の決定 7）
UNWRITTEN_NOTE = "迂回の記録を書けなかった（本番は出ている）"


release_case_or_stop = release_case.case_or_stop  # release-steps.py と merged_lib が同じ止め方を使う


def refresh_release_pr_body(root, pr, body: Callable[[], str]):
    """使い回す開いた開発版の PR の本文を `body()`（今の版の本文）へ書き直す。作ったばかりの PR と閉じた PR は触らない。
    本文の印を読む検査（ndf-next の port）は edited で打ち直される。"""
    if pr.get("new") or pr["state"] != "OPEN":
        return
    p = gh_parts.gh(["pr", "edit", str(pr["number"]), "--body-file", "-"], body(), cwd=root)
    if p.returncode != 0:
        raise StepError(f"gh pr edit #{pr['number']}（本文の書き直し）が失敗: {p.stderr.strip()[:300]}")


def dev_workflow_of(root) -> str | None:
    """開発版の workflow のファイル名（ブランチ戦略の宣言の `channels` の `dev` の `workflow`）。無ければ None（手元でマージする）。"""
    s = repo_lib.strategy_or_none(root)
    return s.dev_workflow if s else None


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
    published: Callable  # (root, a, plugin, prod, main_pr, merge, items, metrics, back=None) 公開の後。結果を出して終わる
    land: Callable  # (root, a, pr, head, trail_url="") -> マージコミット。本番の取り込み（`prod_land.land`。#3703）
    pr_check_buckets: Callable  # (root, n) -> チェックの並び


def release_prod(ctx: ReleaseCtx, case, ops: ProdOps):
    """CD のある場合の本番: 開発版の PR → 本番の PR → CI の待ち → 承認の照合 → マージ（`cd-admin` だけ迂回）→ 公開の後（#2773・#3506）。
    承認が先端と一致しなければ本番の PR を開いたまま承認ゲート（10）で止める。"""
    root, a, plugin, prod = ctx.root, ctx.a, ctx.plugin, ctx.prod
    ver = a.version
    metrics = ctx.prod_metrics(case=case.case)
    items: list[dict] = []
    main_pr = _open_prod_pr(ctx, items, metrics, ops, case)
    repo = repo_or_stop(root)

    def waiting(head, judged):
        how = (
            f"release-steps.py approval-post --pr {main_pr} --approval <承認資料> で承認資料を置いて承認を求め、承認を得たら "
            f"release-steps.py approve --pr {main_pr} --approved-sha {head} --by user で証跡を書いて"
            if case.trail_approval
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
    items.append({"kind": "pr", "name": f"#{main_pr}", "result": "merged", "base": prod, "admin": case.admin_merge})
    git(root, "fetch", "-q", "origin")
    merge = merged_or_tip(root, merge, prod, git)
    ops.published(root, a, plugin, prod, main_pr, merge, items, metrics, back=admin_back())


def _unwritten(pr: int, e: Exception) -> dict:
    """迂回の記録を書けなかった行。"""
    return {"kind": "comment", "name": f"#{pr}", "result": "unwritten", "record": ADMIN_RECORD, "reason": str(e)[:200]}


def admin_record_item(repo: str, pr: int, sha: str, trail_url: str) -> dict:
    """迂回の記録を書き、結果の行を返す（#3506 の I7）。書けなければ `unwritten` の行を返し、例外を上げない（決定 7）。"""
    try:
        wrote, url = approval_trail.write_admin_record(repo, pr, sha, trail_url)
    except (approval_trail.TrailError, ValueError) as e:
        return _unwritten(pr, e)
    return {"kind": "comment", "name": url or f"#{pr}", "result": "written" if wrote else "kept", "record": ADMIN_RECORD}


def admin_record_on_resume(root, repo: str, case, pr: int) -> dict | None:
    """マージ済みの本番の PR に、迂回でマージしたのに記録が無ければ書く（#3506 の I7）。迂回する場合でなければ None。
    「迂回でマージしたか」はマージのときと同じ入力（場合と、利用者の証跡が先端と一致すること）で決め直し、PR のレビューの判定は読まない。"""
    if not case.admin_merge:
        return None
    try:
        head = str((gh_json(root, ["pr", "view", str(pr), "--json", "headRefOid"], "gh pr view") or {}).get("headRefOid") or "")
        judged = approval_trail.judge_trails_on(repo, pr, head, "user") if head else None
    except (StepError, approval_trail.TrailError) as e:
        return _unwritten(pr, e)
    if judged is None or judged.state != "success":
        return None  # 利用者の承認と先端が一致しない: NDF の迂回でマージしたと言えないため記録しない
    return admin_record_item(repo, pr, head, judged.url or "")


def admin_unwritten(items: list[dict]) -> bool:
    """迂回の記録を書けなかった行があるか。"""
    return any(i.get("record") == ADMIN_RECORD and i.get("result") == "unwritten" for i in items if isinstance(i, dict))


def admin_back(back=None):
    """公開の後に打つ関数（`release-steps.py` の `_published` の `back`）。`back`（戻すマージ）を先に打ち、迂回の記録を書けなかった行が
    あれば本番の完了と分けて止まりとして報告する（#3506 の決定 7）。打ち直すと再開の経路が記録だけを書く。"""

    def run(items: list[dict], metrics: dict, done: str) -> tuple[str, int, str]:
        status, code, done = back(items, metrics, done) if back else ("ok", None, done)
        if not admin_unwritten(items):
            return status, code, done
        note = f"{done}。{UNWRITTEN_NOTE}"
        if status != "ok":  # 戻すマージが止まっていればその終了コード（無ければ 1）
            return "stopped", code or 1, note
        return "stopped", 1, note

    return run


def _open_prod_pr(ctx: ReleaseCtx, items, metrics, ops: ProdOps, case=None) -> int:
    """本番の PR を引くか作り、その番号を返す。マージ済みなら公開の後へ進む（再開）。無ければ開発版の PR をマージしてから作る。"""
    root, a, plugin, plugins, branch, base, prod = ctx.root, ctx.a, ctx.plugin, ctx.plugins, ctx.branch, ctx.base, ctx.prod
    ver = a.version
    git(root, "fetch", "-q", "origin", "--tags")
    done = ops.find_pr(root, branch, prod, states=("MERGED", "OPEN"))
    if done and done["state"] == "MERGED":  # 再開: 本番チャネルへのマージは済んでいる
        git(root, "fetch", "-q", "origin")
        merge = merged_or_tip(root, ops.merge_commit_of(root, done["number"]), prod, git)
        items.append({"kind": "pr", "name": f"#{done['number']}", "result": "already_merged", "base": prod})
        if case is not None and (row := admin_record_on_resume(root, repo_or_stop(root), case, done["number"])):
            items.append(row)
        ops.published(root, a, plugin, prod, done["number"], merge, items, metrics, back=admin_back())
    if done:
        return ensure_no_port_line(root, done)["number"]  # 前の版が作った本番の PR は port 検査の印を持たない
    require_untagged(root, plugin, ver, git=git)
    if not ctx.sync:
        # trunk: 版上げの PR が本番の PR を兼ねる。承認と照らす前に本番ブランチへマージしない（#3672 の D17）
        title = f"Release: {plugin} v{ver}"
        body = f"{plugin} v{ver} を {prod} へ出す版上げの PR。\n\n{NO_PORT_LINE}\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)"
        return ops.create_pr(root, prod, branch, title, body)
    release_pr, merged_pr, _ = ops.merge_release_pr(root, a, plugin, plugins, branch, base)
    metrics.update({"release_pr": release_pr, "sync_pr": merged_pr if merged_pr != release_pr else None})
    repo_lib.restore_branch(root, branch, run_git=git)  # 開発版の PR のマージで消えた版上げのブランチを戻す
    title = f"Release: {plugin} v{ver} を {prod} へ"
    body = f"{plugin} v{ver} を {prod} へ出す（開発版の PR #{release_pr}）。\n\n{NO_PORT_LINE}\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)"
    return ops.create_pr(root, prod, branch, title, body)


def merge_on_pr_approval(root, repo, a, case, main_pr, prod, items, metrics, ops, *, waiting, moved, waiting_items=()):
    """CD のある場合（#2773 の決定 7）の照合とマージ: 本番の PR の先端が PR の上の承認（証跡かレビュー）と完全に一致するときだけ、
    本番の取り込み（`ops.land`。#3703）で宣言の主体と方式で入れる。迂回とその記録は `land_approved` が持つ（決定 10）。
    承認が無ければ CI を待って承認ゲート（10）で止め、承認の後に先端が動いても同じゲートで止める。merge queue へ入れた・人のマージを
    待つときは `prod_land.LandPending` で抜ける。
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
    try:
        return ops.land(root, a, main_pr, head, judged.url or "")
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
    """開発版のチャネルに workflow のある開発版: PR を作るか使い回し、開いていれば workflow にマージさせる（#2773 の E10）。"""
    root, a, plugin, plugins, branch, base = ctx.root, ctx.a, ctx.plugin, ctx.plugins, ctx.branch, ctx.base
    pr = ops._reusable_release_pr(root, branch, base)
    new = pr is None
    if new:
        pr = {
            "number": ops.create_pr(root, base, branch, f"Release: {plugin} v{a.version}", ops._release_pr_body(root, a, plugin, plugins)),
            "state": "OPEN",
        }
    refresh_release_pr_body(root, {**pr, "new": new}, lambda: ops._release_pr_body(root, a, plugin, plugins))
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
    metrics["dev_channel_workflow"] = workflow
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


def _pr_to_judge(a, root, repo: str, decl, head: str, base: str, state: str) -> int:
    """証跡を判定する PR の番号。本番チャネル宛てでない・trunk で版上げの PR でない・レビューで承認する場合・閉じて同じ先端の
    開いた PR が無いときは、何も付けずに ok で終える。閉じた PR は同じ先端の開いた PR を判定し直す（I10）。"""
    prod = decl.production
    if base != prod:
        emit(result(TOOL, "ok", f"PR #{a.pr} の宛先 {base} が本番チャネルでない（何も付けない）", [], {"pr": a.pr, "base": base}))
    if decl.base == prod and not (ref := approval_trail.read_head_ref(repo, a.pr)).startswith(RELEASE_PREFIX):
        # trunk ではスプリントの PR も本番宛てになる。リリース承認を取るのは版上げの PR（head が RELEASE_PREFIX）だけ（#3672）
        emit(result(TOOL, "ok", f"PR #{a.pr} は版上げの PR でない（head {ref}。何も付けない）", [], {"pr": a.pr, "head_ref": ref}))
    if (case := release_case_or_stop(root)).case == release_case.CD_REVIEW and not trail_required(root, repo, prod):
        msg = f"リリースの場合が {case.case}（レビューで承認する）で、証跡のチェックを付けない"
        emit(result(TOOL, "ok", msg, [], {"pr": a.pr, "case": case.case}))
    if state == "open":
        return a.pr
    same = approval_trail.open_prs_at(repo, head, prod)
    if not same:
        emit(result(TOOL, "ok", f"PR #{a.pr} は閉じていて、同じ先端の開いた PR が無い（何も付けない）", [], {"pr": a.pr}))
    return same[0]


def _judge_and_post(repo: str, pr: int, head: str, prod: str):
    """証跡を判定して先端へ commit status を付ける。承認なら並んだ実行が同じ先端の PR を開いていないかを読み直す（I10）。"""
    j = judge_pr(repo, pr, head, prod)
    approval_trail.post_status(repo, head, j)
    if j.state == "success":
        again = judge_pr(repo, pr, head, prod)
        if again.state != "success":
            approval_trail.post_status(repo, head, again)
            j = again
    return j


def approval_check(a):
    """PR の承認の証跡を判定し、先端へ commit status を付ける（証跡のチェックの workflow が打つ。#2773 の F4）。
    レビューが必須の場合（cd-review）は証跡を書かないため何も付けない（I8。付けると証跡が無い failure でマージが止まる）。
    ただし本番チャネルの ruleset が証跡のチェックを必須にしていれば、レビュー必須でも判定して付ける（決定 12 の手順 3。
    承認数 1 のまま確認用の PR で success が付くことを確かめる移行の途中）。"""
    root = git_root(a.root)
    decl = delivery.load_delivery(root)
    prod = decl.production
    repo, head = repo_or_stop(root), None
    try:
        head, base, state = approval_trail.read_head(repo, a.pr)
        pr = _pr_to_judge(a, root, repo, decl, head, base, state)
        j = _judge_and_post(repo, pr, head, prod)
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
