"""本番ブランチに効く ruleset と従来のブランチ保護を GitHub から読み、事実へまとめる（ブランチ戦略の解析が使う）。

GitHub の応答の形とページの読み方で変わる部分を、測った値から設定を決める `strategy_measure` から分けて持つ。
GitHub は `api`（`path → (値, 読めない理由)`。404 は理由 `missing`）で読み、テストは差し替える。
"""

from __future__ import annotations

from typing import Any, Callable, NamedTuple, Optional

from ndf.domains.project.items import obj

Api = Callable[[str], tuple[Any, str]]

# GitHub の REST の既定の 1 ページの件数（`per_page` を付けないときの値）
DEFAULT_PER_PAGE = 30


class RuleFacts(NamedTuple):
    """本番ブランチに効く ruleset と従来のブランチ保護の事実（有無・承認の数・merge queue・最新のマージの要否）。"""

    present: bool
    approvals: int
    queue: bool
    up_to_date: bool


NO_RULES = RuleFacts(False, 0, False, False)


def all_rules(api: Api, path: str) -> Optional[list]:
    """`rules/branches/<b>` の規則を全ページ読む。1 ページ目が 404 なら規則なし（空）、途中を読めなければ None。"""
    rules, why = api(path)
    if why == "missing":
        return []
    if not isinstance(rules, list):
        return None
    got, page = rules, 1
    while len(got) >= DEFAULT_PER_PAGE:
        page += 1
        got, _ = api(f"{path}?page={page}")
        if not isinstance(got, list):
            return None
        rules = rules + got
    return rules


def _ruleset_facts(rules: list) -> RuleFacts:
    if not rules:
        return NO_RULES
    kinds = {r.get("type") for r in rules if isinstance(r, dict)}
    approvals = [
        obj(r.get("parameters")).get("required_approving_review_count")
        for r in rules
        if isinstance(r, dict) and r.get("type") == "pull_request"
    ]
    up_to_date = any(
        isinstance(r, dict)
        and r.get("type") == "required_status_checks"
        and obj(r.get("parameters")).get("strict_required_status_checks_policy")
        for r in rules
    )
    count = max((a for a in approvals if isinstance(a, int)), default=0)
    return RuleFacts(True, count, "merge_queue" in kinds, bool(up_to_date))


def _classic_facts(api: Api, branch: str) -> Optional[RuleFacts]:
    """従来のブランチ保護の事実。保護が無ければ `present` が偽、保護があるのに詳細を読めなければ None。"""
    info, _ = api(f"repos/{{owner}}/{{repo}}/branches/{branch}")
    enabled = obj(obj(info).get("protection")).get("enabled")
    if enabled is False:
        return NO_RULES
    protection, _ = api(f"repos/{{owner}}/{{repo}}/branches/{branch}/protection")
    if not isinstance(protection, dict):
        return None
    count = obj(protection.get("required_pull_request_reviews")).get("required_approving_review_count")
    up_to_date = bool(obj(protection.get("required_status_checks")).get("strict"))
    return RuleFacts(True, count if isinstance(count, int) else 0, False, up_to_date)


def rule_facts(api: Api, branch: str) -> Optional[RuleFacts]:
    """本番ブランチの ruleset と従来のブランチ保護を重ねた事実（GitHub は両方を効かせるため厳しいほうを採る）。読めなければ None。"""
    rules = all_rules(api, f"repos/{{owner}}/{{repo}}/rules/branches/{branch}")
    classic = _classic_facts(api, branch) if rules is not None else None
    if rules is None or classic is None:
        return None
    a = _ruleset_facts(rules)
    if not a.present or not classic.present:
        return a if a.present else classic
    return RuleFacts(True, max(a.approvals, classic.approvals), a.queue or classic.queue, a.up_to_date or classic.up_to_date)
