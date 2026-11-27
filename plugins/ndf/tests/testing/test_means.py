"""テストを確かめる手段の判定（`ndf.testing.means`。#4566 の決定 1・2）。"""

from __future__ import annotations

import pytest

from ndf.testing import means

SUITE = {"name": "unit", "command": "pytest -q", "kind": "test"}


@pytest.mark.parametrize(
    ("project", "kind"),
    [
        # 手段の表の 4 行
        ({"ci": {"required_checks": ["test"]}, "test": {"ci": {"only": True}, "suites": [SUITE]}}, means.Kind.REQUIRED_CHECKS),
        ({"ci": {"required_checks": []}, "test": {"ci": {"only": True}, "suites": [SUITE]}}, means.Kind.NONE),
        ({"test": {"ci": {"only": False}, "suites": [SUITE]}}, means.Kind.LOCAL_TEST),
        ({"test": {"suites": [{"name": "lint", "command": "ruff check", "kind": "lint"}]}}, means.Kind.NONE),
    ],
)
def test_the_four_rows_of_the_means_table(project, kind):
    assert means.decide(project).kind == kind


def test_a_suite_without_kind_counts_as_a_test():
    got = means.decide({"test": {"suites": [{"name": "u", "command": "make test"}]}})
    assert got.kind == means.Kind.LOCAL_TEST
    assert got.suites == (("u", "make test"),)


def test_a_suite_without_a_command_is_not_a_local_test():
    assert means.decide({"test": {"suites": [{"name": "u", "scope_command": "pytest {paths}"}]}}).kind == means.Kind.NONE


def test_required_checks_keep_only_names():
    got = means.decide({"ci": {"required_checks": ["a", "", 3, "b"]}})
    assert (got.kind, got.required) == (means.Kind.REQUIRED_CHECKS, ("a", "b"))


@pytest.mark.parametrize("test", [{"unknown": "測れない"}, {"suites": "pytest"}, None, "x"])
def test_shapes_that_are_not_read_are_none(test):
    assert means.decide({"test": test}).kind == means.Kind.NONE


def test_the_local_means_carries_commands_declarations_and_protection():
    suite = {"name": "u", "command": "bash scripts/t.sh", "scope_command": "pytest {paths}", "paths": ["tests"]}
    got = means.decide({"test": {"suites": [suite, {"name": "l", "command": "ruff", "kind": "lint"}], "protected_files": ["a", 1]}})
    assert got.commands == ("bash scripts/t.sh", "pytest {paths}")
    assert got.declarations == (suite,)
    assert got.protected == ("a",)


def test_the_digest_changes_with_the_command_and_not_with_other_keys():
    one = means.decide({"test": {"suites": [SUITE]}}).digest
    assert one == means.decide({"test": {"suites": [{**SUITE, "runner": "pytest"}], "local_total_minutes": 5}}).digest
    assert one != means.decide({"test": {"suites": [{**SUITE, "command": "pytest -x"}]}}).digest


@pytest.mark.parametrize(("value", "want"), [(30, 30.0), (0.5, 0.5), (None, None), (0, None), (-1, None), ("30", None), (True, None)])
def test_total_minutes_is_a_positive_number(value, want):
    project = {"test": {"suites": []} | ({} if value is None else {"local_total_minutes": value})}
    assert means.total_minutes(project) == want
