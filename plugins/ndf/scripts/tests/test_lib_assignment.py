"""担当決定の現状固定テスト。"""
from __future__ import annotations

import importlib.util
import sys
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
    # `@dataclass` は `sys.modules[cls.__module__]` を見るため、登録してから実行する
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("host", EXPECTED)
def test_assign_keeps_the_eight_round_rotation(assignment, host):
    actual = [assignment.assign(round_no, host) for round_no in range(1, 9)]

    assert actual == EXPECTED[host]
    assert any(impl == host for impl, _ in actual)
    assert any(impl != host for impl, _ in actual)
    assert all(len(reviewers) == 2 for _, reviewers in actual)
    assert all(impl not in reviewers for impl, reviewers in actual)


# ---------- 適用の輪番（#727。cross-refactoring が使う） ----------

def test_impl_assign_rotates_over_the_participants_starting_after_the_host(assignment):
    """AC34: `participants[round_no % len]`。ホスト claude の既定でも codex から始まる。"""
    participants = ["claude", "codex", "kiro"]
    actual = [assignment.impl_assign(r, participants) for r in range(1, 7)]
    assert actual == ["codex", "kiro", "claude", "codex", "kiro", "claude"]


def test_impl_assign_rejects_a_bad_round(assignment):
    with pytest.raises(assignment.AssignmentError):
        assignment.impl_assign(0, ["claude", "codex"])


def test_impl_assign_rejects_an_empty_list(assignment):
    with pytest.raises(assignment.AssignmentError):
        assignment.impl_assign(1, [])
