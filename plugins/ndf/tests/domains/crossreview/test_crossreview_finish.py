"""収束ループの終わり方から結果を決める（#2370・#2444・PR #3462 の保留）。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from ndf.contracts import result
from ndf.domains.crossreview import cli, drive, journal, reviewed_pr, scope_decide, store, threads
from ndf.domains.crossreview import finish_decide as fin
from ndf.domains.crossreview.settings import RunConfig

from ._repos import previous_output, run_cli


def _summary(tmp_path: Path, final: str, rounds: int, findings: list[dict]) -> Path:
    path = tmp_path / "summary.json"
    path.write_text(json.dumps({"final": final, "rounds": rounds, "findings": findings}), encoding="utf-8")
    return path


def _rebuild(tmp_path: Path, items: list) -> Path:
    path = tmp_path / "rebuild.json"
    path.write_text(json.dumps(result.result("rebuild", "ok", "足すべき内容", items)), encoding="utf-8")
    return path


FIXED = {"id": "F1", "text": "語の定義が無い", "outcome": fin.FIXED}
DEFERRED = {"id": "F2", "text": "読み順", "outcome": fin.SKIPPED, "reason": "認可に当たるため #9 で扱う"}


@pytest.mark.parametrize("final", [fin.MAX_ROUNDS, fin.ERROR])
def test_issue_2370_limit_with_no_unresolved_findings_moves_on_and_records_why(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], final: str
) -> None:
    """#2370: 上限や途中の誤りで終わっても、未解決 0 件・見送りに理由があれば ok で次の工程へ進み、理由を decisions に残す。"""
    code, got = run_cli(capsys, "finish", "--summary", str(_summary(tmp_path, final, 4, [FIXED, DEFERRED])))
    assert code == 0 and got["status"] == "ok"
    assert any(final in d["decision"] and "次の工程へ進む" in d["decision"] for d in got["decisions"])
    assert got["warnings"]


