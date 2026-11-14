"""cross 系で共通の部品: 相手役の割り当て・相手役の答えの読み取り・振動の判定。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ndf.contracts import facts
from ndf.core.converge import oscillation, participants


def test_default_reviewers_are_seated_runtimes_with_a_read_only_launch_except_the_fixer() -> None:
    got = participants.assign()
    assert participants.FIXER not in got and got
    assert all(facts.value(participants.RUNTIMES, r, "default_seat") for r in got)


def test_named_reviewer_without_a_read_only_launch_is_refused() -> None:
    with pytest.raises(participants.UnknownParticipant):
        participants.assign(("agy",))
    with pytest.raises(participants.UnknownParticipant):
        participants.assign(("nobody",))


def test_review_launch_fills_the_worktree_and_puts_the_prompt_last(tmp_path: Path) -> None:
    reviewer = participants.assign()[0]
    spec = participants.review_launch(reviewer, tmp_path, "問い", tmp_path / "round1-x")
    assert spec.argv[0] == facts.value(participants.RUNTIMES, reviewer, "executable") and spec.argv[-1] == "問い"
    assert "{workdir}" not in " ".join(spec.argv) and spec.cwd == tmp_path


def test_findings_are_read_from_the_last_json_line_and_missing_is_not_empty() -> None:
    text = 'ログ {壊れた\n{"findings": [{"text": "a {b}"}]}\n'
    assert participants.findings_of(text) == [{"text": "a {b}"}]
    assert participants.findings_of('{"findings": []}') == []
    assert participants.findings_of("答えが無い") is None


def test_finding_key_ignores_spacing_and_case() -> None:
    assert oscillation.finding_key("  Foo   bar ") == oscillation.finding_key("foo bar")


def test_returned_is_a_b_a() -> None:
    a, b = frozenset({"a"}), frozenset({"b"})
    assert oscillation.returned([a, b, a]) == {"a"}
    assert oscillation.returned([a, a, a]) == set()
    assert oscillation.returned([a, b]) == set()
