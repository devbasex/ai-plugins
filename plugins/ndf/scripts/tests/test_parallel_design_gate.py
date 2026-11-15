"""`lib/parallel_design_gate.py`（#3603）: 1 スプリントのレーンの設計承認の依頼の組み立てと、その答え（`design-answer`）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
import design_pr_merge  # noqa: E402
import parallel_design_gate as pdg  # noqa: E402
import parallel_lane as pl  # noqa: E402
import parallel_ledger as plg  # noqa: E402
import parallel_plan as pp  # noqa: E402

SHA = "0123456789ab" + "c" * 28


def ti(question: str, options=("承認",)) -> dict:
    return {"questions": [{"question": question, "options": [{"label": o} for o in options]}], "metadata": {"source": "ndf-gate:1"}}


def _plan(main: Path) -> None:
    d = pp.plan_dir(main, "p1")
    d.mkdir(parents=True, exist_ok=True)
    lane = {"name": "la", "input": {"kind": "sprint", "issues": [7, 8]}, "plans": 2, "touches": ["a/"]}
    (d / "plan.json").write_text(json.dumps({"version": 1, "name": "p1", "rules": [], "lanes": [lane]}))


def _sprint(main: Path, plans: dict[int, tuple[int, str]]) -> Path:
    """`plans` は課題 → (設計 PR, 報告の結果)。設計のステージだけを持つスプリントを書く。"""
    sprint = main / ".ndf" / "sprint-s1"
    sprint.mkdir(parents=True)
    (sprint / "state.json").write_text(json.dumps({"issues": [7, 8], "gates": []}))
    names = []
    for n, (pr, res) in plans.items():
        plan = sprint / f"design-{n}.json"
        plan.write_text(json.dumps({"課題": [n], "steps": [{"id": "reject"}]}))
        (sprint / f"design-{n}-state").mkdir()
        (sprint / f"design-{n}-state" / "report.md").write_text(
            f"# 報告\n\n- 結果: {res}\n- Pull Request: https://github.com/o/r/pull/{pr}\n"
        )
        names.append(plan.name)
    stages = [
        {"name": "設計", "plans": names},
        {"name": "設計 PR のマージ", "plans": [], "resume": "python3 supervise.py queue --resume d"},
    ]
    (sprint / "sprint.json").write_text(json.dumps({"スプリント": "s1", "ステージ": stages}, ensure_ascii=False))
    return sprint


def open_pr(_root, _pr):
    return {"state": "OPEN", "headRefOid": SHA}


# ---- 承認の行（I1・決定 2） ----


@pytest.mark.parametrize(
    "question, want",
    [
        ("設計 PR https://github.com/o/r/pull/12 をマージしてよいか。\n承認: #12", [12]),
        ("  承認: #12  ", [12]),
        ("設計の前提が崩れた。#12 の設計を直してよいか（https://github.com/o/r/pull/12）", []),
        ("承認: #12\n承認: #12", [12, 12]),
        ("承認: #12 と #13", []),
    ],
)
def test_approval_prs_reads_only_approval_lines(question, want):
    assert pdg.approval_prs(ti(question)) == want


def test_approval_prs_ignores_options():
    assert pdg.approval_prs(ti("どれにするか", options=("承認: #12",))) == []


# ---- 承認ゲートで終わった設計 PR ----


def test_gated_design_prs_lists_gated_plans_only(tmp_path):
    sprint = _sprint(tmp_path, {7: (12, "関門"), 8: (13, "完了")})
    got = pdg.gated_design_prs(sprint)
    assert list(got) == [12] and got[12]["issue"] == 7 and got[12]["report"].endswith("design-7-state/report.md")


def test_gated_design_prs_without_sprint_json_is_empty(tmp_path):
    assert pdg.gated_design_prs(tmp_path) == {}


# ---- 依頼の id（I2・I4） ----


def _g1(rid):
    return {"kind": "gate1", "id": rid, "msg": {"id": rid}}


def _ans(rid, by="mvv", result="approved", reason="red"):
    return {"kind": "answer", "id": rid, "msg": {"id": rid, "result": result, "by": by, "merge": {"result": "red", "reason": reason}}}


def test_next_gate1_id():
    base = "la-g12-0123456789ab"
    assert pdg.next_gate1_id([], base) == (base, False, "")
    assert pdg.next_gate1_id([_g1(base)], base) == (base, False, "")
    assert pdg.next_gate1_id([_g1(base), _ans(base, by="user")], base) == (base, False, "")
    assert pdg.next_gate1_id([_g1(base), _ans(base)], base) == (f"{base}-r1", True, "red")
    rows = [_g1(base), _ans(base), _g1(f"{base}-r1"), _ans(f"{base}-r1", reason="conflict")]
    assert pdg.next_gate1_id(rows, base) == (f"{base}-r2", True, "conflict")


# ---- 設計承認の依頼の中身（受け入れ条件 1・9） ----


def test_design_gate_builds_the_request_for_the_gated_pr(tmp_path):
    _plan(tmp_path)
    _sprint(tmp_path, {7: (12, "関門"), 8: (13, "関門")})
    got = pdg.design_gate(tmp_path, "p1", "la", ti("設計 PR #12 をマージしてよいか。\n承認: #12"), [], view=open_pr)
    assert (got.issue, got.pr, got.stamp, got.suffix, got.returned) == (7, 12, SHA, "", False)
    assert got.state.endswith("sprint-s1/state.json") and got.verdict == "unknown"


@pytest.mark.parametrize(
    "question, view",
    [
        ("設計の前提が崩れた。https://github.com/o/r/pull/12 を直してよいか", open_pr),  # 承認の行が無い
        ("承認: #12\n承認: #13", open_pr),  # 承認の行が 2 行
        ("承認: #99", open_pr),  # 承認ゲートで終わった設計 PR でない
        ("承認: #12", lambda r, p: {"state": "MERGED", "headRefOid": SHA}),
        ("承認: #12", lambda r, p: None),
        ("承認: #12", lambda r, p: {"state": "OPEN", "headRefOid": ""}),
    ],
)
def test_design_gate_returns_none_when_the_pr_is_not_determined(tmp_path, question, view):
    _plan(tmp_path)
    _sprint(tmp_path, {7: (12, "関門"), 8: (13, "関門")})
    assert pdg.design_gate(tmp_path, "p1", "la", ti(question), [], view=view) is None


def test_design_gate_without_a_sprint_returns_none(tmp_path):
    _plan(tmp_path)
    assert pdg.design_gate(tmp_path, "p1", "la", ti("承認: #12"), [], view=open_pr) is None


def test_design_gate_returns_a_returned_request_after_an_mvv_approval(tmp_path):
    """I4: MVV 判定で承認した同じ先頭の依頼があり PR が開いたままなら、接尾辞と印 returned とマージの理由を付ける。"""
    _plan(tmp_path)
    _sprint(tmp_path, {7: (12, "関門")})
    base = "la-g12-0123456789ab"
    got = pdg.design_gate(tmp_path, "p1", "la", ti("承認: #12"), [_g1(base), _ans(base)], view=open_pr)
    assert (got.suffix, got.returned) == ("-r1", True) and "red" in got.reasons


# ---- 答え（受け入れ条件 3・5、決定 4） ----


def _request(main: Path, sprint: Path, rid: str = "la-g12-0123456789ab") -> None:
    report = str(sprint / "design-7-state" / "report.md")
    msg = {"kind": "gate1", "id": rid, "lane": "la", "issue": 7, "pr": 12, "report": report, "sprint_state": str(sprint / "state.json")}
    plg.append_ledger_row(main, "p1", "gate1", f"gate1:{rid}", {**msg, "stamp": SHA, "verdict": "unknown", "reasons": ["r"]})


def _answer(main: Path, result: str, by: str = "user", note: str | None = None, rid: str = "la-g12-0123456789ab") -> None:
    pl.send_answer(main, "p1", rid, result, note=note, by=by, merge={"result": "merged", "reason": ""} if result == "approved" else None)


class Run:
    def __init__(self, code=0):
        self.calls, self.code = [], code

    def __call__(self, cmd, cwd=None, stdin=None):
        self.calls.append(cmd)
        return self.code, json.dumps({"summary": "失敗した"}) if self.code else "", ""


@pytest.fixture()
def lane(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "record_exists", lambda main, name: False)
    _plan(tmp_path)
    sprint = _sprint(tmp_path, {7: (12, "関門")})
    _request(tmp_path, sprint)
    return tmp_path, sprint


def test_answer_design_without_answer_is_3(lane):
    main, sprint = lane
    assert pdg.answer_design(main, "p1", "la-g12-0123456789ab", sprint / "state.json", run=Run()).code == 3


def test_answer_design_rejects_a_non_gate1_id(lane):
    main, sprint = lane
    assert pdg.answer_design(main, "p1", "la-qX", sprint / "state.json", run=Run()).code == 1


def test_answer_design_user_approval_writes_the_gate_record(lane):
    main, sprint = lane
    _answer(main, "approved")
    run = Run()
    got = pdg.answer_design(main, "p1", "la-g12-0123456789ab", sprint / "state.json", run=run)
    status, _, code, m = got.status, got.summary, got.code, got.metrics
    assert (status, code) == ("ok", 0) and m["next"].endswith("queue --resume d") and m["merge"]["result"] == "merged"
    cmd = run.calls[0]
    assert cmd[cmd.index("gate") + 2] == "設計承認ゲート" and cmd[cmd.index("--by") + 1] == "user"
    assert cmd[cmd.index("--pr") + 1] == "12" and cmd[cmd.index("--outcome") + 1] == "approved"


def test_answer_design_mvv_approval_writes_one_record(lane):
    """受け入れ条件 3・I9: MVV 判定の承認は by: mvv・pr・outcome の記録を 1 件だけ書く（2 回打っても 1 件）。"""
    main, sprint = lane
    _answer(main, "approved", by="mvv")
    state = sprint / "state.json"
    for _ in range(2):
        got = pdg.answer_design(main, "p1", "la-g12-0123456789ab", state, run=design_pr_merge.run_cmd)
        summary, code = got.summary, got.code
        assert code == 0, summary
    gates = json.loads(state.read_text())["gates"]
    assert [(g["by"], g["pr"], g["outcome"]) for g in gates] == [("mvv", 12, "approved")]


def test_answer_design_revise_writes_the_note_and_does_not_record_approval(lane, tmp_path):
    main, sprint = lane
    _answer(main, "revise", note="図を直す")
    run = Run()
    got = pdg.answer_design(main, "p1", "la-g12-0123456789ab", sprint / "state.json", run=run)
    status, _, code, m = got.status, got.summary, got.code, got.metrics
    assert (status, code, run.calls) == ("gate", 10, [])
    assert Path(m["note"]).read_text() == "図を直す" and "--from reject" in m["next"]


def test_answer_design_rejected_records_once(lane):
    main, sprint = lane
    _answer(main, "rejected")
    run = Run()
    for _ in range(2):
        got = pdg.answer_design(main, "p1", "la-g12-0123456789ab", sprint / "state.json", run=run)
        status, _, code, _m = got.status, got.summary, got.code, got.metrics
        assert (status, code) == ("gate", 10)
    assert len(run.calls) == 1 and run.calls[0][run.calls[0].index("--outcome") + 1] == "rejected"


def test_answer_design_stops_when_the_record_cannot_be_written(lane):
    main, sprint = lane
    _answer(main, "approved")
    got = pdg.answer_design(main, "p1", "la-g12-0123456789ab", sprint / "state.json", run=Run(code=2))
    status, summary, code, _m = got.status, got.summary, got.code, got.metrics
    assert (status, code) == ("stopped", 2) and "失敗した" in summary


def test_answer_design_stops_on_an_unreadable_state(lane):
    main, sprint = lane
    _answer(main, "approved")
    assert pdg.answer_design(main, "p1", "la-g12-0123456789ab", sprint / "none.json", run=Run()).code == 2


def test_answer_design_rejects_other_answers(lane):
    main, sprint = lane
    pl.send_answer(main, "p1", "la-g12-0123456789ab", "answered", choice=["x"])
    assert pdg.answer_design(main, "p1", "la-g12-0123456789ab", sprint / "state.json", run=Run()).code == 1
