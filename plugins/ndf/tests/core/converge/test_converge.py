"""cross 系で共通の部品: 相手役の割り当て・相手役の回答の読み取り・振動の判定。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ndf.adapters import agents
from ndf.contracts import facts
from ndf.core.converge import oscillation, participants

SANDBOX_OFF = "--dangerously-bypass-approvals-and-sandbox"


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
    spec = participants.review_launch(reviewer, tmp_path, "質問", tmp_path / "round1-x")
    assert spec.argv[0] == facts.value(participants.RUNTIMES, reviewer, "executable") and spec.argv[-1] == "質問"
    assert "{workdir}" not in " ".join(spec.argv) and spec.cwd == tmp_path


@pytest.mark.parametrize(("namespaces", "present", "absent"), [(True, ["-s", "read-only"], SANDBOX_OFF), (False, [SANDBOX_OFF], "-s")])
def test_issue_3893_codex_sandbox_follows_whether_user_namespaces_can_be_made(
    tmp_path: Path, monkeypatch, namespaces: bool, present: list[str], absent: str
) -> None:
    """#3893: 名前空間を作れない環境では、codex のサンドボックス（bubblewrap）の代わりに外の隔離に任せる引数で起動する。"""
    monkeypatch.setattr(agents, "user_namespaces", lambda: namespaces)
    argv = participants.review_launch("codex", tmp_path, "質問", tmp_path / "round1-codex").argv
    at = argv.index(present[0])
    assert list(argv[at : at + len(present)]) == present and absent not in argv
    assert "{sandbox}" not in argv and argv[-1] == "質問"


def test_findings_are_read_from_the_last_json_line_and_missing_is_not_empty() -> None:
    text = 'ログ {壊れた\n{"findings": [{"text": "a {b}"}]}\n'
    assert participants.findings_of(text) == [{"text": "a {b}"}]
    assert participants.findings_of('{"findings": []}') == []
    assert participants.findings_of("回答が無い") is None


def test_finding_key_ignores_spacing_and_case() -> None:
    assert oscillation.finding_key("  Foo   bar ") == oscillation.finding_key("foo bar")


def test_returned_is_a_b_a() -> None:
    a, b = frozenset({"a"}), frozenset({"b"})
    assert oscillation.returned([a, b, a]) == {"a"}
    assert oscillation.returned([a, a, a]) == set()
    assert oscillation.returned([a, b]) == set()
