"""trunk_release.py: CD の無い trunk の本番（#3703）。版上げの PR（版上げのブランチ → 本番ブランチ）が本番の PR を兼ねる。

承認を読む前に版上げの PR を開き（使い回す）、承認が無いか承認の SHA が PR の今の head と違えば検査を待って承認ゲート（10）で
止める。一致すれば本番の取り込み（`ops.land`。`release_lib/prod_land.py`）へ渡す。承認の後に head が動いたら許容リストで入れ直さず、
承認を取り直させる（決定 2・不変条件 I2）。マージ済みの PR からの再開は、承認の SHA とマージされた PR の head の一致で確かめる
（squash・rebase でマージコミットが承認の SHA を含まなくてもよい）。

`ops` は `release-steps.py` のモジュール（git・find_pr・create_pr・pr_view・merge_commit_of・land・pr_check_buckets・_release_pr_body）。
出す（emit）のはここで、公開は呼び手（`_published`）が行う。
"""

from __future__ import annotations

from pathlib import Path

import approved_commit as ac
from release_lib import approval_trail, names, prod_land, reviewed
from release_lib.approval import HeadMoved
from release_lib.context import ReleaseCtx
from release_lib.land_read import merged_or_tip
from step_result import EXIT_GATE, EXIT_PRECONDITION, EXIT_UNREADABLE, StepError, emit, result
from strategy_rows import RELEASE_PREFIX

TOOL = "release"


def _trunk_material(ctx: ReleaseCtx) -> Path | None:
    material = getattr(ctx.a, "approval", None)
    if not material:
        return None
    path = Path(material)
    return path if path.is_absolute() else Path(ctx.root) / path


def read_trunk_approval(ctx: ReleaseCtx) -> str | None:
    """承認したコミット。`--approved-sha` があればその値、無ければ `--approval` の承認資料の承認の記録。
    承認がまだ無い（資料が無い・承認の記録が無い・記録の SHA が違う）ときは None。資料の欄の形が壊れていれば StepError（3）。"""
    if sha := getattr(ctx.a, "approved_sha", None):
        try:
            return ac.parse_sha(sha)
        except ValueError as e:
            raise StepError(str(e), EXIT_UNREADABLE) from e
    path = _trunk_material(ctx)
    if path is None or not path.is_file():
        return None
    try:
        return ac.from_material(path)
    except ac.Unapproved:
        return None
    except ValueError as e:
        raise StepError(str(e), EXIT_PRECONDITION) from e


def _bump_pr_head(ctx: ReleaseCtx, ops, pr: int) -> str:
    head = str((ops.pr_view(ctx.root, pr, "headRefOid") or {}).get("headRefOid") or "").lower()
    if not head:
        raise StepError(f"版上げの PR #{pr} の head を読めない")
    return head


def open_release_pr(ctx: ReleaseCtx, ops) -> int:
    """開いた版上げの PR を使い回すか作り、番号を返す（マージ済みの判定は `release_trunk` が先に行う）。"""
    root, a, plugin, branch, prod = ctx.root, ctx.a, ctx.plugin, ctx.branch, ctx.prod
    found = ops.find_pr(root, branch, prod, states=("OPEN",))
    if found:
        return found["number"]
    body = ops._release_pr_body(root, a, plugin, ctx.plugins)
    return ops.create_pr(root, prod, branch, f"Release: {plugin} v{a.version}", body)


def waiting_out(ctx: ReleaseCtx, pr: int, head: str, approved: str | None, items: list[dict], metrics: dict, moved: bool = False) -> dict:
    """承認ゲート（10）の結果。`next` に承認資料の作り直しと承認の取り直しを載せる。"""
    a, plugin = ctx.a, ctx.plugin
    material = getattr(a, "approval", None) or "<承認資料>"
    if moved:
        summary = f"{plugin} v{a.version} のリリースを止めた: 版上げの PR #{pr} の head が承認した {str(approved)[:8]} から動いた"
    elif approved:
        summary = f"{plugin} v{a.version} のリリース承認を待つ: 承認した {approved[:8]} が版上げの PR #{pr} の head {head[:8]} と違う"
    else:
        summary = f"{plugin} v{a.version} のリリース承認を待つ（版上げの PR #{pr}・head {head[:8]}）"
    nxt = (
        f"{RELEASE_PREFIX}{a.version} の head {head[:8]} で承認資料（release-steps.py approval-facts）を作り直して承認を得て、"
        f"release-steps.py approve --approval {material} --approved-sha {head} --by user で承認の記録を書き、{prod_land.release_retry_command(a)} を打ち直す"
    )
    row = {"kind": "pr", "name": f"#{pr}", "result": "awaiting_approval", "base": ctx.prod, "head": head}
    m = {**metrics, "main_pr": pr, "compared_head": head, "approved_sha": approved}
    return result(TOOL, "gate", summary, [*items, row], m, None, nxt)


