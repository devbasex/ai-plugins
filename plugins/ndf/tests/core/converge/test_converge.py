"""cross 系で共通の部品: 相手役の割り当て・相手役の回答の読み取り・振動の判定。"""

from __future__ import annotations

import json
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


@pytest.mark.parametrize(
    ("err", "line"),
    [
        ("bwrap: No permissions to create new namespace\n回答\n", "bwrap: No permissions to create new namespace"),
        # 相手役が標準エラーへ写した差分・文書の中の語は拾わない（PR 3944 の差分の文書で読めなかったと誤った）
        ("diff\n-   コマンドを打てなかった相手役（標準エラーの `bwrap:`）は読めなかった\n", None),
        ("", None),
    ],
)
def test_sandbox_failed_reads_only_lines_that_start_with_bwrap(tmp_path: Path, err: str, line) -> None:
    paths = agents.AgentPaths(tmp_path / "o", tmp_path / "e", tmp_path / "l", tmp_path / "p")
    paths.err.write_text(err, encoding="utf-8")
    assert agents.sandbox_failed(paths) == line


# ---------------------------------------------------------------- #3955: 席を決める・読むだけの claude


def test_issue_3955_assign_is_unchanged_for_cross_refactoring() -> None:
    """受け入れ条件 13: cross-refactoring の相手役の選び方（既定・名指し・allowed）は変えない。"""
    assert participants.assign() == ("codex", "kiro")
    assert participants.assign(("kiro", "kiro", "codex")) == ("kiro", "codex")
    assert participants.assign(allowed={"claude", "codex"}) == ("codex",)
    with pytest.raises(participants.UnknownParticipant):
        participants.assign(allowed={"claude"})
    with pytest.raises(participants.UnknownParticipant):
        participants.assign(("kiro",), allowed={"claude", "codex"})
    with pytest.raises(participants.UnknownParticipant):
        participants.assign(("claude",))
    with pytest.raises(participants.FixerNotAllowed):
        participants.assign(allowed={"codex"})


def test_issue_3955_read_only_launch_is_a_separate_minimal_claude(tmp_path: Path) -> None:
    """受け入れ条件 7・8: 修正担当の会話を持たない最小構成で、読む Tool だけを許す。読める場所を渡すと境界を足す。"""
    plain = participants.read_only_launch("sys", tmp_path, "読む", tmp_path / "s", 30.0)
    assert plain.agent == participants.FIXER and plain.argv[-1] == "読む"
    assert "--no-session-persistence" in plain.argv and "--resume" not in plain.argv and "--session-id" not in plain.argv
    assert plain.argv[plain.argv.index("--tools") + 1] == participants.READ_TOOLS == "Read,Grep,Glob"
    assert "--restricted" not in plain.argv
    place = tmp_path / "in"
    bound = participants.read_only_launch("sys", tmp_path, "読む", tmp_path / "s", 30.0, (place,))
    assert bound.argv[-3:] == ("--permission-mode", "dontAsk", "読む")
    assert "--restricted" in bound.argv and bound.argv[bound.argv.index("--add-dir") + 1] == str(place)
    assert "acceptEdits" not in bound.argv


def test_issue_3955_answer_text_is_the_outer_result_for_claude(tmp_path: Path) -> None:
    out = tmp_path / "o"
    out.write_text('{"result": "本文"}', encoding="utf-8")
    assert participants.answer_text("claude", out) == "本文"
    out.write_text("切れた {", encoding="utf-8")
    assert participants.answer_text("claude", out) == "切れた {"
    assert participants.answer_text("codex", out) == "切れた {"


