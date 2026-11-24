"""文書の基準（#4285）: 文書を含む PR の指示にだけ基準の本文が入ること、2 ラウンド目の限定、読めない基準で止まること。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ndf import contracts
from ndf.contracts import writing
from ndf.domains.crossreview import criteria_decide, drive
from ndf.domains.crossreview import finish_decide as fin

from ._repos import BRANCH, Repos, commit, sh
from .test_crossreview_run import Recording, _run, _state

IMPACT = "承認する人が、何を承認するのかを取り違える"
MAJOR = {"text": "「語の決まり」が読み取れない", "severity": "major", "impact": IMPACT, "path": "doc.md", "line": 5}


@pytest.fixture
def code_wt(repos: Repos, tmp_path: Path) -> Path:
    """Markdown を変えない PR のブランチの worktree。"""
    commit(repos.other, "code.py", "x = 1\n", "コード")
    sh(repos.other, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    sh(repos.main, "fetch", "-q", "origin")
    return repos.add_worktree(tmp_path / "code")


def _prompt(fake: Recording, name: str) -> str:
    return next(p for _, out, p in fake.prompts if out == name)


def test_issue_4285_prompts_of_a_pr_without_documents_are_unchanged(code_wt: Path, tmp_path: Path, capsys, monkeypatch, hub) -> None:
    """受け入れ条件 11: Markdown の無い PR の 2 つの指示は、前置き・指摘の基準・回答の形だけからなる（退行テスト）。"""
    finding = {"text": "止まる", "severity": "major", "impact": "保存が止まる"}
    fake = Recording(tmp_path / "st", {"codex": [[finding], []], "kiro": [[], []]})
    _run(capsys, monkeypatch, fake, code_wt)
    reviewer = [*criteria_decide.reviewer_block(()), drive.FINDINGS_SHAPE]
    for n in (1, 2):
        assert _prompt(fake, f"round{n}-codex.out").splitlines()[2:] == reviewer
    assert _prompt(fake, "round1-fix.out").splitlines()[2:-1] == criteria_decide.fixer_block(())


def test_issue_4285_document_pr_gives_the_criteria_to_both_prompts(wt: Path, tmp_path: Path, capsys, monkeypatch, hub) -> None:
    """受け入れ条件 7: Markdown を含む PR では、レビューする AI と修正担当への指示に基準の全文が入る。"""
    fake = Recording(tmp_path / "st", {"codex": [[MAJOR], []], "kiro": [[], []]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert (code, got["status"]) == (0, "ok")
    assert all(writing.criteria().rstrip() in p for _, _, p in fake.prompts)
    number = criteria_decide._table()["document"]["number"]
    assert all(f"\n{number}. " in p for _, _, p in fake.prompts)


def test_issue_4285_documents_are_read_from_the_whole_diff(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch, hub) -> None:
    """2 ラウンド目の差分に Markdown が無くても（修正は fix0.txt だけ）、PR の全差分にあれば基準を渡す。"""
    fake = Recording(tmp_path / "st", {"codex": [[MAJOR], []], "kiro": [[], []]})
    _run(capsys, monkeypatch, fake, wt)
    assert ".md" not in (fake.state_dir / "round2.diff").read_text(encoding="utf-8")
    assert writing.criteria().rstrip() in _prompt(fake, "round2-codex.out")


def test_issue_4285_major_is_limited_from_the_second_round(wt: Path, tmp_path: Path, capsys, monkeypatch, hub) -> None:
    """受け入れ条件 10: major を限る文は 2 ラウンド目のレビューの指示にだけ入り、前のラウンドの head を示す。"""
    fake = Recording(tmp_path / "st", {"codex": [[MAJOR], []], "kiro": [[], []]})
    _run(capsys, monkeypatch, fake, wt)
    head = _state(fake.state_dir)["rounds"][0]["head"]
    limit = criteria_decide._table()["document"]["since"].format(since=head)
    assert limit in _prompt(fake, "round2-codex.out") and limit in _prompt(fake, "round2-kiro.out")
    assert all(limit not in p for _, out, p in fake.prompts if not out.startswith("round2-"))
    assert f"git diff {head}...HEAD" in limit


def test_issue_4285_major_without_impact_becomes_minor_and_is_not_handed_over(
    wt: Path, tmp_path: Path, capsys, monkeypatch, hub
) -> None:
    """受け入れ条件 9: 起きることの 1 文の無い文章の指摘は minor になり、修正担当に渡らず、レビューは終わる。"""
    bare = {k: v for k, v in MAJOR.items() if k != "impact"}
    fake = Recording(tmp_path / "st", {"codex": [[bare]], "kiro": [[]]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert (code, got["status"]) == (0, "ok") and "claude" not in fake.calls
    [row] = _state(fake.state_dir)["findings"]
    assert (row["severity"], row["outcome"]) == ("minor", fin.UNRESOLVED)


def test_issue_4285_a_deferred_finding_is_not_left(wt: Path, tmp_path: Path, capsys, monkeypatch, hub) -> None:
    """受け入れ条件 9: 直す AI が理由を書いて「直さない」と決めた文章の指摘は、残っていないものとして終わる。"""
    fake = Recording(
        tmp_path / "st", {"codex": [[MAJOR], []], "kiro": [[], []]}, lambda fid: {"outcome": fin.SKIPPED, "reason": "用語集の語である"}
    )
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert (code, got["status"]) == (0, "ok")
    assert _state(fake.state_dir)["rounds"][-1]["verdict"] == fin.CONVERGED


def test_issue_4285_document_block_by_role_and_round() -> None:
    assert criteria_decide.document_block(criteria_decide.REVIEWER, False, "abc") == []
    assert criteria_decide.document_block(criteria_decide.FIXER, False) == []
    doc = criteria_decide._table()["document"]
    first = criteria_decide.document_block(criteria_decide.REVIEWER, True)
    second = criteria_decide.document_block(criteria_decide.REVIEWER, True, "abc")
    fixer = criteria_decide.document_block(criteria_decide.FIXER, True)
    assert [line for line in second if line not in first] == [doc["since"].format(since="abc")]
    assert set(doc["fields"]) <= set(first) and not set(doc["fields"]) & set(fixer)
    assert doc["fixer"] in fixer and doc["fixer"] not in first
    assert first[-1] == second[-1] == fixer[-1] == writing.criteria().rstrip()


def test_issue_4285_unreadable_criteria_stop_before_any_launch(wt: Path, tmp_path: Path, capsys, monkeypatch, hub) -> None:
    """基準を読めなければ、担当を起動する前に invalid で止まる。"""

    def broken() -> str:
        raise writing.CriteriaBroken("文書の基準を読めない")

    monkeypatch.setattr(writing, "criteria", broken)
    fake = Recording(tmp_path / "st", {"codex": [[]], "kiro": [[]]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert got["status"] == "stopped" and got["cause"]["kind"] == "invalid"
    assert fake.calls == []
    assert issubclass(writing.CriteriaBroken, contracts.ContractError)
