"""PR に既にあるコメント（#3941）: 相手役へ既存の指摘を渡す・前の実行が残した未解決の指摘を引き継ぐ・終わったレビューを
回し直さない・長い設計文書を知らせる。"""

from __future__ import annotations

from pathlib import Path

from ndf.domains.crossreview import drive, prior_decide, report_decide, threads
from ndf.domains.crossreview import finish_decide as fin

from ._repos import BRANCH, Repos, commit, sh
from .test_crossreview_run import Fake, Recording, _run, _state

OPENING = threads.opening({"id": "r1-1", "by": "codex", "round": 1, "severity": "major", "text": "A が止まる"})


def test_others_drop_our_own_record_and_thread_openings() -> None:
    remarks = [
        prior_decide.Remark("comment", "alice", "ここが遅い"),
        prior_decide.Remark("comment", "ndf", report_decide.MARKER + "\n## cross-review の記録"),
        prior_decide.Remark("review_comment", "ndf", OPENING, "a.py", 3),
        prior_decide.Remark("review_comment", "ndf", "直した（修正のコミット abc）", "a.py", 3),
        prior_decide.Remark("review", "bot", ""),
    ]
    kept = prior_decide.others(remarks)
    assert [r.body for r in kept] == ["ここが遅い", "直した（修正のコミット abc）"]
    lines = prior_decide.remark_lines(kept, 5)
    assert lines == ["- alice（comment）: ここが遅い", "- ndf（review_comment・a.py:3）: 直した（修…"]


def test_carried_reads_our_open_threads_only() -> None:
    left = [
        prior_decide.Thread(77, OPENING, False, "a.py", 9),
        prior_decide.Thread(78, OPENING, True, "a.py", 9),
        prior_decide.Thread(79, "人の指摘", False, "a.py", 1),
        prior_decide.Thread(80, OPENING, False, None, None),
    ]
    got = prior_decide.carried(left, tracked={80})
    assert got == [prior_decide.Carried("t77", 77, "A が止まる", "codex", "major", "a.py", 9)]


def test_existing_comments_reach_every_round_prompt(wt: Path, tmp_path: Path, capsys, monkeypatch, hub) -> None:
    hub.port.existing = [
        prior_decide.Remark("comment", "alice", "セクション B の根拠が無い"),
        prior_decide.Remark("comment", "ndf", report_decide.MARKER + "\n記録"),
    ]
    fake = Recording(tmp_path / "st", {"codex": [["一つ目"], []], "kiro": [[], []]})
    _run(capsys, monkeypatch, fake, wt)
    reviews = [p for a, _, p in fake.prompts if a != "claude"]
    assert len(reviews) == 4 and all("- alice（comment）: セクション B の根拠が無い" in p for p in reviews)
    assert not any("記録" in p.split("PR に既に出ている指摘", 1)[-1] for p in reviews)
    # ラウンドごとに取り直す
    assert hub.port.reads == 2


def test_open_thread_left_by_another_run_is_carried_until_fixed(wt: Path, tmp_path: Path, capsys, monkeypatch, hub) -> None:
    """別の保存先で始めた実行は、前の実行が開いたまま残した自分の指摘を未解決として引き継ぎ、修正を通すまで収束させない。
    直したら、そのスレッドへ返信して resolve する（新しいスレッドは作らない）。"""
    hub.port.left = [prior_decide.Thread(77, OPENING, False, "doc.md", None)]
    fake = Recording(tmp_path / "st", {"codex": [[], []], "kiro": [[], []]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert (code, got["status"]) == (0, "ok")
    fix = next(p for a, _, p in fake.prompts if a == "claude")
    assert "- t77: A が止まる（major・doc.md）" in fix
    state = _state(fake.state_dir)
    assert [(r["round"], r["verdict"]) for r in state["rounds"]] == [(1, drive.CHANGES_REQUIRED), (2, fin.CONVERGED)]
    assert state["threads"]["t77"]["comment"] == 77
    assert hub.port.opened == [] and hub.port.resolved == [77]
    assert {"run": "start", "result": "PR に残った未解決の指摘 1 件を引き継いだ"} in got["process"]


def test_converged_review_on_the_same_head_is_not_run_again(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    state_dir = tmp_path / "st"
    _run(capsys, monkeypatch, Fake(state_dir, {"codex": [[]], "kiro": [[]]}), wt)
    again = Fake(state_dir, {"codex": [["出し直し"]], "kiro": [[]]})
    code, got = _run(capsys, monkeypatch, again, wt)
    assert (code, got["status"]) == (0, "ok")
    assert again.calls == []
    assert got["process"] == [{"run": "round 1", "result": "head が変わっていないので前回の結果（converged）を返した"}]
    assert len(_state(state_dir)["rounds"]) == 1


def test_converged_review_runs_again_after_a_new_commit(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    state_dir = tmp_path / "st"
    _run(capsys, monkeypatch, Fake(state_dir, {"codex": [[]], "kiro": [[]]}), wt)
    commit(repos.other, "more.md", "# 足した\n", "足す")
    sh(repos.other, "pull", "-q", "--rebase", "origin", BRANCH)
    sh(repos.other, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    again = Fake(state_dir, {"codex": [[]], "kiro": [[]]})
    _run(capsys, monkeypatch, again, wt)
    assert sorted(again.calls) == ["codex", "kiro"]


def test_long_design_document_is_a_warning(repos: Repos, tmp_path: Path, capsys, monkeypatch) -> None:
    from ndf.contracts import limits

    over = int(limits.value(drive.DOC_LINES, "count")) + 1
    commit(repos.other, "long.md", "\n".join(f"行 {i}" for i in range(over)), "長い")
    sh(repos.other, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    sh(repos.main, "fetch", "-q", "origin")
    wt = repos.add_worktree(tmp_path / "wt")
    fake = Fake(tmp_path / "st", {"codex": [["直す"], []], "kiro": [[], []]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert (code, got["status"]) == (0, "ok")
    warned = [w for w in got["warnings"] if "long.md" in w]
    assert len(warned) == 1 and f"{over} 行" in warned[0]