def _metrics(ctx: ReleaseCtx, pr: int | None) -> dict:
    return ctx.prod_metrics(release_pr=pr, main_pr=pr)


def _landed_pr(ctx: ReleaseCtx, ops):
    """本番ブランチへ入った版上げの PR とその取り込みのコミット。マージ済みの PR か、早送りで閉じて取り込みの記録がある PR。無ければ None。"""
    root, branch, prod = ctx.root, ctx.branch, ctx.prod
    done = ops.find_pr(root, branch, prod, states=("MERGED",))
    if done:
        ops.git(root, "fetch", "-q", "origin")
        return done["number"], ops.merge_commit_of(root, done["number"])
    closed = ops.find_pr(root, branch, prod, states=("CLOSED",))
    if closed:
        approved = read_trunk_approval(ctx)
        commit = prod_land.landed_commit(root, closed["number"], approved) if approved else None
        if commit:
            return closed["number"], commit
    return None


def _resume_landed(ctx: ReleaseCtx, ops, pr: int, commit: str | None):
    """再開: 本番ブランチへ入った版上げの PR の head が承認の SHA と同じことを確かめ、無いものだけを公開へ渡す（I2）。"""
    root, prod = ctx.root, ctx.prod
    approved = read_trunk_approval(ctx)
    if approved is None:
        raise StepError(f"版上げの PR #{pr} は {prod} へ入っているが、承認の記録が無い。承認資料を戻してから打ち直す")
    head = _bump_pr_head(ctx, ops, pr)
    if head != approved.lower():
        raise StepError(f"本番ブランチへ入った版上げの PR #{pr} の head {head[:8]} が承認したコミット {approved[:8]} と違う")
    metrics = {**_metrics(ctx, pr), "approved_sha": approved, "compared_head": head}
    items = [{"kind": "pr", "name": f"#{pr}", "result": "already_merged", "base": prod}]
    merge = merged_or_tip(root, commit, prod, ops.git)
    return pr, merge, items, metrics


def release_trunk(ctx: ReleaseCtx, ops) -> tuple[int, str, list[dict], dict]:
    """CD の無い trunk の本番（詳細設計の「`trunk_release.release_trunk` の判定」）。(版上げの PR, マージコミット, 行, 計測) を返す。"""
    root, a, plugin, prod = ctx.root, ctx.a, ctx.plugin, ctx.prod
    ops.git(root, "fetch", "-q", "origin", "--tags")
    if landed := _landed_pr(ctx, ops):
        return _resume_landed(ctx, ops, *landed)
    names.require_untagged(root, plugin, a.version, git=ops.git)
    pr = open_release_pr(ctx, ops)
    head = _bump_pr_head(ctx, ops, pr)
    approved = read_trunk_approval(ctx)
    metrics = _metrics(ctx, pr)
    items: list[dict] = []
    if approved is None or approved.lower() != head:
        # 承認資料は検査がそろった head で作る。承認の前の 1 回目もここで待つ（落ちれば 1、上限は 75）
        reviewed.wait_ci_except(root, pr, (approval_trail.STATUS_CONTEXT,), float(a.ci_wait), ops)
        emit(waiting_out(ctx, pr, head, approved, items, metrics), EXIT_GATE)
    try:
        merge = ops.land(root, a, pr, head)
    except HeadMoved:
        emit(waiting_out(ctx, pr, head, approved, items, metrics, moved=True), EXIT_GATE)
    metrics.update({"approved_sha": approved, "compared_head": head})
    items.append({"kind": "pr", "name": f"#{pr}", "result": "merged", "base": prod})
    ops.git(root, "fetch", "-q", "origin")
    return pr, merged_or_tip(root, merge, prod, ops.git), items, metrics
