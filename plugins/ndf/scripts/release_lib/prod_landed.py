"""prod_landed.py: 本番ブランチへ入った本番の PR からの再開（#3703。`release_lib/reviewed.py` から分けた）。

本番の取り込み（`prod_land.land`）は、人のマージ（`merge.actor: human`）・merge queue を待つと 6 で返り、打ち直しで再開する。
再開ではマージし直さないため、本番ブランチへ入った head を承認と照らしてから公開へ渡す（I2）。経路はどれもここを通る。

- `landed_prod_pr`: マージ済みの本番の PR か、ruleset の無い `strict` の早送りで閉じて取り込みの記録がある本番の PR
- `require_approved_landing`: CD のある場合。入った head が PR の上の承認（証跡かレビュー）と一致するか
- `require_landed_head`: CD の無い場合。入った head を承認したコミットと許可リストで比べる。マージ後の祖先関係でなく
  PR の head で比べるため、squash・rebase で入れた取り込みも再開できる
"""

from __future__ import annotations

import os
from typing import Callable

import repo as repo_lib
from release_lib import approval, approval_trail
from step_result import EXIT_UNREADABLE, StepError


def repo_or_stop(root):
    repo = os.environ.get("GITHUB_REPOSITORY") or repo_lib.owner_repo(root)
    if not repo:
        raise StepError("リポジトリ（origin の owner/repo）を決められない", EXIT_UNREADABLE)
    return repo


def landed_head(root, pr: int) -> str:
    """本番の PR `pr` の head（小文字）。読めなければ StepError（1）。"""
    try:
        return approval_trail.read_head(repo_or_stop(root), pr)[0]
    except approval_trail.TrailError as e:
        raise StepError(str(e)) from e


def require_approved_landing(root, pr: int, case, head: str | None = None) -> str:
    """本番ブランチへ入った本番の PR `pr` の head（`head`。省けば PR から読む）が PR の上の承認（証跡かレビュー）と一致することを確かめ、
    その head を返す（CD のある場合の再開）。人のマージは承認の後に積んだコミットごと入りうるため、一致しなければ StepError（1）で止め、
    公開しない。読めなければ StepError（1）。"""
    head = head or landed_head(root, pr)
    try:
        judged = approval.approved_on_pr(repo_or_stop(root), pr, head, case)
    except approval_trail.TrailError as e:
        raise StepError(str(e)) from e
    if judged.state != "success":
        raise StepError(
            f"本番ブランチへ入った本番の PR #{pr} の head {head[:8]} が PR の上の承認と一致しない（{judged.description}）。公開しない"
        )
    return head


def require_landed_head(root, pr: int, approved: str, allowed_of: Callable):
    """本番ブランチへ入った本番の PR `pr` の head を、承認したコミット `approved` と許可リスト（`allowed_of(head)`）で比べた判定を返す
    （CD の無い場合の再開）。承認の外のコミットごと入っていれば・比べられなければ StepError（1）で止め、公開しない。"""
    head = landed_head(root, pr)
    verdict = approval.check_head(root, approved, head, allowed_of(head))
    if not verdict.ok:
        raise StepError(
            f"本番ブランチへ入った本番の PR #{pr} の head {head[:8]} が承認したコミット {str(approved)[:8]} と合わない"
            f"（{verdict.reason}）。公開しない"
        )
    return verdict


def landed_prod_pr(root, branch: str, prod: str, done: dict | None, ops, case) -> tuple[int, str | None] | None:
    """本番ブランチへ入った本番の PR とその取り込みのコミット（再開）。`done` はマージ済みか開いた PR の探索の結果。
    マージ済みの PR か、ruleset の無い `strict` の早送りで閉じて取り込みの記録がある PR（`ops.landed_commit`）を返し、無ければ None。
    `case`（CD のある場合）なら、入った head が PR の上の承認と一致することを確かめる（`require_approved_landing`）。
    CD の無い場合の承認との照合は呼び手が `require_landed_head` で行う。"""
    if done and done["state"] == "MERGED":
        if case is not None:
            require_approved_landing(root, done["number"], case)
        return done["number"], ops.merge_commit_of(root, done["number"])
    landed = getattr(ops, "landed_commit", None)
    if done or landed is None:
        return None
    closed = ops.find_pr(root, branch, prod, states=("CLOSED",))
    if not closed:
        return None
    head = landed_head(root, closed["number"])
    commit = landed(root, closed["number"], head)
    if not commit:
        return None
    if case is not None:
        require_approved_landing(root, closed["number"], case, head)
    return closed["number"], commit
