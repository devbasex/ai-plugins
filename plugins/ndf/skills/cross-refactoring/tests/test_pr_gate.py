"""入口の判定（`pr_gate.refusal`）の表（#1658 の設計「`refactor.py init` の止め方」）。git も GitHub も使わない。"""

from __future__ import annotations

import sys

import pytest


@pytest.fixture
def gate(refactor):
    return sys.modules["refactor_lib.pr_gate"]


@pytest.mark.parametrize(
    "state, draft, merged_at, resuming, words",
    [
        ("merged", True, None, False, ["#7 の状態を判定できない（state: merged）", "gh api repos/acme/demo/pulls/7"]),
        ("closed", False, "2026-10-06T15:44:18Z", False, ["#7 はマージ済み"]),
        ("closed", True, None, True, ["#7 は閉じている"]),
        ("open", "yes", None, True, None),
        ("open", None, None, False, ["#7 の状態を判定できない（draft: None）"]),
        ("open", False, None, False, ["#7 は Draft でない", "gh pr ready 7 --undo"]),
        ("open", True, None, False, None),
    ],
)
def test_refusal_follows_the_table(gate, state, draft, merged_at, resuming, words):
    reason = gate.refusal(7, "acme/demo", gate.PrStatus(state, draft, merged_at), resuming=resuming)
    if words is None:
        assert reason is None
    else:
        assert reason and all(w in reason for w in words), reason
        assert "\n" not in reason


def test_state_is_checked_before_draft(gate):
    """閉じた Draft でない Pull Request は「閉じている」で止まる（`draft` を先に見ると語が変わる）。"""
    reason = gate.refusal(7, "acme/demo", gate.PrStatus("closed", False, None), resuming=False)
    assert "閉じている" in reason and "Draft" not in reason.split("。")[0]


def test_status_reads_the_three_fields_as_they_are(gate):
    body = {"state": "open", "draft": True, "merged_at": None, "number": 7}
    assert gate.PrStatus.of(body) == gate.PrStatus("open", True, None)
    assert gate.PrStatus.of({}) == gate.PrStatus(None, None, None)