def test_pr_4461_denied_reads_resolves_tool_paths_from_the_claude_workdir(tmp_path: Path) -> None:
    """PR 4461 の r2-3・r2-4: 1 回読んだ出力から本文と拒否を取り出し、Tool ごとのパスの引数だけを claude の作業ディレクトリから解決する。"""
    work, inputs = tmp_path / "wt", tmp_path / "in"
    diff, since = inputs / "round2.diff", inputs / "round2-since.diff"
    denials = [
        {"tool_name": "Read", "tool_input": {"file_path": "../in/round2.diff"}},
        {"tool_name": "Read", "tool_input": {"file_path": str(tmp_path / "other")}},
        {"tool_name": "Grep", "tool_input": {"pattern": str(since), "path": "src"}},
    ]
    out = tmp_path / "o"
    out.write_text(json.dumps({"result": "本文", "permission_denials": [*denials, "壊れた要素"]}), encoding="utf-8")
    answer = participants.claude_answer(out)
    assert answer.text == "本文" and list(answer.denials) == denials
    assert participants.denied_reads(answer.denials, (diff, since), work) == (diff,)
    glob = [{"tool_name": "Glob", "tool_input": {"path": str(inputs)}}]
    assert participants.denied_reads(glob, (diff, since), work) == (diff, since)


@pytest.mark.parametrize(
    ("requested", "allowed", "names", "added", "reason", "may_fill"),
    [
        ((), None, ("codex", "kiro"), (), None, True),
        ((), {"claude", "codex"}, ("codex", "claude"), ("claude",), "allowed の相手役 1（codex）", True),
        ((), {"claude"}, ("claude", "claude-2"), ("claude", "claude-2"), "allowed の相手役 0", True),
        (("codex", "codex"), None, ("codex",), (), None, False),
        (("codex", "kiro"), None, ("codex", "kiro"), (), None, True),
        (("codex",), {"claude", "codex"}, ("codex",), (), None, False),
    ],
)
def test_issue_3955_review_seats(requested, allowed, names, added, reason, may_fill) -> None:
    """受け入れ条件 5・6・12: 名指しが無ければ足りない席を claude・claude-2 で埋め、名指しは重なりを除いた数で 1 者かを決める。"""
    assert participants.review_seats(requested, allowed) == participants.Seats(names, added, reason, may_fill)


def test_issue_3955_review_seats_refuse_like_assign() -> None:
    with pytest.raises(participants.FixerNotAllowed):
        participants.review_seats((), {"codex"})
    with pytest.raises(participants.UnknownParticipant):
        participants.review_seats(("claude",), None)


def test_issue_3955_seat_names() -> None:
    assert [participants.runtime_of(s) for s in ("codex", "claude", "claude-2", "kiro")] == ["codex", "claude", "claude", "kiro"]
    assert participants.free_seat(()) == "claude"
    assert participants.free_seat(("codex", "claude")) == "claude-2"
    assert participants.free_seat(("claude", "claude-2")) == "claude-3"


@pytest.mark.parametrize(
    ("seats", "unread", "want"),
    [
        (("codex", "kiro"), (), ()),
        (("codex", "kiro"), ("kiro",), ("kiro",)),
        (("codex", "claude"), ("claude",), ()),
        (("codex", "claude"), ("codex", "claude"), ()),
        (("codex", "kiro"), ("codex", "kiro"), ()),
        (("codex", "claude"), ("codex",), ("codex",)),
    ],
)
def test_issue_3955_fill_targets(seats, unread, want) -> None:
    assert participants.fill_targets(seats, unread) == want


def test_issue_3955_refused_runtimes_check_each_runtime_once(monkeypatch) -> None:
    """受け入れ条件 15: 断られたランタイムと理由の組を返し、同じランタイムは 1 回だけ確かめる。unready は今の返り値のまま。"""
    from ndf.adapters import training

    asked: list[str] = []

    def ensure(runtime, env=None, now=None, read=None):
        asked.append(runtime)
        return "学習に使う設定" if runtime == "kiro" else None

    monkeypatch.setattr(training, "ensure", ensure)
    assert participants.refused_runtimes(("claude", "kiro", "claude", "codex")) == {"kiro": "学習に使う設定"}
    assert asked == ["claude", "kiro", "codex"]
    assert participants.unready(("kiro", "claude")) == ["学習に使う設定"]
    assert participants.unready(("codex",)) == []
