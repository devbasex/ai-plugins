"""`ndf project branch-strategy`（解析のサブコマンド）の単体テスト: GitHub の保護規則を読む（#3672 のテスト設計の 9・10・11。#3718）。

入力と期待値は 10 系の `plugins/ndf/scripts/tests/test_branch_strategy.py` の同じ観点のテストと同じにする。
5 種の設定は `tests/fixtures/branch_strategy/` の 1 か所にあり、10 系のテストも同じ fixture を読む。
"""

from __future__ import annotations

from ndf.domains.project import strategy_rules

from ._branch_strategy import fake_api

RF = strategy_rules.RuleFacts


def test_rule_facts_merge_rulesets_and_classic_protection():
    rules = [{"type": "merge_queue"}, {"type": "pull_request", "parameters": {"required_approving_review_count": 1}}]
    assert strategy_rules.rule_facts(fake_api([], rules=rules), "main") == RF(True, 1, True, False)
    protection = {"required_pull_request_reviews": {"required_approving_review_count": 2}, "required_status_checks": {"strict": True}}
    assert strategy_rules.rule_facts(fake_api([], rules=[], protection=protection), "main") == RF(True, 2, False, True)
    assert strategy_rules.rule_facts(fake_api([]), "main") == RF(False, 0, False, False)
    assert strategy_rules.rule_facts(lambda p: (None, "boom"), "main") is None


# --- 現状固定テスト（I-011: rule_facts） ---------------------------------------------------------

RULES_PATH = "repos/{owner}/{repo}/rules/branches/main"
BRANCH_PATH = "repos/{owner}/{repo}/branches/main"
PROTECTION_PATH = "repos/{owner}/{repo}/branches/main/protection"


def table_api(table: dict):
    """パス → (値, 理由) の表で回答し、読んだパスを順に残す。表に無いパスは (None, "unknown")。"""
    calls: list[str] = []

    def api(path):
        calls.append(path)
        return table.get(path, (None, "unknown"))

    return api, calls


def test_rule_facts_reads_rules_then_branch_then_protection():
    api, calls = table_api({
        RULES_PATH: ([], ""), BRANCH_PATH: ({"protection": {"enabled": True}}, ""), PROTECTION_PATH: ({}, ""),
    })  # fmt: skip
    assert strategy_rules.rule_facts(api, "main") == RF(True, 0, False, False)
    assert calls == [RULES_PATH, BRANCH_PATH, PROTECTION_PATH]


def test_rule_facts_unreadable_rules_skip_the_classic_protection():
    api, calls = table_api({RULES_PATH: ({"message": "x"}, "")})
    assert strategy_rules.rule_facts(api, "main") is None
    assert calls == [RULES_PATH]


def test_rule_facts_missing_rules_and_disabled_protection_are_no_rules():
    api, calls = table_api({RULES_PATH: (None, "missing"), BRANCH_PATH: ({"protection": {"enabled": False}}, "")})
    assert strategy_rules.rule_facts(api, "main") == strategy_rules.NO_RULES
    assert calls == [RULES_PATH, BRANCH_PATH]


def test_rule_facts_unknown_protection_state_reads_the_detail():
    # 保護の有無を読めない（enabled が偽でない）ときは詳細を読み、読めなければ None
    api, calls = table_api({RULES_PATH: ([], "")})
    assert strategy_rules.rule_facts(api, "main") is None
    assert calls == [RULES_PATH, BRANCH_PATH, PROTECTION_PATH]


def test_rule_facts_reads_every_page_of_rules():
    page1 = [{"type": "deletion"}] * strategy_rules.DEFAULT_PER_PAGE
    page2 = [{"type": "pull_request", "parameters": {"required_approving_review_count": 3}}]
    api, calls = table_api({
        RULES_PATH: (page1, ""), f"{RULES_PATH}?page=2": (page2, ""),
        BRANCH_PATH: ({"protection": {"enabled": False}}, ""),
    })  # fmt: skip
    assert strategy_rules.rule_facts(api, "main") == RF(True, 3, False, False)
    assert calls == [RULES_PATH, f"{RULES_PATH}?page=2", BRANCH_PATH]


