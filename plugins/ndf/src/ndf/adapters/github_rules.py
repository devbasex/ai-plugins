"""本番ブランチの ruleset と従来のブランチ保護の事実（外部との接続の層。#3672 の I10。`github.py` から分けた）。

GitHub へはつながない。読み取りは `ndf.adapters.github.GitHub.rule_facts` が行い、ここは応答の配列と辞書から事実を組んで重ねる。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, NamedTuple, Optional

# 迂回の可否（ruleset の `current_user_can_bypass`）の強さ。弱いほうを全体の値にする。`always`・`pull_requests_only` だけが
# 本番の PR を越えられる（10 系の `release_case._BYPASS_ORDER` と同じ並び）
BYPASS_ORDER = ("never", "exempt", "pull_requests_only", "always")
# PR の規則の種類（`rules/branches/<branch>` の `type`）。承認の数とマージの方式の指定を持つ
PULL_REQUEST_RULE = "pull_request"


class RuleFacts(NamedTuple):
    """本番ブランチの ruleset と従来のブランチ保護を重ねた事実（#3672 の I10。10 系の `release_case.RuleFacts` と同じ値）。

    `bypass` は当たった ruleset の `current_user_can_bypass`（どれか 1 つでも越えられない値なら、その値。ruleset が無ければ None）。
    `queue_merge_method` は merge queue の規則の `parameters.merge_method`（`MERGE`・`SQUASH`・`REBASE`）。
    """

    present: bool
    approvals: int
    queue: bool
    up_to_date: bool
    bypass: Optional[str]
    queue_merge_method: Optional[str] = None


NO_RULES = RuleFacts(False, 0, False, False, None)


def _weakest(bypasses: Iterable[Optional[str]]) -> Optional[str]:
    known = [b for b in bypasses if b in BYPASS_ORDER]
    return min(known, key=BYPASS_ORDER.index) if known else None


def _review_count(raw: Any) -> int:
    value = raw.get("required_approving_review_count") if isinstance(raw, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def rule_facts_of(rules: Sequence[dict], bypasses: Sequence[Optional[str]]) -> RuleFacts:
    """`rules/branches/<branch>` の規則の配列と、当たった ruleset ごとの迂回の可否から事実を組む。"""
    if not rules:
        return NO_RULES
    params = [(r.get("type"), r.get("parameters") if isinstance(r.get("parameters"), dict) else {}) for r in rules]
    reviews = [_review_count(p) for kind, p in params if kind == PULL_REQUEST_RULE]
    up_to_date = any(kind == "required_status_checks" and p.get("strict_required_status_checks_policy") for kind, p in params)
    queues = [p for kind, p in params if kind == "merge_queue"]
    queue_method = queues[0].get("merge_method") if queues else None
    queue = bool(queues)
    return RuleFacts(True, max(reviews) if reviews else 0, queue, bool(up_to_date), _weakest(bypasses), queue_method)


def classic_facts_of(protection: dict) -> RuleFacts:
    """従来のブランチ保護（`branches/<branch>/protection`）から事実を組む。保護を読めるのは管理者だけなので、
    管理者に保護を課さない（`enforce_admins` が偽）ときは迂回できる（`always`）とみなす。"""
    checks = protection.get("required_status_checks")
    enforce = protection.get("enforce_admins")
    up_to_date = isinstance(checks, dict) and bool(checks.get("strict"))
    enforced = isinstance(enforce, dict) and bool(enforce.get("enabled"))
    approvals = _review_count(protection.get("required_pull_request_reviews"))
    return RuleFacts(True, approvals, False, up_to_date, "never" if enforced else "always")


def merge_facts(a: RuleFacts, b: RuleFacts) -> RuleFacts:
    """ruleset と従来のブランチ保護の事実を重ねる。GitHub は両方を効かせるため、厳しいほうを採る（迂回は弱いほう）。"""
    if not a.present or not b.present:
        return a if a.present else b
    bypass, queue_method = _weakest((a.bypass, b.bypass)), a.queue_merge_method or b.queue_merge_method
    return RuleFacts(True, max(a.approvals, b.approvals), a.queue or b.queue, a.up_to_date or b.up_to_date, bypass, queue_method)
