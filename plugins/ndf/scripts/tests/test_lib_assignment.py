"""担当決定の現状固定テスト。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ASSIGNMENT = Path(__file__).resolve().parents[1] / "lib" / "assignment.py"

EXPECTED = {
    "claude": [
        ("codex", ["agy", "kiro"]),
        ("agy", ["codex", "kiro"]),
        ("kiro", ["codex", "agy"]),
        ("claude", ["codex", "kiro"]),
        ("codex", ["agy", "kiro"]),
        ("agy", ["codex", "kiro"]),
        ("kiro", ["codex", "agy"]),
        ("claude", ["codex", "agy"]),
    ],
    "codex": [
        ("codex", ["agy", "kiro"]),
        ("agy", ["claude", "kiro"]),
        ("kiro", ["claude", "agy"]),
        ("claude", ["agy", "kiro"]),
        ("codex", ["claude", "kiro"]),
        ("agy", ["claude", "kiro"]),
        ("kiro", ["claude", "agy"]),
        ("claude", ["agy", "kiro"]),
    ],
    "agy": [
        ("codex", ["claude", "kiro"]),
        ("agy", ["codex", "kiro"]),
        ("kiro", ["claude", "codex"]),
        ("claude", ["codex", "kiro"]),
        ("codex", ["claude", "kiro"]),
        ("agy", ["claude", "kiro"]),
        ("kiro", ["claude", "codex"]),
        ("claude", ["codex", "kiro"]),
    ],
    "kiro": [
        ("codex", ["claude", "agy"]),
        ("agy", ["claude", "codex"]),
        ("kiro", ["codex", "agy"]),
        ("claude", ["codex", "agy"]),
        ("codex", ["claude", "agy"]),
        ("agy", ["claude", "codex"]),
        ("kiro", ["claude", "agy"]),
        ("claude", ["codex", "agy"]),
    ],
}


@pytest.fixture(scope="module")
def assignment():
    spec = importlib.util.spec_from_file_location("ndf_lib_assignment", ASSIGNMENT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_detect_host_prefers_an_explicit_host(assignment):
    assert assignment.detect_host(explicit="codex", env={"CLAUDECODE": "1"}) == (
        "codex", "explicit")


def test_detect_host_rejects_an_unknown_explicit_host(assignment):
    with pytest.raises(
        assignment.AssignmentError,
        match=(
            r"^--host には claude/codex/agy/kiro のいずれかを"
            r"指定してください: unknown$"
        ),
    ):
        assignment.detect_host(explicit="unknown", env={})


def test_detect_host_uses_the_first_environment_hint(assignment):
    assert assignment.detect_host(env={"CODEX_HOME": "x"}) == ("codex", "env")


def test_detect_host_rejects_an_environment_without_a_hint(assignment):
    with pytest.raises(
        assignment.AssignmentError,
        match=(
            r"^ホストを推定できませんでした。"
            r"`--host claude\|codex\|agy\|kiro` で明示してください$"
        ),
    ):
        assignment.detect_host(env={})


@pytest.mark.parametrize("host", EXPECTED)
def test_assign_keeps_the_eight_round_rotation(assignment, host):
    actual = [assignment.assign(round_no, host) for round_no in range(1, 9)]

    assert actual == EXPECTED[host]
    assert any(impl == host for impl, _ in actual)
    assert any(impl != host for impl, _ in actual)
    assert all(len(reviewers) == 2 for _, reviewers in actual)
    assert all(impl not in reviewers for impl, reviewers in actual)
