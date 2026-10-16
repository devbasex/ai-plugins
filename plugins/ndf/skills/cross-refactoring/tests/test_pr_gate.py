"""入口の判定（`pr_gate.init_refusal`）の表（#1658 の設計「`refactor.py init` の止め方」）。git も GitHub も使わない。"""

from __future__ import annotations

import sys

import pytest


@pytest.fixture
def gate(refactor):
    return sys.modules["refactor_lib.pr_gate"]


@pytest.mark.parametrize(
    "state, merged_at, words",
    [
        ("merged", None, ["#7 の状態を判定できない（state: merged）", "gh api repos/acme/demo/pulls/7"]),
        (None, None, ["#7 の状態を判定できない（state: None）"]),
        ("closed", "2026-10-06T15:44:18Z", ["#7 はマージ済み"]),
        ("closed", None, ["#7 は閉じている"]),
        ("open", None, None),
    ],
)
def test_refusal_follows_the_table(gate, state, merged_at, words):
    reason = gate.init_refusal(7, "acme/demo", gate.PrStatus(state, merged_at))
    if words is None:
        assert reason is None
    else:
        assert reason and all(w in reason for w in words), reason
        assert "\n" not in reason


def test_status_reads_the_fields_as_they_are(gate):
    """`draft` は読まない。Draft でない Pull Request でも続ける（2026-10-07 の差し戻し）。"""
    body = {"state": "open", "draft": False, "merged_at": None, "number": 7}
    assert gate.PrStatus.of(body) == gate.PrStatus("open", None)
    assert gate.init_refusal(7, "acme/demo", gate.PrStatus.of(body)) is None
    assert gate.PrStatus.of({}) == gate.PrStatus(None, None)