def test_issue_2370_unresolved_findings_stop_as_incomplete_with_gaps(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """#2370: 未解決が残れば incomplete（gaps つき）で止まり、プランの incomplete のルートが受ける。"""
    open_one = {"id": "F3", "text": "コントラクトが食い違う", "outcome": fin.UNRESOLVED}
    no_reason = {"id": "F4", "text": "名前", "outcome": fin.SKIPPED}
    code, got = run_cli(capsys, "finish", "--summary", str(_summary(tmp_path, fin.MAX_ROUNDS, 4, [FIXED, open_one, no_reason])))
    assert got["cause"]["kind"] == "incomplete" and code == result.code_for(got)
    assert [(g["kind"], g["name"]) for g in got["gaps"]] == [(fin.GAP_UNRESOLVED, "F3"), (fin.GAP_NO_REASON, "F4")]


def test_issue_2444_rebuild_findings_each_become_fixed_or_deferred(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """#2444 の残り: 組み直しの読み手役の「足すべき内容」は、レビューで 1 件ずつ「直した」「見送った（理由）」になり、
    どちらにもならなかった件は欠けとして結果に残る。"""
    rebuild = _rebuild(tmp_path, [{"id": "R1", "text": "出すコミットの固定"}, {"id": "R2", "text": "項目名の対応"}, "記録の行のキー"])
    handled = [
        {"id": "R1", "text": "出すコミットの固定", "outcome": fin.FIXED},
        {"id": "R2", "text": "項目名の対応", "outcome": fin.SKIPPED, "reason": "別の設計で決める"},
    ]
    code, got = run_cli(capsys, "finish", "--summary", str(_summary(tmp_path, fin.CONVERGED, 2, handled)), "--findings", str(rebuild))
    assert got["cause"]["kind"] == "incomplete"
    assert [d["decision"] for d in got["decisions"]] == ["足すべき内容 R1 を直した", "足すべき内容 R2 を見送った（理由: 別の設計で決める）"]
    assert got["gaps"] == [
        {"kind": fin.GAP_UNHANDLED, "name": "rebuild-3", "reason": "レビューで直したとも見送ったとも決めていない: 記録の行のキー"}
    ]
    handled.append({"id": "rebuild-3", "text": "記録の行のキー", "outcome": fin.FIXED})
    code, got = run_cli(capsys, "finish", "--summary", str(_summary(tmp_path, fin.CONVERGED, 2, handled)), "--findings", str(rebuild))
    assert code == 0 and len(got["decisions"]) == 3 and "warnings" not in got


def test_pr_3462_incomplete_review_keeps_gaps_in_its_result_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """PR #3462 の保留: incomplete を返すとき、未解決の gaps を結果 JSON に載せる（プランの結果へ引き継ぐ元）。"""
    open_one = {"id": "F3", "text": "コントラクトが食い違う", "outcome": fin.UNRESOLVED}
    code, got = run_cli(capsys, "finish", "--summary", str(_summary(tmp_path, fin.CONVERGED, 1, [open_one])))
    assert result.errors(got, code) == []
    assert got["gaps"][0]["name"] == "F3"
    assert got["outputs"] == {"gaps": got["gaps"]}


def test_mvv_findings_left_open_are_a_gap_of_their_own(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """MVV に反する指摘の未解決は、ほかの未解決と別の種類の欠けにする。"""
    mvv = {"id": "F1", "text": "反する", "outcome": fin.UNRESOLVED, "severity": "major", "mvv": "Value 5"}
    other = {"id": "F2", "text": "止まる", "outcome": fin.UNRESOLVED, "severity": "major"}
    deferred = {**mvv, "id": "F3", "outcome": fin.SKIPPED, "reason": "反しない"}
    _, got = run_cli(capsys, "finish", "--summary", str(_summary(tmp_path, fin.MAX_ROUNDS, 2, [mvv, other, deferred])))
    assert [(g["kind"], g["name"]) for g in got["outputs"]["gaps"]] == [(fin.GAP_MVV, "F1"), (fin.GAP_UNRESOLVED, "F2")]
    assert (got["status"], got["cause"]["kind"]) == ("stopped", "mvv_unresolved") and "F1（Value 5）" in got["summary"]


@pytest.mark.parametrize("final", [fin.CONVERGED, fin.MAX_ROUNDS])
def test_unfixed_citation_errors_and_a_failed_check_stop_the_review(tmp_path: Path, capsys: pytest.CaptureFixture[str], final: str) -> None:
    """根拠の記述の誤りが残るか、最後のコミットで確かめられなければ、指摘が 0 件でも止まる。直っていれば止まらない。"""
    wrong = {"id": "r1-n1", "text": "誤り", "outcome": fin.UNRESOLVED, "severity": "major", "by_ndf": True}
    for findings, checked, names in [
        ([wrong], True, ["r1-n1"]),
        ([], False, [fin.CITATION_CHECK]),
        ([wrong], False, ["r1-n1", fin.CITATION_CHECK]),
        ([{**wrong, "outcome": fin.FIXED}], True, []),
    ]:
        path = _summary(tmp_path, final, 2, findings)
        path.write_text(json.dumps({**json.loads(path.read_text(encoding="utf-8")), "mvv_checked": checked}), encoding="utf-8")
        _, got = run_cli(capsys, "finish", "--summary", str(path))
        assert [(g["kind"], g["name"]) for g in got["outputs"]["gaps"]] == [(fin.GAP_MVV, n) for n in names]
        assert got["status"] == ("stopped" if names else "ok")
        assert not names or got["cause"]["kind"] == "mvv_unresolved"


def test_secrets_in_findings_do_not_reach_the_result(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """指摘の本文・見送った理由の中の秘密は結果 JSON に出さない。根拠の記述の誤りの本文（文書のパスと根拠欄の中身）は載せない。"""
    token = "ghp_" + "a" * 20
    left = {"id": "F1", "text": f"止まる {token}", "outcome": fin.UNRESOLVED, "severity": "major"}
    wrong = {"id": "r1-n1", "text": "secret.md の「owner/private」", "outcome": fin.UNRESOLVED, "severity": "major", "by_ndf": True}
    done = {"id": "R1", "text": "足す", "outcome": fin.SKIPPED, "reason": f"別で扱う {token}"}
    rebuild = _rebuild(tmp_path, [{"id": "R1", "text": f"足す {token}"}])
    for findings in ([left, done], [left, wrong, done]):
        path = _summary(tmp_path, fin.MAX_ROUNDS, 2, findings)
        code, got = run_cli(capsys, "finish", "--summary", str(path), "--findings", str(rebuild))
        shown = json.dumps(got, ensure_ascii=False)
        assert result.errors(got, code) == [] and "止まる" in shown and "別で扱う" in shown
        assert not [word for word in (token, "secret.md", "owner/private") if word in shown]


def test_unread_reviewer_with_findings_left_is_still_a_valid_result(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """相手役が差分を読めずに止まったときの欠けは、結果 JSON の outputs にだけ載せる（最上位の gaps は incomplete だけが持てる）。"""
    left = {"id": "F1", "text": "止まる", "outcome": fin.UNRESOLVED, "severity": "major"}
    code, got = run_cli(capsys, "finish", "--summary", str(_summary(tmp_path, fin.INCOMPLETE_REVIEWERS, 1, [left])))
    assert result.errors(got, code) == [] and got["cause"]["kind"] == "work_stopped"
    assert [g["name"] for g in got["outputs"]["gaps"]] == ["F1"]


@pytest.mark.parametrize("severity", ["major", "minor"])
def test_redlines_are_not_gaps_but_are_counted(tmp_path: Path, capsys: pytest.CaptureFixture[str], severity: str) -> None:
    """必ず人の承認が要る操作の指摘だけが残っていれば収束とし、件数を要約に載せる。"""
    left = {"id": "F1", "text": "消す", "outcome": fin.UNRESOLVED, "severity": severity, "mvv": "P1", "redline": True}
    shown = {**left, "id": "F2", "outcome": fin.SKIPPED}
    code, got = run_cli(capsys, "finish", "--summary", str(_summary(tmp_path, fin.CONVERGED, 1, [left, shown])))
    assert (code, got["status"]) == (0, "ok")
    assert got["summary"].endswith("指摘 2 件（直した 0・見送った 1・未解決 0・人の承認を待つ 1）")


FAILED = {"rows": [{"key": "k", "outcome": "redline"}], "cause": "MVV の記録を書けない"}
OPEN_MVV = fin.Finding("F1", "反する", fin.UNRESOLVED, None, "major", "Value 5")


@pytest.mark.parametrize(
    ("findings", "handed", "failed", "kind", "within", "gaps"),
    [
        ((), (), None, None, "converged", []),
        # 受け入れ条件 5: 収束でも、承認ゲートの判定に要る記録を書けなければ止まる
        ((), (), FAILED, fin.RECORD_FAILED, "本来の終わり方は ok", []),
        ((OPEN_MVV,), (), FAILED, fin.RECORD_FAILED, "本来の終わり方は mvv_unresolved", ["F1"]),
        ((), (fin.Handed("R1", "足す"),), FAILED, fin.RECORD_FAILED, "本来の終わり方は incomplete", ["R1"]),
    ],
)
def test_rows_left_unwritten_stop_before_any_other_ending(findings, handed, failed, kind, within, gaps) -> None:
    got = fin.finish(fin.Review(fin.CONVERGED, 2, findings), handed, record_failed=failed)
    assert (got.kind, [g["name"] for g in got.gaps]) == (kind, gaps) and within in got.summary
    assert ("MVV の記録を書けない" in got.warnings) is (failed is not None)


@pytest.mark.parametrize(
    ("final", "rounds", "outcome", "kind"),
    [("done", 1, fin.FIXED, "invalid"), (fin.CONVERGED, 1, "maybe", "invalid"), (fin.ERROR, 0, fin.FIXED, "work_stopped")],
)
def test_unknown_words_are_invalid_and_an_error_before_any_round_is_work_stopped(final: str, rounds: int, outcome: str, kind: str) -> None:
    review = fin.Review(final, rounds, (fin.Finding("F1", "x", outcome, None),) if rounds else ())
    assert fin.finish(review).kind == kind


def test_malformed_summary_is_invalid(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, got = run_cli(capsys, "finish", "--summary", str(_summary(tmp_path, fin.CONVERGED, 1, ["x"])))
    assert code != 0 and got["cause"]["kind"] == "invalid"


def _held(outcome: str, defer: str | None = None, *, fid: str = "F1", redline: bool = False) -> fin.Finding:
    reason = "理由" if outcome == fin.SKIPPED else None
    return fin.Finding(fid, f"指摘 {fid}", outcome, reason, "major", redline=redline, defer=defer)


@pytest.mark.parametrize(
    ("found", "held"),
    [
        (_held(fin.SKIPPED, "needs_user"), True),
        (_held(fin.SKIPPED), True),
        (_held(fin.SKIPPED, "later"), True),
        (_held(fin.SKIPPED, "filed"), False),
        (_held(fin.SKIPPED, "not_applicable"), False),
        (_held(fin.UNRESOLVED, redline=True), True),
        (_held(fin.SKIPPED, "filed", redline=True), True),
        (_held(fin.FIXED, redline=True), False),
        (_held(fin.FIXED), False),
    ],
)
def test_stop_on_hold_turns_findings_waiting_for_a_person_into_gaps(found: fin.Finding, held: bool) -> None:
    """#4369 の決定 14: 直していないレッドラインと、区分が needs_user・無い・知らない値の見送りで incomplete になる。"""
    review = fin.Review(fin.CONVERGED, 1, (found,))
    assert fin.finish(review).status == "ok"
    got = fin.finish(review, stop_on_hold=True)
    assert (got.status == "ok", got.kind) == (not held, "incomplete" if held else None)
    assert list(got.gaps) == ([{"kind": "held_finding", "name": "F1", "reason": "指摘 F1"}] if held else [])


def test_stop_on_hold_adds_only_missing_names_and_leaves_other_endings() -> None:
    """incomplete には同じ名前の欠けが無い指摘だけを足し、ほかの停止は変えない。記録の失敗はその後に当てる。"""
    open_one = fin.Finding("F1", "止まる", fin.UNRESOLVED, None, "major")
    red = _held(fin.UNRESOLVED, fid="F2", redline=True)
    no_reason = fin.Finding("F3", "名前", fin.SKIPPED, None)
    got = fin.finish(fin.Review(fin.MAX_ROUNDS, 2, (open_one, red, no_reason)), stop_on_hold=True)
    assert [(g["kind"], g["name"]) for g in got.gaps] == [
        (fin.GAP_UNRESOLVED, "F1"),
        (fin.GAP_NO_REASON, "F3"),
        (fin.GAP_HELD, "F2"),
    ]
    limit = fin.finish(fin.Review(fin.MAX_ROUNDS, 2, (red,)), stop_on_hold=True)
    assert limit.kind == "incomplete" and not limit.decisions and not limit.warnings
    for review in (
        fin.Review(fin.INCOMPLETE_REVIEWERS, 1, (red,)),
        fin.Review(fin.CONVERGED, 1, (red, fin.Finding("F4", "反する", fin.UNRESOLVED, None, "major", "Value 5"))),
    ):
        assert fin.finish(review, stop_on_hold=True) == fin.finish(review)
    failed = fin.finish(fin.Review(fin.CONVERGED, 1, (red,)), record_failed=FAILED, stop_on_hold=True)
    assert failed.kind == fin.RECORD_FAILED and [g["name"] for g in failed.gaps] == ["F2"]


def test_the_defer_kind_reaches_the_verdict_through_the_journal_and_the_summary() -> None:
    """ジャーナルの行の区分は、要約を経ても残る。"""
    rows = [{"id": "F1", "text": "止まる", "outcome": fin.SKIPPED, "reason": "後で", "defer": "filed"}]
    found = journal.FindingJournal(rows).findings()
    summary = fin.summary_of(fin.Review(fin.CONVERGED, 1, found), 7)
    assert [f.defer for f in fin.as_review(summary).findings] == ["filed"]


def _in_process_run(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    *extra: str,
    head: str | None = "a" * 40,
    read: str | None = "o/r#9",
) -> tuple[int, dict, list]:
    """駆動と PR への記録を差し替えて `run` を打つ（git も相手役も起動しない）。HUB に渡った引数も返す。"""
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    cfg = RunConfig(tmp_path, "feat/x", "origin/main", 7, state, False, ("codex",))
    made: list = []
    review = fin.Review(fin.CONVERGED, 1, (_held(fin.SKIPPED, "needs_user"),))

    def fake_drive(cfg_, launch, reader, clean):
        reader(7)
        return drive.Outcome(review, state / "summary.json", (), (), head, reviewed_pr=read)

    monkeypatch.setattr(cli, "_ready", lambda args: (cfg, "saved/repo"))
    monkeypatch.setattr(cli.drive, "drive", fake_drive)
    monkeypatch.setattr(cli, "_report", lambda *a: ([], []))
    monkeypatch.setattr(cli, "HUB", lambda *a: made.append(a) or cli.Hub(None, None, lambda pr: None))
    code, got = run_cli(capsys, "run", "--pr", "7", "--state-dir", str(state), "--base", "origin/main", *extra)
    assert result.errors(got, code) == []
    return code, got, made


def test_run_takes_the_given_repository_over_the_saved_state(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--repo` は保存済みの状態より優先して HUB と子の `gh` へ渡り、読んだ PR は駆動の結果の値で書く。"""
    monkeypatch.setenv("GH_REPO", "before/repo")
    pr_out = tmp_path / "reviewed-pr"
    head_out = tmp_path / "reviewed-head"
    code, got, made = _in_process_run(
        monkeypatch,
        capsys,
        tmp_path,
        "--repo",
        "o/r",
        "--stop-on-hold",
        "--reviewed-pr-out",
        str(pr_out),
        "--reviewed-out",
        str(head_out),
    )
    assert (code, got["cause"]["kind"]) == (6, "incomplete") and got["gaps"][0]["kind"] == fin.GAP_HELD
    assert made == [("o/r", "saved/repo", tmp_path)] and os.environ["GH_REPO"] == "o/r"
    assert pr_out.read_text(encoding="utf-8") == "o/r#9\n" and head_out.read_text(encoding="utf-8") == "a" * 40 + "\n"


def test_run_empties_the_previous_reviewed_pr_and_writes_it_only_with_the_head(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """前の起動のファイルは空に戻し、HEAD を書かない起動は PR も書かない。引数が無ければ結果は今のまま。"""
    pr_out = tmp_path / "reviewed-pr"
    previous_output(pr_out, "o/r#1\n")
    code, got, made = _in_process_run(
        monkeypatch, capsys, tmp_path, "--reviewed-pr-out", str(pr_out), "--reviewed-out", str(tmp_path / "head"), head=None
    )
    assert (code, got["status"]) == (0, "ok") and pr_out.read_text(encoding="utf-8") == ""  # 同じファイルのまま空に戻す（r1-3）
    assert made == [(None, "saved/repo", tmp_path)]


@pytest.mark.parametrize("slug", ["norepo", "a/b/c", "/r"])
def test_run_refuses_a_malformed_repository_and_keeps_the_previous_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, slug: str
) -> None:
    """起動できない打ち方では、前の起動の出力も消さない（PR 4638 の C4）。"""
    monkeypatch.setenv("GH_REPO", "before/repo")
    pr_out = tmp_path / "reviewed-pr"
    (tmp_path / "state").mkdir()
    previous_output(pr_out, "o/r#1\n")
    code, got, made = _in_process_run(monkeypatch, capsys, tmp_path, "--repo", slug, "--reviewed-pr-out", str(pr_out))
    assert got["cause"]["kind"] == "invalid" and not made and pr_out.read_text(encoding="utf-8") == "o/r#1\n"
    assert os.environ["GH_REPO"] == "before/repo"


def test_run_stops_when_the_reviewed_pr_cannot_be_written(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    code, got, _ = _in_process_run(
        monkeypatch,
        capsys,
        tmp_path,
        "--repo",
        "o/r",
        "--reviewed-pr-out",
        str(tmp_path / "no-such-dir" / "reviewed-pr"),
        "--reviewed-out",
        str(tmp_path / "head"),
    )
    assert got["cause"]["kind"] == "precondition"


def test_run_writes_no_reviewed_pr_when_the_review_did_not_confirm_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR を読めないか head が一致しない回は、HEAD だけを書く。"""
    pr_out, head_out = tmp_path / "reviewed-pr", tmp_path / "head"
    _in_process_run(monkeypatch, capsys, tmp_path, "--reviewed-pr-out", str(pr_out), "--reviewed-out", str(head_out), read=None)
    assert head_out.exists() and not pr_out.exists()


def _reading_run(targets: list[str | None], pushed: set[str]) -> SimpleNamespace:
    """読んだ PR を持つ起動（push の送り先は Git の境界を差し替える）。"""
    pr = threads.ReadPr("o/r#9", "feat/x", "h0", "o/r")
    g = SimpleNamespace(push_repos=lambda remote: targets)
    return SimpleNamespace(cfg=SimpleNamespace(check_pr=True), read_pr=pr, pushed=pushed, g=g, remote="origin", warnings=[])


@pytest.mark.parametrize(
    ("head", "targets", "want"),
    [
        ("h0", ["other/r"], "o/r#9"),
        ("h1", ["O/R"], "o/r#9"),
        ("h1", ["other/r"], None),
        ("h1", ["o/r", None], None),
        ("h1", [], None),
        ("local", ["o/r"], None),
        (None, ["o/r"], None),
    ],
)
def test_the_reviewed_pr_needs_the_reviewed_head_to_be_in_the_pull_request(
    head: str | None, targets: list[str | None], want: str | None
) -> None:
    """読んだ head か、PR の head のリポジトリへ送った修正のコミットだけを PR の head と認める。"""
    run = _reading_run(targets, {"h1"})
    assert reviewed_pr.confirmed(run, head) == want
    assert bool(run.warnings) == (head == "h1" and want is None)
    run.read_pr = None
    assert reviewed_pr.confirmed(run, head) is None


@pytest.mark.parametrize(("read", "seen"), [("before", True), ("resent", True), ("elsewhere", False)])
def test_the_pull_request_read_before_a_resend_is_still_the_reviewed_one(read: str, seen: bool) -> None:
    """同期の前に読んだ head も、送り直した後に読んだ head も、同じ PR として扱う。"""
    run = SimpleNamespace(cfg=SimpleNamespace(branch="feat/x"), synced_head="before", sent="resent")
    pr = threads.ReadPr("o/r#9", "feat/x", read, "o/r")
    assert reviewed_pr.seen(run, pr) == (pr if seen else None)
    assert reviewed_pr.seen(run, pr._replace(branch="feat/y")) is None


def test_a_round_of_another_pull_request_is_not_reused() -> None:
    """読んだ PR の確認を求める起動は、同じ PR を読んだラウンドだけを使う。"""
    state = {store.ROUNDS: [{"head": "h1", scope_decide.PR: "o/r#9"}]}
    for pr, want in (("o/r#9", "h1"), ("o/r#10", None), (None, "h1")):
        assert scope_decide.read_by_all_head(state, scope_decide.Basis(pr=pr)) == want
    assert scope_decide.read_by_all_head({store.ROUNDS: [{"head": "h1"}]}, scope_decide.Basis(pr="o/r#9")) is None


def test_without_the_request_the_push_targets_are_not_read() -> None:
    """読んだ PR の確認を求めない起動は、git を起動せず、記録も出さない。"""
    run = _reading_run(["o/r"], {"h1"})
    run.cfg.check_pr = False
    run.g.push_repos = lambda remote: pytest.fail("push の送り先を調べた")
    assert reviewed_pr.confirmed(run, "h1") is None


@pytest.mark.parametrize(
    ("pr", "reason"),
    [(None, "読めない"), (threads.ReadPr("o/r#9", "feat/x", "elsewhere", "o/r"), "elsewhere"[:9])],
)
def test_an_unconfirmed_pull_request_stops_before_the_reviewers(
    monkeypatch: pytest.MonkeyPatch, pr: threads.ReadPr | None, reason: str
) -> None:
    """確認を求める起動は、PR を読めないか head が違えば、相手役を起動せずに理由を返す。"""
    monkeypatch.setattr(drive, "_reader", lambda cfg, reader_for, warnings: None)
    monkeypatch.setattr(drive, "_read_prior", lambda prs, warnings: drive._Prior(pr=pr))
    cfg = SimpleNamespace(check_pr=True, branch="feat/x")
    run = SimpleNamespace(cfg=cfg, synced_head="h0", sent="h0", read_pr=None, warnings=[])
    with pytest.raises(drive.DriveStopped, match=reason) as stopped:
        drive._drive_rounds(run, lambda spec: pytest.fail("相手役を起動した"), None)
    assert stopped.value.kind == "precondition"
    run.cfg.check_pr = False
    assert reviewed_pr.why_unread(run, pr) is None


def test_the_port_reports_the_pull_request_read_with_the_threads() -> None:
    page = {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []}
    pull = {"baseRefName": "main", "headRefName": "feat/x", "headRefOid": "h0", "reviewThreads": page}
    pull["headRepository"] = {"nameWithOwner": "fork/r"}
    hub = SimpleNamespace(repo="o/r", repository=lambda query, variables: {"pullRequest": pull})
    port = threads.GitHubPort(hub, None, 9, RuntimeError)
    assert port.read_pr() is None
    port.open_threads()
    assert port.read_pr() == threads.ReadPr("o/r#9", "feat/x", "h0", "fork/r")
