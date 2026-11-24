"""本番ブランチの ruleset と従来のブランチ保護の事実（`ndf.adapters.github_rules` と `GitHub.rule_facts`）。

githubkit へ `httpx.MockTransport` を渡し、ネットワークへ出ない。
"""

from __future__ import annotations

from typing import Any

import folded
import httpx
import pytest

from ndf.adapters.github_rules import NO_RULES, RuleFacts, merge_facts

from ._github_helpers import RouteServer, hub

REPO = "devbasex/ai-plugins"


# --- 本番ブランチの ruleset の事実（#3672 の I10。10 系の release_case.read_rule_facts と同じ値） -------------


def _rule_routes(rules: list, ruleset: dict | None, branch: dict, protection: httpx.Response) -> dict:
    routes = {
        ("GET", f"/repos/{REPO}/rules/branches/main"): [httpx.Response(200, json=rules)],
        ("GET", f"/repos/{REPO}/branches/main"): [httpx.Response(200, json=branch)],
        ("GET", f"/repos/{REPO}/branches/main/protection"): [protection],
    }
    if ruleset is not None:
        routes[("GET", f"/repos/{REPO}/rulesets/5")] = [httpx.Response(200, json=ruleset)]
    return routes


def _rule_facts_reads_rulesets_and_bypass() -> None:
    rules = [
        {"type": "pull_request", "ruleset_id": 5, "parameters": {"required_approving_review_count": 1}},
        {"type": "merge_queue", "ruleset_id": 5, "parameters": {"merge_method": "MERGE"}},
        {"type": "required_status_checks", "ruleset_id": 5, "parameters": {"strict_required_status_checks_policy": True}},
    ]
    server = RouteServer(
        _rule_routes(rules, {"current_user_can_bypass": "pull_requests_only"}, {"protection": {"enabled": False}}, httpx.Response(404))
    )
    got = hub(REPO, server).rule_facts("main")
    assert got == RuleFacts(True, 1, True, True, "pull_requests_only", "MERGE")


def _rule_facts_without_rules_or_protection_is_absent_and_unreadable_is_none() -> None:
    absent = RouteServer(_rule_routes([], None, {"protection": {"enabled": False}}, httpx.Response(404)))
    assert hub(REPO, absent).rule_facts("main") == NO_RULES
    # 従来の保護がある（か有無を読めない）のに詳細を読めなければ None（比べずに入れない）
    hidden = RouteServer(_rule_routes([], None, {"protection": {"enabled": True}}, httpx.Response(404, json={"message": "no"})))
    assert hub(REPO, hidden).rule_facts("main") is None


def _rule_facts_merges_classic_protection_with_the_stricter_values() -> None:
    protection = {"required_pull_request_reviews": {"required_approving_review_count": 2}, "enforce_admins": {"enabled": True}}
    server = RouteServer(_rule_routes([], None, {"protection": {"enabled": True}}, httpx.Response(200, json=protection)))
    assert hub(REPO, server).rule_facts("main") == RuleFacts(True, 2, False, False, "never")
    a = RuleFacts(True, 1, True, False, "always", "SQUASH")
    b = RuleFacts(True, 2, False, True, "exempt")
    assert merge_facts(a, b) == RuleFacts(True, 2, True, True, "exempt", "SQUASH")


# #3959 で足した場合を 1 つのテストの関数へ束ねる（テストの関数の数の予算。束ね方は tests/support/folded.py）
@pytest.mark.parametrize(
    "case",
    folded.cases(
        _rule_facts_reads_rulesets_and_bypass,
        _rule_facts_without_rules_or_protection_is_absent_and_unreadable_is_none,
        _rule_facts_merges_classic_protection_with_the_stricter_values,
    ),
)
def test_github_rules_cases(case: Any, request: pytest.FixtureRequest) -> None:
    folded.run(case, request)
