"""規則 `const-fact` の、上限の設定の数との突き合わせ（`limit-num`・`stale`）。#3667 の受け入れ条件 5。"""

from __future__ import annotations

import json

import pytest

from ndf.checks import facts
from ndf.checks.model import CheckError

POLICIES = {
    "a.run": {"seconds": 1800},
    "a.retry": {"attempts": 4, "seconds": 7},
    "a.keep": {"count": 14},
    "procs.stop-tree": {"interval": 0.1},
    "procs.stop-test": {"interval": 0.1},
}
POLL = {"name": "ndf.a.POLL_INTERVAL", "kind": "implementation", "reason": "条件を見直す間隔"}


@pytest.fixture
def found(plugin, rules):
    """上限の設定と定数を書いた plugin の、指摘の種類と定数の名前の文。"""

    def run(code: str, allowed=()) -> list[str]:
        rows = {name: {"summary": "s", "read_by": ["ndf.a"], **row} for name, row in POLICIES.items()}
        plugin.write("contracts/limits.json", json.dumps({"owner": "o", "policies": rows}))
        plugin.write("src/ndf/a.py", code)
        rules["facts"]["limit_constants"] = list(allowed)
        return [f.detail.partition("（")[0] for f in facts.check(plugin.scope(), rules) if f.rule == "const-fact"]

    return run


def copied(name: str) -> list[str]:
    return [f"limit-num: {name} が上限の設定の数を写す"]


@pytest.mark.parametrize(
    "code",
    ["X_TIMEOUT = 1800\n", "X_TIMEOUT: float = 1800.0\n", "MAX_ATTEMPTS = 4\n", "KEEP_DAYS = 14\n", "_ROWS_LIMIT = 14\n"],
)
def test_a_constant_named_as_a_limit_with_the_same_number_is_a_finding(found, code):
    name = code.split(" ")[0].rstrip(":")
    assert found(code) == copied(name)


@pytest.mark.parametrize(
    "code",
    [
        "ROWS = 14\n",  # 名前が上限・回数・待ちを表さない
        "X_TIMEOUT = 1801\n",  # 同じ数が無い
        "MAX_ATTEMPTS = 7\n",  # 語に対応しないキーだけが同じ値
        "X_TIMEOUT = 14\n",
        "KEEP = True\n",
        'X_TIMEOUT = "1800"\n',
    ],
)
def test_other_constants_are_not_findings(found, code):
    assert found(code) == []


def test_a_constant_kept_with_a_reason_is_not_a_finding(found):
    code = "POLL_INTERVAL = 0.1\n"
    assert found(code) == copied("POLL_INTERVAL")
    assert found(code, [POLL]) == []


def test_the_finding_names_the_policies(plugin, rules, found):
    found("POLL_INTERVAL = 0.1\n")
    [finding] = facts.check(plugin.scope(), rules)
    assert "procs.stop-tree.interval" in finding.detail and "procs.stop-test.interval" in finding.detail


@pytest.mark.parametrize("code", ["OTHER = 1\n", "POLL_INTERVAL = 0.3\n"])
def test_a_kept_constant_that_is_gone_or_no_longer_hits_is_stale(plugin, rules, found, code):
    assert found(code, [POLL])[0].startswith("stale: facts.limit_constants の ndf.a.POLL_INTERVAL ")
    [finding] = facts.check(plugin.scope(), rules)
    assert finding.path.endswith("src/ndf/checks/rules.json")


@pytest.mark.parametrize("change", [{"reason": " "}, {"kind": "policy"}, {"why": "x"}])
def test_a_kept_constant_without_a_reason_or_a_known_kind_stops_the_check(found, change):
    with pytest.raises(CheckError, match="limit_constants"):
        found("POLL_INTERVAL = 0.1\n", [{**POLL, **change}])


def test_broken_limits_stop_the_check_and_a_plugin_without_limits_is_skipped(plugin, rules):
    plugin.write("src/ndf/a.py", "X_TIMEOUT = 1800\n")
    assert facts.check(plugin.scope(), rules) == []
    plugin.write("contracts/limits.json", "{")
    with pytest.raises(CheckError, match="上限の設定を読めない"):
        facts.check(plugin.scope(), rules)
    plugin.write("contracts/limits.json", json.dumps({"owner": "o", "policies": {"a.b": {"summary": "s"}}}))
    with pytest.raises(CheckError, match="read_by"):
        facts.check(plugin.scope(), rules)
