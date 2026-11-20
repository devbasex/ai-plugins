"""指摘の基準と重要度（#3941）: 相手役と修正担当への基準、minor・nit を収束の妨げにしない、位置の構造、重点の設定。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ndf.contracts import facts
from ndf.domains.crossreview import criteria_decide, report_decide, store, threads
from ndf.domains.crossreview import finish_decide as fin

from ._repos import run_cli
from .test_crossreview_run import PR, Fake, Recording, _run, _state


def test_blocking_follows_the_severity_table_and_unknown_blocks() -> None:
    for row in facts.rows("severities"):
        assert criteria_decide.blocking(row["id"]) is row["blocking"]
    assert criteria_decide.blocking(criteria_decide.severity_of("重い")) is True
    assert criteria_decide.heavier("minor", "critical") == "critical"
    assert criteria_decide.heavier("critical", "nit") == "critical"


def test_focus_reads_version_1_only() -> None:
    assert criteria_decide.focus_of({"version": 1, "focus": [" 速さ "]}, "r") == ("速さ",)
    for raw in ({"version": 2, "focus": ["速さ"], "profile": {}}, {"version": 3, "profiles": []}, {"version": 1}, {"version": True}):
        with pytest.raises(criteria_decide.ReviewDeclInvalid):
            criteria_decide.focus_of(raw, "r")
    # 基準 3 は重点があるときだけ現れ、番号は詰めない
    assert [line.split(".")[0] for line in criteria_decide.criteria_lines(())] == ["1", "2", "4"]
    assert [line.split(".")[0] for line in criteria_decide.criteria_lines(("速さ",))] == ["1", "2", "3", "4"]


def test_light_findings_do_not_block_and_are_not_handed_to_the_fixer(wt: Path, tmp_path: Path, capsys, monkeypatch, hub) -> None:
    """minor・nit だけの指摘は収束を止めず、修正担当を起動しない。ジャーナルと記録のコメントには重要度つきで残り、スレッドにしない。"""
    nit = {"text": "語の揺れ", "severity": "nit", "path": "doc.md", "line": 3}
    fake = Recording(tmp_path / "st", {"codex": [[nit]], "kiro": [[]]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert (code, got["status"]) == (0, "ok")
    assert "claude" not in fake.calls
    assert "軽い指摘 1" in got["summary"]
    [row] = _state(fake.state_dir)["findings"]
    assert (row["severity"], row["outcome"], row["path"], row["line"]) == ("nit", fin.UNRESOLVED, "doc.md", 3)
    assert _state(fake.state_dir)["rounds"][-1]["verdict"] == fin.CONVERGED
    assert hub.port.opened == []
    assert f"| r1-1 | codex | nit | doc.md:3 | 語の揺れ | {report_decide.LIGHT_LABEL} | — |" in hub.comments[-1][2]


def test_blocking_findings_go_to_the_fixer_with_the_criteria(wt: Path, tmp_path: Path, capsys, monkeypatch, hub) -> None:
    """基準と重要度を相手役へ渡し、重い指摘だけを修正担当へ基準つきで渡す。スレッドは相手役が返した path・line に付ける。"""
    major = {"text": "止まる", "severity": "major", "path": "doc.md", "line": 5}
    fake = Recording(tmp_path / "st", {"codex": [[major, {"text": "好み", "severity": "minor"}], []], "kiro": [[], []]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert (code, got["status"]) == (0, "ok")
    review = next(p for a, _, p in fake.prompts if a == "codex")
    assert all(line in review for line in criteria_decide.reviewer_block(()))
    assert '"severity"' in review and '"path"' in review and '"line"' in review
    fix = next(p for a, _, p in fake.prompts if a == "claude")
    assert "- r1-1: 止まる（major・doc.md:5）" in fix and "r1-2" not in fix
    assert all(line in fix for line in criteria_decide.fixer_block(()))
    assert [(path, line) for path, line, _, _ in hub.port.opened] == [("doc.md", 5)]
    assert threads.location({"path": "doc.md", "line": 5, "section": "x.md#y"}) == ("doc.md", 5)


def test_review_json_focus_reaches_both_prompts(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    (wt / ".ndf").mkdir(exist_ok=True)
    (wt / ".ndf" / "review.json").write_text(json.dumps({"version": 1, "focus": ["処理の回数"]}), encoding="utf-8")
    fake = Recording(tmp_path / "st", {"codex": [[{"text": "回数が増える", "severity": "major"}], []], "kiro": [[], []]})
    _run(capsys, monkeypatch, fake, wt)
    [focus_line] = [line for line in criteria_decide.criteria_lines(("処理の回数",)) if line.startswith("3.")]
    assert all(focus_line in p for a, _, p in fake.prompts)


def test_review_json_with_profiles_is_invalid(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """version 2・3 のプロファイルは黙って無視せず invalid で止める（相手役を起動しない）。"""
    (wt / ".ndf").mkdir(exist_ok=True)
    (wt / ".ndf" / "review.json").write_text(json.dumps({"version": 3, "profiles": [{"name": "code"}]}), encoding="utf-8")
    fake = Recording(tmp_path / "st", {"codex": [[]], "kiro": [[]]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert got["cause"]["kind"] == "invalid" and "version 1" in got["cause"]["detail"]
    assert fake.calls == []


def test_finish_does_not_count_light_findings_as_gaps() -> None:
    findings = (fin.Finding("a", "揺れ", fin.UNRESOLVED, None, "nit"), fin.Finding("b", "止まる", fin.UNRESOLVED, None, None))
    got = fin.finish(fin.Review(fin.MAX_ROUNDS, 1, findings))
    assert [g["name"] for g in got.gaps] == ["b"]
    assert fin.as_findings([{"id": "a", "outcome": "unresolved", "severity": "nit"}])[0].severity == "nit"


def test_incomplete_reviewers_is_work_stopped() -> None:
    got = fin.finish(fin.Review(fin.INCOMPLETE_REVIEWERS, 1, ()))
    assert (got.status, got.kind) == ("stopped", "work_stopped")


def test_runtimes_allowed_narrows_the_reviewers(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """メインディレクトリの .ndf/runtimes.json の allowed の外の相手役は選ばず、名指せば invalid。形が違えば invalid。"""
    from ndf.core.converge import location
    from ndf.adapters import git
    from ndf.domains.crossreview import cli

    main = location.main_dir(git.Git(wt))
    (main / ".ndf").mkdir(exist_ok=True)
    policy = main / ".ndf" / "runtimes.json"
    policy.write_text(json.dumps({"allowed": ["claude", "kiro"]}), encoding="utf-8")
    base = ["run", "--pr", str(PR), "--state-dir", str(tmp_path / "st"), "--base", "origin/main", "--worktree", str(wt)]
    assert cli._run_config(cli._parser().parse_args(base)).reviewers == ("kiro",)
    code, got = run_cli(capsys, *base, "--reviewer", "codex")
    assert got["cause"]["kind"] == "invalid" and "codex" in got["cause"]["detail"]
    policy.write_text(json.dumps({"allowed": ["claude"], "review_seats": ["claude", "claude-2"]}), encoding="utf-8")
    code, got = run_cli(capsys, *base)
    assert got["cause"]["kind"] == "invalid"
    assert not store.state_pr(tmp_path / "st", PR).path.is_file()


def test_runtimes_allowed_without_the_fixer_stops_before_any_launch(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """PR 3944 の r1-4: 修正担当（claude）が allowed の外なら、どの担当も起動せず（学習の設定の確かめもせず）precondition で止まる。"""
    from ndf.adapters import git, training
    from ndf.core.converge import location
    from ndf.domains.crossreview import cli

    main = location.main_dir(git.Git(wt))
    (main / ".ndf").mkdir(exist_ok=True)
    (main / ".ndf" / "runtimes.json").write_text(json.dumps({"allowed": ["codex"]}), encoding="utf-8")
    checked: list[str] = []
    monkeypatch.setattr(training, "ensure", lambda runtime, env=None, now=None, read=None: checked.append(runtime))
    fake = Fake(tmp_path / "st", {"codex": [["直す"]]})
    monkeypatch.setattr(cli, "LAUNCH", fake)
    code, got = run_cli(capsys, "run", "--pr", str(PR), "--state-dir", str(fake.state_dir), "--base", "origin/main", "--worktree", str(wt))
    assert got["cause"]["kind"] == "precondition" and "claude" in got["cause"]["detail"]
    assert fake.calls == [] and checked == []
