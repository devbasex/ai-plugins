"""文書の基準（#4285）: 文書を含む PR の指示にだけ基準の本文が入ること、2 ラウンド目の限定、読めない基準で止まること。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ndf import contracts
from ndf.contracts import writing
from ndf.domains.crossreview import criteria_decide, drive, store
from ndf.domains.crossreview import finish_decide as fin

from ._repos import BRANCH, Repos, commit, sh
from .test_crossreview_run import PR, Recording, _run, _state

IMPACT = "承認する人が、何を承認するのかを取り違える"
MAJOR = {"text": "「語の決まり」が読み取れない", "severity": "major", "impact": IMPACT, "path": "doc.md", "line": 5}


@pytest.fixture
def code_wt(repos: Repos, tmp_path: Path) -> Path:
    """Markdown を変えない PR のブランチの worktree。"""
    commit(repos.other, "code.py", "x = 1\n", "コード")
    sh(repos.other, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    sh(repos.main, "fetch", "-q", "origin")
    return repos.add_worktree(tmp_path / "code")


def _limit(head: str, diff: Path) -> str:
    return criteria_decide._table()["document"]["since"].format(since=head, diff=diff)


def _prompt(fake: Recording, name: str) -> str:
    return next(p for _, out, p in fake.prompts if out == name)


def test_issue_4285_prompts_of_a_pr_without_documents_are_unchanged(code_wt: Path, tmp_path: Path, capsys, monkeypatch, hub) -> None:
    """受け入れ条件 11: Markdown の無い PR の 2 つの指示は、前置き・指摘の基準・回答の形だけからなる（退行テスト）。"""
    finding = {"text": "止まる", "severity": "major", "impact": "保存が止まる"}
    fake = Recording(tmp_path / "st", {"codex": [[finding], []], "kiro": [[], []]})
    _run(capsys, monkeypatch, fake, code_wt)
    reviewer = [*criteria_decide.reviewer_block(()), drive.FINDINGS_SHAPE % criteria_decide.mvv_shape(None)]
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
    assert ".md" not in fake.inputs["round2.diff"]
    assert writing.criteria().rstrip() in _prompt(fake, "round2-codex.out")


def test_issue_4285_major_is_limited_from_the_second_round(wt: Path, tmp_path: Path, capsys, monkeypatch, hub) -> None:
    """受け入れ条件 10: major を限る文は 2 ラウンド目のレビューの指示にだけ入り、前のラウンドの head を示す。"""
    fake = Recording(tmp_path / "st", {"codex": [[MAJOR], []], "kiro": [[], []]})
    _run(capsys, monkeypatch, fake, wt)
    head = _state(fake.state_dir)["rounds"][0]["head"]
    limit = _limit(head, fake.input_dir / "round2.diff")
    assert limit in _prompt(fake, "round2-codex.out") and limit in _prompt(fake, "round2-kiro.out")
    assert all(limit not in p for _, out, p in fake.prompts if not out.startswith("round2-"))
    # 渡した差分が前の head からのものなら、同じファイルを指す（差分を作り直さない）
    assert "round2-since.diff" not in fake.inputs


def _resume(capsys, monkeypatch, state_dir: Path, wt: Path, *extra: str) -> Recording:
    """次の起動（どの相手役も指摘しない）の記録。"""
    fake = Recording(state_dir, {"codex": [[]], "kiro": [[]]})
    _run(capsys, monkeypatch, fake, wt, *extra)
    return fake


def _first_round(capsys, monkeypatch, state_dir: Path, wt: Path) -> dict:
    """major を 1 件直して 1 ラウンドで止めた後の状態。"""
    fake = Recording(state_dir, {"codex": [[MAJOR]], "kiro": [[]]})
    _run(capsys, monkeypatch, fake, wt, "--max-rounds", "1")
    return _state(state_dir)


def test_issue_4285_an_added_reviewer_is_not_limited(wt: Path, tmp_path: Path, capsys, monkeypatch, hub) -> None:
    """前のラウンドを読んでいないレビュアーを足した再開では、全差分を渡し、major の範囲を限らない。"""
    state_dir = tmp_path / "st"
    data = _first_round(capsys, monkeypatch, state_dir, wt)
    data["rounds"][0]["reviewers"] = ["codex"]
    store.write_object(store.state_pr(state_dir, PR).path, data)
    fake = _resume(capsys, monkeypatch, state_dir, wt)
    assert _state(state_dir)["rounds"][1]["scope"] == "full"
    assert data["rounds"][0]["head"] not in _prompt(fake, "round2-codex.out")
    assert data["rounds"][0]["head"] not in _prompt(fake, "round2-kiro.out")


def test_issue_4285_a_rewound_head_does_not_limit(wt: Path, tmp_path: Path, capsys, monkeypatch, hub) -> None:
    """前のラウンドの head が今の head の祖先でなくなったら、全差分を渡し、major の範囲を限らない。"""
    state_dir = tmp_path / "st"
    data = _first_round(capsys, monkeypatch, state_dir, wt)
    tree, base = (sh(wt, "rev-parse", r).strip() for r in ("HEAD^{tree}", "origin/main"))
    gone = sh(wt, "commit-tree", tree, "-p", base, "-m", "取り除いたコミット").strip()
    data["rounds"][0]["head"] = gone
    store.write_object(store.state_pr(state_dir, PR).path, data)
    fake = _resume(capsys, monkeypatch, state_dir, wt)
    assert _state(state_dir)["rounds"][1]["scope"] == "full"
    assert gone not in _prompt(fake, "round2-codex.out")


def test_issue_4285_a_full_round_shares_one_diff_since_the_last_head(wt: Path, tmp_path: Path, capsys, monkeypatch, hub) -> None:
    """設計の PR で全差分を見続けるラウンドは、前の head からの差分を 1 つ作り、どのレビュアーにも同じファイルを示す。"""
    state_dir = tmp_path / "st"
    data = _first_round(capsys, monkeypatch, state_dir, wt)
    data[store.REVIEWED] = []
    store.write_object(store.state_pr(state_dir, PR).path, data)
    fake = _resume(capsys, monkeypatch, state_dir, wt, "--purpose", "design")
    limit = _limit(data["rounds"][0]["head"], fake.input_dir / "round2-since.diff")
    assert limit in _prompt(fake, "round2-codex.out") and limit in _prompt(fake, "round2-kiro.out")
    since = fake.inputs["round2-since.diff"]
    assert "fix0.txt" in since and "doc.md" not in since
    assert "doc.md" in fake.inputs["round2.diff"]


def test_issue_4285_major_without_impact_becomes_minor_and_is_not_handed_over(wt: Path, tmp_path: Path, capsys, monkeypatch, hub) -> None:
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
    previous = criteria_decide.Previous("abc", Path("round2.diff"))
    assert criteria_decide.document_block(criteria_decide.REVIEWER, False, previous) == []
    assert criteria_decide.document_block(criteria_decide.FIXER, False) == []
    doc = criteria_decide._table()["document"]
    first = criteria_decide.document_block(criteria_decide.REVIEWER, True)
    second = criteria_decide.document_block(criteria_decide.REVIEWER, True, previous)
    fixer = criteria_decide.document_block(criteria_decide.FIXER, True)
    assert [line for line in second if line not in first] == [_limit("abc", Path("round2.diff"))]
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
