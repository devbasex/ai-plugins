"""指摘の基準と重要度（#3941）: 相手役と修正担当への基準、minor・nit を収束の妨げにしない、位置の構造、重点の設定。"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from ndf.contracts import facts
from ndf.domains.crossreview import criteria_decide, report_decide, store, threads
from ndf.domains.crossreview import finish_decide as fin

from ._repos import Repos, run_cli
from .test_crossreview_run import PR, Fake, Recording, _run, _state


def test_blocking_follows_the_severity_table_and_unknown_blocks() -> None:
    for row in facts.rows("severities"):
        assert criteria_decide.blocking(row["id"]) is row["blocking"]
    assert criteria_decide.blocking(criteria_decide.severity_of("重い")) is True
    assert criteria_decide.heavier("minor", "critical") == "critical"
    assert criteria_decide.heavier("critical", "nit") == "critical"


IMPACT = "保存が止まり、利用者の入力が消える"


def test_issue_4014_effective_severity_downgrades_heavy_findings_without_impact() -> None:
    """#4014: 1 文の無い critical・major と重要度の無い指摘は minor、1 文のある重要度の無い指摘は major。minor・nit はそのまま。"""
    expected = {
        ("critical", True): "critical",
        ("critical", False): "minor",
        ("major", True): "major",
        ("major", False): "minor",
        ("minor", True): "minor",
        ("minor", False): "minor",
        ("nit", True): "nit",
        ("nit", False): "nit",
        (None, True): "major",
        (None, False): "minor",
    }
    for (severity, has), want in expected.items():
        assert criteria_decide.effective_severity(severity, IMPACT if has else None) == want, (severity, has)
    # 境界: 空文字・空白だけは 1 文を持たない、前後に空白のある 1 文は持つ
    assert criteria_decide.effective_severity("major", "") == "minor"
    assert criteria_decide.effective_severity("major", "  \n") == "minor"
    assert criteria_decide.effective_severity("major", f"  {IMPACT} ") == "major"
    # 異常: 文字列でない 1 文は 1 文を持たない指摘として読む
    for bad in (3, ["止まる"], None):
        assert criteria_decide.effective_severity("critical", bad) == "minor"
        assert criteria_decide.effective_severity(None, bad) == "minor"


def test_issue_4014_downgraded_reads_raised_severity() -> None:
    """#4014: 下げた行は raised_severity が重い重さか null で severity が minor。項目の無い行は下げていない。"""
    assert criteria_decide.downgraded({"severity": "minor", "raised_severity": "major"}) is True
    assert criteria_decide.downgraded({"severity": "minor", "raised_severity": "critical"}) is True
    assert criteria_decide.downgraded({"severity": "minor", "raised_severity": None}) is True
    # 尺度に無い raised_severity は null と同じ
    assert criteria_decide.downgraded({"severity": "minor", "raised_severity": "重い"}) is True
    assert criteria_decide.downgraded({"severity": "major", "raised_severity": "major", "impact": IMPACT}) is False
    assert criteria_decide.downgraded({"severity": "minor", "raised_severity": "minor"}) is False
    assert criteria_decide.downgraded({"severity": "nit", "raised_severity": "nit"}) is False
    # 引き継いだ行・前のバージョンの行（raised_severity が無い）
    assert criteria_decide.downgraded({"severity": "minor"}) is False
    assert criteria_decide.downgraded({}) is False


def test_focus_reads_version_1_only() -> None:
    assert criteria_decide.focus_of({"version": 1, "focus": [" 速さ "]}, "r") == ("速さ",)
    for raw in ({"version": 2, "focus": ["速さ"], "profile": {}}, {"version": 3, "profiles": []}, {"version": 1}, {"version": True}):
        with pytest.raises(criteria_decide.ReviewDeclInvalid):
            criteria_decide.focus_of(raw, "r")
    # 基準 3 は重点があるときだけ現れ、番号は詰めない
    assert [line.split(".")[0] for line in criteria_decide.criteria_lines(())] == ["1", "2", "4"]
    assert [line.split(".")[0] for line in criteria_decide.criteria_lines(("速さ",))] == ["1", "2", "3", "4"]


def test_issue_4013_fixer_sessions_reads_a_boolean_and_defaults_to_false() -> None:
    """#4013: `fixer_sessions` は真偽値だけを読み、キーが無ければ偽。"""
    assert criteria_decide.fixer_sessions_of({"version": 1, "focus": []}, "r") is False
    assert criteria_decide.fixer_sessions_of({"version": 1, "focus": [], "fixer_sessions": False}, "r") is False
    assert criteria_decide.fixer_sessions_of({"version": 1, "focus": [], "fixer_sessions": True}, "r") is True


@pytest.mark.parametrize("value", ["true", "false", 1, 0, None])
def test_issue_4013_fixer_sessions_that_is_not_a_boolean_is_invalid(value: object) -> None:
    """#4013: 真偽値でない `fixer_sessions` は黙って保存する側にもしない側にも倒さず `ReviewDeclInvalid`。"""
    with pytest.raises(criteria_decide.ReviewDeclInvalid, match="fixer_sessions"):
        criteria_decide.fixer_sessions_of({"version": 1, "focus": [], "fixer_sessions": value}, "r")