def test_rule_facts_full_second_page_reads_a_third():
    full = [{"type": "deletion"}] * strategy_rules.DEFAULT_PER_PAGE
    api, calls = table_api({
        RULES_PATH: (full, ""), f"{RULES_PATH}?page=2": (full, ""), f"{RULES_PATH}?page=3": ([], ""),
        BRANCH_PATH: ({"protection": {"enabled": False}}, ""),
    })  # fmt: skip
    assert strategy_rules.rule_facts(api, "main") == RF(True, 0, False, False)
    assert calls[:3] == [RULES_PATH, f"{RULES_PATH}?page=2", f"{RULES_PATH}?page=3"]


def test_rule_facts_unreadable_later_page_is_none():
    full = [{"type": "deletion"}] * strategy_rules.DEFAULT_PER_PAGE
    api, calls = table_api({RULES_PATH: (full, "")})
    assert strategy_rules.rule_facts(api, "main") is None
    assert calls == [RULES_PATH, f"{RULES_PATH}?page=2"]


def test_rule_facts_ruleset_details():
    rules = [
        "not-a-dict",
        {"type": "pull_request", "parameters": {"required_approving_review_count": "2"}},
        {"type": "pull_request", "parameters": {"required_approving_review_count": 1}},
        {"type": "pull_request"},
        {"type": "required_status_checks", "parameters": {"strict_required_status_checks_policy": True}},
    ]
    api, _ = table_api({RULES_PATH: (rules, ""), BRANCH_PATH: ({"protection": {"enabled": False}}, "")})
    assert strategy_rules.rule_facts(api, "main") == RF(True, 1, False, True)


def test_rule_facts_ruleset_without_approvals_counts_zero():
    rules = [{"type": "required_status_checks", "parameters": {"strict_required_status_checks_policy": False}}]
    api, _ = table_api({RULES_PATH: (rules, ""), BRANCH_PATH: ({"protection": {"enabled": False}}, "")})
    assert strategy_rules.rule_facts(api, "main") == RF(True, 0, False, False)


def test_rule_facts_classic_non_int_count_is_zero():
    protection = {"required_pull_request_reviews": {"required_approving_review_count": None}, "required_status_checks": None}
    api, _ = table_api({RULES_PATH: (None, "missing"), BRANCH_PATH: ({}, ""), PROTECTION_PATH: (protection, "")})
    assert strategy_rules.rule_facts(api, "main") == RF(True, 0, False, False)


def test_rule_facts_both_present_take_the_stricter_of_each():
    rules = [{"type": "merge_queue"}, {"type": "pull_request", "parameters": {"required_approving_review_count": 1}}]
    protection = {"required_pull_request_reviews": {"required_approving_review_count": 2}, "required_status_checks": {"strict": True}}
    api, _ = table_api({
        RULES_PATH: (rules, ""), BRANCH_PATH: ({"protection": {"enabled": True}}, ""), PROTECTION_PATH: (protection, ""),
    })  # fmt: skip
    assert strategy_rules.rule_facts(api, "main") == RF(True, 2, True, True)
    rules = [{"type": "pull_request", "parameters": {"required_approving_review_count": 4}},
             {"type": "required_status_checks", "parameters": {"strict_required_status_checks_policy": True}}]  # fmt: skip
    api, _ = table_api({
        RULES_PATH: (rules, ""), BRANCH_PATH: ({"protection": {"enabled": True}}, ""), PROTECTION_PATH: ({}, ""),
    })  # fmt: skip
    assert strategy_rules.rule_facts(api, "main") == RF(True, 4, False, True)


def test_rule_facts_uses_the_branch_name_in_every_path():
    rules_path, branch_path = "repos/{owner}/{repo}/rules/branches/dev/x", "repos/{owner}/{repo}/branches/dev/x"
    api, calls = table_api({rules_path: (None, "missing"), branch_path: ({"protection": {"enabled": False}}, "")})
    assert strategy_rules.rule_facts(api, "dev/x") == strategy_rules.NO_RULES
    assert calls == [rules_path, branch_path]
