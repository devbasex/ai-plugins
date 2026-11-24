"""空の状態ディレクトリから始め直した cross-review が、PR に残った未解決の指摘を、プロジェクト MVV に反するという判定ごと引き継ぐこと
（#4076・#4090）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import pytest

from ndf.adapters import publish
from ndf.domains.crossreview import prior_decide, report_decide, store, threads
from ndf.domains.crossreview import finish_decide as fin
from ndf.domains.crossreview.journal import FindingJournal
from ndf.domains.crossreview.prior_decide import Remark, Thread
from ndf.domains.records.mvv_decide import identity_of

from ._repos import Repos
from .test_crossreview_citations import WRONG, Fixer, _base, _by_ndf, _git_fails, _push_pr
from .test_crossreview_mvv import MAJOR, REDLINE_BODY, _prompt, _push_base
from .test_crossreview_run import IMPACT, PR, Recording, _run, _state

ITEMS = ("Mission", "Value 1", "Value 5", "P1", "C7")
V11, V12 = (identity_of("approved", n, digit * 64, "c" * 64) for n, digit in ((11, "a"), (12, "b")))
VALUE, REDLINE, CITATION = "反する指摘", "人に聞く操作", "根拠の記述の誤り"
ERROR = {"path": "doc.md", "line": 3, "cited": "Value 9", "kind": "unknown", "unknown": ["Value 9"], "text": CITATION}
MARKS = ("mvv", "mvv_pinned", "redline", "redline_items", "judged_mvv", "unpinned_severity")
JUDGED = {
    VALUE: {"mvv": "Value 5", "mvv_pinned": True, "judged_mvv": V11, "unpinned_severity": "minor"},
    REDLINE: {"mvv": "P1", "redline": True, "redline_items": ["P1", "C7"], "judged_mvv": V11},
    CITATION: {"judged_mvv": V11},
}


@pytest.fixture(autouse=True)
def _mvv_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NDF_MVV_STATE_DIR", str(tmp_path / "mvv-state"))


def _scrub(texts: Any) -> list[str]:
    return [publish.scrub(t, True, accounts=()).text for t in texts]


def _left() -> FindingJournal:
    """前の実行が未解決のまま残した、MVV の項目に反する指摘・人の承認が要る操作の指摘・根拠の記述の誤り。"""
    journal = FindingJournal()
    raised = [(VALUE, "Value 5"), (REDLINE, "C7"), (REDLINE, "P1")]
    found = [{"text": t, "mvv": m, "severity": "minor", "impact": IMPACT, "by": "codex", "path": "doc.md"} for t, m in raised]
    journal.enter(1, found, ITEMS, V11)
    journal.ingest_citations([ERROR], V11, 1)
    return journal


def _published(journal: FindingJournal, done: Optional[dict[str, Any]] = None) -> Remark:
    """PR に残る記録のコメント（秘密などを伏せる処理を通した後）。"""
    data = {store.FINDINGS: list(journal.rows.values()), report_decide.THREADS: done or {}}
    body = report_decide.comment(PR, data, None, (), _scrub)
    return Remark("comment", "ndf", publish.scrub(body, True, accounts=()).text, trusted=True)


def _thread(comment: int, row: dict[str, Any], **how: Any) -> Thread:
    """指摘 `row` のスレッド（`comment` は最初のコメントの id）。"""
    return Thread(comment, *_scrub([threads.opening(row)]), False, "doc.md", 3, True)._replace(**how)


def _resumed(carried: list[prior_decide.Carried], mvv: str = V11, items: tuple[str, ...] = ITEMS) -> FindingJournal:
    """空の状態から `carried` を引き継いだジャーナル。"""
    journal = FindingJournal()
    assert journal.carry(carried, items, mvv) == [c.id for c in carried]
    return journal


def _marks(journal: FindingJournal) -> dict[str, dict[str, Any]]:
    return {r["text"]: {k: r[k] for k in MARKS if k in r} for r in journal.rows.values()}


def _handed(journal: FindingJournal) -> list[str]:
    return [r["text"] for r in journal.handable()]


# --- 記録のコメントとスレッドから戻す指摘 -------------------------------------------------------


def test_issue_4090_5_judgements_come_back_from_the_record() -> None:
    """軽い重さの「人の承認が要る操作」の指摘も記録のコメントに載り、3 つの指摘が判定ごと戻る。"""
    listed, marks = prior_decide.recorded([_published(_left())])
    journal = _resumed(listed)
    assert (_marks(journal), marks) == (JUDGED, {})
    assert _handed(journal) == [VALUE, CITATION] and journal.warnings == []
    assert journal.restate(V11) == []


def test_issue_4090_5_judgements_are_put_on_findings_carried_from_threads() -> None:
    """スレッドから戻す指摘には、記録のコメントにある同じスレッドの判定を重ねる。"""
    left = _left()
    numbers = dict(zip(left.rows, (71, 72, 73)))
    listed, marks = prior_decide.recorded([_published(left, {fid: {"comment": n} for fid, n in numbers.items()})])
    assert [(c.text, c.thread, c.comment) for c in listed] == [(CITATION, 73, None)]
    opened = [_thread(n, left.rows[fid]) for fid, n in numbers.items()]
    carried = prior_decide.carried(opened, set(), marks)
    assert [c.id for c in carried] == ["t71", "t72", "t73"]
    assert _marks(_resumed(carried)) == JUDGED
    # 記録のコメントに無いスレッドの指摘は、判定なしで戻る
    assert _marks(_resumed(prior_decide.carried(opened, set()))) == {VALUE: {}, REDLINE: {}, CITATION: {}}


def test_issue_4090_5_judgements_of_an_earlier_mvv_are_dropped() -> None:
    """MVV が変わった後の起動は、前の MVV での判定を外して履歴に残し、固定した重さを固定する前へ戻す。"""
    journal = _resumed(prior_decide.recorded([_published(_left())])[0], V12)
    assert _marks(journal) == {VALUE: {}, REDLINE: {}, CITATION: {}}
    rows = {r["text"]: r for r in journal.rows.values()}
    assert (rows[VALUE]["severity"], rows[REDLINE]["severity"], rows[CITATION]["by"]) == ("minor", "minor", "ndf")
    [old] = rows[REDLINE]["mvv_history"]
    assert (old["judged_mvv"], old["mvv"], old["redline_items"], old["outcome"]) == (V11, "P1", ["P1", "C7"], fin.UNRESOLVED)
    assert _handed(journal) == [CITATION] and journal.warnings == [] and journal.restate(V12) == []
    # 根拠の記述の誤りは、次の確かめが今の MVV での判定を置き直す
    journal.ingest_citations([ERROR], V12, 1)
    assert [r["judged_mvv"] for r in journal.handable()] == [V12]


def test_carried_item_missing_from_the_mvv_is_an_ordinary_finding_with_a_warning() -> None:
    listed, _ = prior_decide.recorded([_published(_left())])
    journal = _resumed(listed, items=("Mission", "C7"))
    assert _marks(journal) == {
        VALUE: {},
        REDLINE: {"mvv": "C7", "redline": True, "redline_items": ["C7"], "judged_mvv": V11},
        CITATION: {"judged_mvv": V11},
    }
    assert journal.rows["p-r1-1"]["severity"] == "minor"
    [warning] = journal.warnings
    assert "p-r1-1" in warning and "MVV の項目" in warning and "p-r1-2" not in warning


@pytest.mark.parametrize("bad", [{"redline_items": "P1", "mvv": 5}, {"redline_items": [1, None], "mvv": ["P1"], "unpinned_severity": "?"}])
def test_values_of_another_shape_in_the_record_are_not_read(bad: dict[str, Any]) -> None:
    row = {"id": "r1-1", "text": VALUE, "by": "codex", "outcome": fin.UNRESOLVED, "judged_mvv": V11, **bad}
    remark = Remark("comment", "ndf", report_decide.comment(PR, {store.FINDINGS: [row]}, None), trusted=True)
    journal = _resumed(prior_decide.recorded([remark])[0])
    assert _marks(journal) == {VALUE: {}} and len(journal.warnings) == 1


def test_record_lists_light_findings_only_when_they_wait_for_a_person_or_were_made_by_ndf() -> None:
    rows = [
        {"id": "r1-1", "text": "軽い"},
        {"id": "r1-2", "text": REDLINE, "redline": True, "mvv": "P1", "redline_items": ["P1"], "judged_mvv": V11},
        {"id": "r1-n1", "text": CITATION, "by": "ndf", "citation_key": "k" * 64, "judged_mvv": V11},
    ]
    data = {store.FINDINGS: [{"by": "codex", "outcome": fin.UNRESOLVED, "severity": "minor", **r} for r in rows]}
    block = report_decide.open_block(data)
    assert [i["id"] for i in report_decide.open_items(block)] == ["r1-2", "r1-n1"]
    # 根拠の記述の誤りを見分ける値は、手元の状態ファイルだけに持つ
    assert "citation_key" not in block and "k" * 64 not in block


# --- 既にある指摘に重ねる判定 -------------------------------------------------------------------

PLAIN = {"text": REDLINE, "severity": "major", "impact": IMPACT, "by": "codex", "path": "doc.md"}


def _held(outcome: str = fin.UNRESOLVED, mvv: Optional[str] = None) -> FindingJournal:
    """同じ本文の指摘を普通の指摘（`mvv` なら `Value 5`）として持つ、元の保存先のジャーナル。"""
    journal = FindingJournal()
    journal.enter(1, [PLAIN if mvv is None else {**PLAIN, "mvv": "Value 5"}], ITEMS, mvv)
    journal.rows["r1-1"]["outcome"] = outcome
    return journal


@pytest.mark.parametrize("outcome", [fin.UNRESOLVED, fin.FIXED, fin.SKIPPED])
@pytest.mark.parametrize("judged", [None, V11, V12])
def test_redline_in_the_record_is_put_on_the_finding_already_held(outcome: str, judged: Optional[str]) -> None:
    listed, _ = prior_decide.recorded([_published(_left())])
    journal = _held(outcome, judged)
    assert "r1-1" not in journal.carry(listed, ITEMS, V11) and journal.merged == ["r1-1"]
    assert [r["id"] for r in journal.rows.values() if r["text"] == REDLINE] == ["r1-1"]
    assert (_marks(journal)[REDLINE], journal.rows["r1-1"]["outcome"]) == (JUDGED[REDLINE], fin.UNRESOLVED)
    assert REDLINE not in _handed(journal) and journal.revived == ([] if outcome == fin.UNRESOLVED else ["r1-1"])
    # 同じ記録をもう一度読んでも変わらず、人が閉じた後も戻さない
    journal.rows["r1-1"]["outcome"] = fin.FIXED
    assert journal.carry(listed, ITEMS, V11) == [] and journal.merged == []
    assert journal.rows["r1-1"]["outcome"] == fin.FIXED


def test_judgement_of_another_mvv_in_the_record_is_not_put_on_the_finding_already_held() -> None:
    journal = _held()
    journal.carry(prior_decide.recorded([_published(_left())])[0], ITEMS, V12)
    assert (_marks(journal)[REDLINE], journal.merged, REDLINE in _handed(journal)) == ({}, [], True)


def test_redline_judged_on_a_tracked_thread_reaches_the_first_state_directory(
    repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch, hub
) -> None:
    """別の保存先が、状態ファイルの追うスレッドの指摘をレッドラインと判定した後に、元の保存先で再開しても修正担当へ渡さない。"""
    _push_base(repos, text=REDLINE_BODY)
    plain = {**MAJOR, "path": "doc.md"}
    first = Recording(tmp_path / "st", {"codex": [[plain]], "kiro": [[]]}, decide=lambda fid: None)
    _run(capsys, monkeypatch, first, wt, "--base", "main", "--max-rounds", "1")
    hub.port.existing = [Remark("comment", "ndf", hub.comments[-1][2], trusted=True)]
    other = Recording(tmp_path / "st2", {"codex": [[{**plain, "mvv": "P1"}]], "kiro": [[]]}, decide=lambda fid: None)
    _run(capsys, monkeypatch, other, wt, "--base", "main", "--max-rounds", "1")
    hub.port.existing = [Remark("comment", "ndf", hub.comments[-1][2], trusted=True)]
    again = Recording(tmp_path / "st", {"codex": [], "kiro": []}, decide=lambda fid: None)
    _, got = _run(capsys, monkeypatch, again, wt, "--base", "main", "--max-rounds", "1")
    [row] = _state(tmp_path / "st")["findings"]
    assert (row["mvv"], row["redline"], row["outcome"]) == ("P1", True, fin.UNRESOLVED)
    assert "人の承認を待つ 1" in got["summary"]


# --- 戻した根拠の記述の誤り --------------------------------------------------------------------

SECRET = "s3cr3t-value-0123456789"


@pytest.mark.parametrize("route", ["記録のコメント", "スレッド"])
def test_issue_4090_5_carried_citation_error_is_replaced_once_when_it_is_found_again(route: str, monkeypatch) -> None:
    """戻した指摘は誤りを見分ける値を持たないので、同じ誤りが見つかると直したになり、新しい指摘が 1 回だけ増える。引いた項目が秘密を含み、
    PR では伏せてあっても同じである。"""
    monkeypatch.setenv("NDF_TEST_API_TOKEN", SECRET)
    error = {**ERROR, "cited": f"Value {SECRET}", "text": f"{CITATION} {SECRET}"}
    left = FindingJournal()
    left.ingest_citations([error], V11, 1)
    if route == "スレッド":
        carried = prior_decide.carried([_thread(73, left.rows["r1-n1"])], set(), prior_decide.recorded([_published(left)])[1])
    else:
        carried, _ = prior_decide.recorded([_published(left)])
    [c] = carried
    assert SECRET not in c.text and c.text.startswith(CITATION)
    journal = _resumed(carried)
    assert "citation_key" not in journal.rows[c.id]
    # 確かめられなかった起動では、未解決のまま残る
    assert not journal.ingest_citations(None, V11, 1) and [r["id"] for r in journal.handable()] == [c.id]
    for _ in range(2):
        journal.ingest_citations([error], V11, 1)
        assert {fid: r["outcome"] for fid, r in journal.rows.items()} == {c.id: fin.FIXED, "r1-n1": fin.UNRESOLVED}


def _first_run(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch, hub) -> int:
    """根拠の記述の誤りを未解決のまま残す 1 回目の起動。PR に残った記録のコメントを次の起動に読ませ、誤りのスレッドの id を返す。"""
    _push_base(repos)
    _push_pr(repos, "doc.md", "# 設計", WRONG)
    _base(capsys, monkeypatch, Fixer(tmp_path / "st", wt), wt, "--max-rounds", "1")
    hub.port.existing = [Remark("comment", "ndf", hub.comments[-1][2], trusted=True)]
    return _state(tmp_path / "st")["threads"]["r1-n1"]["comment"]


@pytest.mark.parametrize(
    ("thread", "kept"),
    [("開いている", "t{}"), ("resolve 済み", "p-r1-n1"), ("書き換えられた", "p-r1-n1"), ("消えた", "p-r1-n1")],
)
def test_issue_4090_5_citation_error_is_carried_whatever_happened_to_its_thread(
    repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch, hub, thread: str, kept: str
) -> None:
    """根拠の記述の誤りは、スレッドがどうなっていても空の状態ディレクトリへ 1 件だけ戻り、確かめられない起動では未解決のまま止まる。"""
    comment = _first_run(repos, wt, tmp_path, capsys, monkeypatch, hub)
    if thread == "resolve 済み":
        hub.port.resolved.append(comment)
    elif thread == "書き換えられた":
        hub.port.made[PR] = [t._replace(body=f"{report_decide.MARKER}\n消した") for t in hub.port.made[PR]]
    elif thread == "消えた":
        hub.port.made[PR] = []
    again = Fixer(tmp_path / "st2", wt)
    again._fixes = 1
    with monkeypatch.context() as broken:
        _git_fails(broken, "_diff_files")
        _, got = _base(capsys, broken, again, wt, "--max-rounds", "1")
    fid = kept.format(comment)
    assert [(f["id"], f["outcome"]) for f in _by_ndf(again.state_dir)] == [(fid, fin.UNRESOLVED)]
    assert (got["status"], got["cause"]["kind"]) == ("stopped", "mvv_unresolved")
    assert fid in [g["name"] for g in got["outputs"]["gaps"]]


def test_citation_error_of_a_thread_in_the_state_file_is_not_carried_again(
    repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch, hub
) -> None:
    """状態ファイルが既に持つスレッドの誤りは、記録のコメントの本文が違っていても（伏せ字など）重ねない。"""
    _first_run(repos, wt, tmp_path, capsys, monkeypatch, hub)
    [remark] = hub.port.existing
    [row] = _by_ndf(tmp_path / "st")
    hub.port.existing = [remark._replace(body=remark.body.replace("承認済みでないバージョン", "[REDACTED]"))]
    assert row["text"] not in report_decide.open_items(hub.port.existing[0].body)[0]["text"]
    again = Fixer(tmp_path / "st", wt)
    again._fixes = 1
    _base(capsys, monkeypatch, again, wt, "--max-rounds", "1")
    assert [f["id"] for f in _by_ndf(tmp_path / "st")] == ["r1-n1"]


# --- 起動し直したときの扱い ---------------------------------------------------------------------


def test_issue_4090_5_redline_still_waits_for_a_person_after_starting_over(
    repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch, hub
) -> None:
    """空の状態ディレクトリから始め直しても、必ず人の承認が要る操作の指摘は修正担当へ渡さず、収束も止めない。MVV の項目に反する指摘は
    スレッドから判定ごと戻り、直すまで収束しない。"""
    _push_base(repos, text=REDLINE_BODY)
    held = {"text": REDLINE, "severity": "minor", "impact": IMPACT, "mvv": "P1"}
    value = {**MAJOR, "severity": "minor", "mvv": "Value 1", "path": "doc.md"}
    first = Recording(tmp_path / "st", {"codex": [[held, value]], "kiro": [[]]}, decide=lambda fid: None)
    _, got = _run(capsys, monkeypatch, first, wt, "--base", "main", "--max-rounds", "1")
    assert got["cause"]["kind"] == "mvv_unresolved"
    hub.port.existing = [Remark("comment", "ndf", hub.comments[-1][2], trusted=True)]
    again = Recording(tmp_path / "st2", {"codex": [], "kiro": []})
    again._fixes = 1
    code, got = _run(capsys, monkeypatch, again, wt, "--base", "main")
    assert (code, got["status"]) == (0, "ok") and "人の承認を待つ 1" in got["summary"]
    assert MAJOR["text"] in _prompt(again, "claude") and REDLINE not in _prompt(again, "claude")
    rows = {r["text"]: r for r in _state(again.state_dir)["findings"]}
    assert (rows[REDLINE]["redline"], rows[REDLINE]["mvv"], rows[REDLINE]["outcome"]) == (True, "P1", fin.UNRESOLVED)
    assert (rows[MAJOR["text"]]["mvv"], rows[MAJOR["text"]]["outcome"]) == ("Value 1", fin.FIXED)
    assert not any("MVV" in p["result"] for p in got["process"] if p["run"] == "start")


def test_issue_4076_finding_hidden_in_the_own_record_is_not_doubled(
    repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch, hub
) -> None:
    """同じ保存先の打ち直しは、自分の記録のコメントで本文が伏せられたスレッドの無い指摘を、別の指摘として重ねない。"""
    monkeypatch.setenv("NDF_TEST_API_TOKEN", SECRET)
    _push_base(repos, text=REDLINE_BODY)
    text = f"{SECRET} を文書に書いた"
    held = {"text": text, "severity": "minor", "impact": IMPACT, "mvv": "P1"}
    first = Recording(tmp_path / "st", {"codex": [[held]], "kiro": [[]]}, decide=lambda fid: None)
    _run(capsys, monkeypatch, first, wt, "--base", "main")
    body, own = hub.comments[-1][2], _state(tmp_path / "st")[report_decide.COMMENT_KEY]
    assert text not in body
    hub.port.existing = [Remark("comment", "ndf", body, trusted=True, url=own)]
    again = Recording(tmp_path / "st", {"codex": [[]], "kiro": [[]]}, decide=lambda fid: None)
    _, got = _run(capsys, monkeypatch, again, wt, "--base", "main")
    assert [r["id"] for r in _state(tmp_path / "st")["findings"]] == ["r1-1"] and "人の承認を待つ 1" in got["summary"]
    # 別の保存先は、同じコメントから引き継ぐ
    _run(capsys, monkeypatch, Recording(tmp_path / "st2", {"codex": [[]], "kiro": [[]]}), wt, "--base", "main")
    assert [r["id"] for r in _state(tmp_path / "st2")["findings"]] == ["p-r1-1"]


def test_warning_about_a_carried_item_reaches_the_result(repos: Repos, wt: Path, tmp_path: Path, capsys, monkeypatch, hub) -> None:
    """記録のコメントにある項目が、同じ MVV の項目に無ければ、普通の指摘として引き継いだことを結果の注意に残す。"""
    _push_base(repos)
    fake = Recording(tmp_path / "st", {"codex": [[{**MAJOR, "mvv": "Value 1"}]], "kiro": [[]]}, decide=lambda fid: None)
    _run(capsys, monkeypatch, fake, wt, "--base", "main", "--max-rounds", "1")
    body = hub.comments[-1][2].replace('"mvv": "Value 1"', '"mvv": "Value 99"')
    hub.port.existing = [Remark("comment", "ndf", body, trusted=True)]
    again = Recording(tmp_path / "st2", {"codex": [], "kiro": []})
    again._fixes = 1
    _, got = _run(capsys, monkeypatch, again, wt, "--base", "main")
    assert len([w for w in got["warnings"] if "p-r1-1" in w and "MVV の項目" in w]) == 1