def test_issue_4014_reviewer_block_asks_for_the_impact_from_the_data(monkeypatch) -> None:
    """#4014 の決定 5: 重要度の行は critical・major に起きることの 1 文を求め（基準 2・3・4 の言い換えつき）、書けない指摘を
    minor にさせる。「基準に当たる指摘は」の文を持たず、行の文は review-criteria.json の impact から組む。"""
    for focus in ((), ("速さ",)):
        block = criteria_decide.reviewer_block(focus)
        [line] = [x for x in block if x.startswith("重要度 severity は")]
        assert "impact" in line and "critical・major" in line and "minor" in line
        assert all(w in line for w in ("漏れる", "戻せなくなる", "単位", "作り違える"))
        assert not any("基準に当たる指摘は" in x for x in block)
    assert criteria_decide.reviewer_block(())[-1] == criteria_decide.reviewer_block(("速さ",))[-1]
    table = dict(criteria_decide._table())
    monkeypatch.setattr(
        criteria_decide, "_table", lambda: table | {"impact": {**table["impact"], "reviewer": "差し替えた {blocking} と {light}"}}
    )
    assert criteria_decide.reviewer_block(())[-1].endswith("差し替えた critical・major と minor・nit")


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
    major = {"text": "止まる", "severity": "major", "impact": IMPACT, "path": "doc.md", "line": 5}
    fake = Recording(tmp_path / "st", {"codex": [[major, {"text": "好み", "severity": "minor"}], []], "kiro": [[], []]})
    code, got = _run(capsys, monkeypatch, fake, wt)
    assert (code, got["status"]) == (0, "ok")
    review = next(p for a, _, p in fake.prompts if a == "codex")
    assert all(line in review for line in criteria_decide.reviewer_block(()))
    assert '"severity"' in review and '"impact"' in review and '"path"' in review and '"line"' in review
    fix = next(p for a, _, p in fake.prompts if a == "claude")
    assert f"- r1-1: 止まる（major・doc.md:5）— 起きること: {IMPACT}" in fix and "r1-2" not in fix
    assert all(line in fix for line in criteria_decide.fixer_block(()))
    assert [(path, line) for path, line, _, _ in hub.port.opened] == [("doc.md", 5)]
    assert threads.location({"path": "doc.md", "line": 5, "section": "x.md#y"}) == ("doc.md", 5)


def _fixer_prompt_with(tmp_path: Path, capsys, monkeypatch, wt: Path, project: dict | None) -> list[str]:
    """修正担当の worktree に設定 `project`（None なら置かない）を置いて 1 ラウンド回し、修正担当への指示の行を返す。"""
    if project is not None:
        (wt / ".ndf").mkdir(exist_ok=True)
        (wt / ".ndf" / "project.json").write_text(json.dumps(project), encoding="utf-8")
    major = {"text": "止まる", "severity": "major", "impact": IMPACT}
    fake = Recording(tmp_path / "st", {"codex": [[major], []], "kiro": [[], []]})
    _run(capsys, monkeypatch, fake, wt)
    return next(p for a, _, p in fake.prompts if a == "claude").splitlines()


def _between_findings_and_result(lines: list[str]) -> list[str]:
    """指摘の行（`- r<ラウンド>-<番号>: `）の後から結果ファイルの行（最後の行）の前まで。"""
    start = max(i for i, line in enumerate(lines) if re.match(r"- r\d+-\d+: ", line)) + 1
    return lines[start:-1]


def _fixer_lines() -> list[str]:
    """文書を変えた PR の修正担当への基準の行（`wt` は doc.md を変える）。"""
    block = criteria_decide.fixer_block(()) + criteria_decide.document_block(criteria_decide.FIXER, True)
    return "\n".join(block).splitlines()


def test_issue_4030_ci_only_worktree_tells_the_fixer_not_to_run_tests(wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """#4030: 修正担当の worktree の設定が `test.ci.only: true` なら、指摘の基準の後にデータの文が 1 行だけ足される。"""
    lines = _fixer_prompt_with(tmp_path, capsys, monkeypatch, wt, {"test": {"ci": {"only": True}}})
    assert _between_findings_and_result(lines) == [*_fixer_lines(), criteria_decide._table()["fixer_ci_only"]]


@pytest.mark.parametrize("project", [None, {}, {"test": {}}, {"test": {"ci": {"only": False}}}])
def test_issue_4030_without_ci_only_the_fixer_prompt_is_unchanged(
    project: dict | None, wt: Path, tmp_path: Path, capsys, monkeypatch
) -> None:
    """#4030: 設定・test のセクションが無いか `ci.only` が偽なら、その文は載らず、指摘の基準のほかに行は足されない。"""
    lines = _fixer_prompt_with(tmp_path, capsys, monkeypatch, wt, project)
    assert _between_findings_and_result(lines) == _fixer_lines()


def test_review_json_focus_reaches_both_prompts(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    (repos.main / ".ndf").mkdir(exist_ok=True)
    (repos.main / ".ndf" / "review.json").write_text(json.dumps({"version": 1, "focus": ["処理の回数"]}), encoding="utf-8")
    fake = Recording(
        tmp_path / "st",
        {
            "codex": [[{"text": "回数が増える", "severity": "major", "impact": "1 ラウンドごとに CLI の起動が 1 回増える"}], []],
            "kiro": [[], []],
        },
    )
    _run(capsys, monkeypatch, fake, wt)
    [focus_line] = [line for line in criteria_decide.criteria_lines(("処理の回数",)) if line.startswith("3.")]
    assert all(focus_line in p for a, _, p in fake.prompts)


def test_review_json_with_profiles_is_invalid(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch) -> None:
    """version 2・3 のプロファイルは黙って無視せず invalid で止める（相手役を起動しない）。"""
    (repos.main / ".ndf").mkdir(exist_ok=True)
    (repos.main / ".ndf" / "review.json").write_text(json.dumps({"version": 3, "profiles": [{"name": "code"}]}), encoding="utf-8")
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
