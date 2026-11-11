"""1 スプリントのレーンの答え待ちの停止の分け方（#3000）: 新しい承認を生むプランの判定（I1）・次に始めるプラン（決定 3・I5）・
答えの後に起こすか（受け入れ条件 4・5、I3）・耐久ワークフローの再起動の規則（I4、決定 4）。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_parallel_run as tpr  # noqa: E402,F401  lib を sys.path へ足す

import durable  # noqa: E402
import parallel_answer as pa  # noqa: E402
import parallel_hold as ph  # noqa: E402
import parallel_hold_sprint as phs  # noqa: E402
import parallel_launch as plc  # noqa: E402
import parallel_run as prun  # noqa: E402
from pace_state import sprint_state  # noqa: E402
from test_supervise_pace_auto import AUTO, load, make_repo, new_sprint  # noqa: E402

DESIGN = {"steps": [{"id": "d", "type": "work", "kind": ph.DESIGN_KIND, "prompt": "設計を書く", "next": "end"}]}
MERGE = {"steps": [{"id": "m", "type": "run", "cmd": "true", "next": "end"}]}
HOLD = {"plan": False, "lanes": ["la"], "count": 4, "by_lane": {"la": 4}, "limits": {"plan": 8, "lane": 3}}


# ---- 新しい承認を生むプラン（I1） ----


@pytest.mark.usefixtures("issue_bodies")
@pytest.mark.parametrize("pace", ["normal", "auto", "fast"])
def test_only_the_design_plans_of_a_sprint_need_approval(tmp_path, pace):
    """組み立てたスプリントのプランのうち、設計のステージのプランだけが新しい承認を生む。設計 PR のマージ（`フェーズ: 設計`）は生まない。"""
    repo = make_repo(tmp_path, {"auto": AUTO, "fast": {"enabled": True, "verify": "true"}})
    p, out = new_sprint(tmp_path, repo, sprint_state(tmp_path, pace), pace=pace)
    assert p.returncode == 0, p.stdout + p.stderr
    stages = [s for s in load(out / "sprint.json")["ステージ"] if s.get("plans")]
    got = {(s["name"], Path(x).name): ph.needs_approval(load(x), None) for s in stages for x in s["plans"]}
    assert any(n == "設計" for n, _ in got) and len({n for n, _ in got}) > 2
    assert got == {k: k[0] == "設計" for k in got}


def test_the_rest_of_an_approved_design_plan_does_not_need_approval():
    """開始のステップから設計を書くステップへ行けなければ、設計のプランの続き（承認の後・差し戻しの記録）は承認を生まない。"""
    plan = {
        "steps": [
            {"id": "design", "type": "work", "kind": ph.DESIGN_KIND, "prompt": "書く"},
            {"id": "gate", "type": "run", "cmd": "true", "next": "end"},
            {"id": "approve", "type": "run", "cmd": "true", "from_only": True, "next": "end"},
        ]
    }
    assert ph.needs_approval(plan, None) and ph.needs_approval(plan, "design")
    assert not ph.needs_approval(plan, "approve") and not ph.needs_approval(plan, "gate")


# ---- 次に始めるプラン（決定 3・I5） ----


def put_plan(root: Path, name: str, body: dict, result: str | None = None) -> str:
    path = root / f"{name}.json"
    path.write_text(json.dumps(body, ensure_ascii=False))
    if result:
        (root / f"{name}-state").mkdir(exist_ok=True)
        (root / f"{name}-state" / "report.md").write_text(f"# 報告\n\n- 結果: {result}\n")
    return str(path)


def make_sprint(repo: Path, design: str | None, merge: str | None = None, issues=(11, 12), name="s1") -> Path:
    """設計 → 設計承認ゲート → 設計 PR のマージ の 3 ステージのスプリント。`design`・`merge` は各プランの結果（None なら未着手）。"""
    d = repo / ".ndf" / f"sprint-{name}"
    d.mkdir(parents=True)
    (d / "state.json").write_text(json.dumps({"name": name, "issues": list(issues)}))
    stages = [
        {"name": "設計", "plans": [put_plan(d, f"1-design-{i}", DESIGN, design) for i in issues]},
        {"name": "設計承認ゲート", "gate": "承認を取る"},
        {"name": "設計 PR のマージ", "plans": [put_plan(d, f"3-design-merge-{i}", MERGE, merge) for i in issues]},
    ]
    (d / "sprint.json").write_text(json.dumps({"スプリント": name, "ステージ": stages}, ensure_ascii=False))
    return d


@pytest.mark.parametrize(
    "design, merge, free",
    [
        (None, None, False),  # 受け入れ条件 5: 設計のステージが未着手 → 設計のプランだけ
        ("関門", None, True),  # 受け入れ条件 4: 設計承認ゲートの答えの後 → 設計 PR のマージ
        ("止まった", None, False),  # 設計のプランを流し直す
        ("関門", "完了", False),  # I5: 次に始めるプランが無い
    ],
)
def test_the_next_plans_decide_whether_a_free_plan_is_left(tmp_path, design, merge, free):
    sprint = make_sprint(tmp_path, design, merge)
    assert phs.find_sprint(tmp_path, [11, 12, 13]) == sprint
    assert any(not ph.needs_approval(load(p), None) for p in phs.next_plans(sprint)) is free


def test_the_sprint_is_found_by_the_lane_issues_and_the_newest_wins(tmp_path):
    make_sprint(tmp_path, None, issues=(11, 14), name="other")  # レーンの課題の外を含む
    old = make_sprint(tmp_path, None, name="old")
    new = make_sprint(tmp_path, "関門", name="new")
    os.utime(old / "sprint.json", (1, 1))
    assert phs.find_sprint(tmp_path, [11, 12]) == new
    assert phs.find_sprint(tmp_path, [11]) is None and phs.find_sprint(tmp_path, []) is None


def test_an_unreadable_sprint_has_no_next_plans(tmp_path):
    """I5: `sprint.json` が壊れていれば次に始めるプランは無い。"""
    sprint = make_sprint(tmp_path, "関門")
    (sprint / "sprint.json").write_text("{壊れた")
    assert phs.next_plans(sprint) == []


# ---- 答えの後に起こすか（受け入れ条件 4・5、I3） ----


@pytest.fixture
def plan(tmp_path, monkeypatch):
    d = tmp_path / ".ndf" / "parallel" / "p1"
    d.mkdir(parents=True)
    lanes = [{"name": "la", "input": {"kind": "sprint", "issues": [11, 12]}}]
    (d / "plan.json").write_text(json.dumps({"lanes": lanes}))
    (d / ph.HOLD_FILE).write_text(json.dumps(HOLD))
    monkeypatch.setattr(pa, "owner_lanes", lambda *_a: ["la"])
    monkeypatch.setattr(pa, "legacy_launch", lambda *_a: False)
    return tmp_path


def answer(main: Path):
    return pa.owner_next(main, "p1", "q1", None, [{"id": "q1", "kind": "ask", "lane": "la"}])


def test_a_held_sprint_lane_with_a_free_plan_is_woken_without_a_mark(plan, monkeypatch):
    """受け入れ条件 4: 次に始めるプランが設計 PR のマージなら、`held_owners` に載せず印を置かない。古い記録では再起動の 1 行を返す。"""
    make_sprint(plan, "関門")
    assert answer(plan) == (None, []) and ph.read_marks(plan, "p1") == {}
    monkeypatch.setattr(pa, "legacy_launch", lambda *_a: True)
    assert answer(plan)[0].endswith("relay.py restart la")


@pytest.mark.parametrize("design", [None, "止まった"])
def test_a_held_sprint_lane_with_only_design_plans_left_gets_a_mark(plan, design):
    """受け入れ条件 5: 次に始めるプランが設計のプランだけなら、今と同じく `held_owners` に載り印 `answered` が置かれる。"""
    make_sprint(plan, design)
    assert answer(plan) == (None, ["la"]) and ph.read_marks(plan, "p1")["la"]["why"] == "answered"


def test_a_held_sprint_lane_without_its_sprint_or_with_a_mark_stays_held(plan):
    """I5: スプリントが見つからなければ起こさない。I3: 印を持つレーン（区切りで止まった）は承認の要らないプランがあっても起こさない。"""
    assert answer(plan)[1] == ["la"]
    ph.drop_mark(plan, "p1", "la")
    make_sprint(plan, "関門")
    ph.place_mark(plan, "p1", "la", "boundary")
    assert answer(plan)[1] == ["la"] and ph.read_marks(plan, "p1")["la"]["why"] == "answered"


# ---- 耐久ワークフローの再起動（I4、決定 4） ----


@pytest.fixture
def run(monkeypatch):
    r = prun._Run("/m", "p1", [{"name": "la", "input": {"kind": "sprint"}}, {"name": "lb", "input": {"kind": "loop"}}])
    r.launched = []
    monkeypatch.setattr(durable, "set_event", lambda *_a: None)
    monkeypatch.setattr(plc, "step_launch", lambda _m, argv, lane, *_a: r.launched.append(lane) or {"result": "up", "attempts": 1})
    monkeypatch.setattr(r, "stopped", lambda: {"la", "lb"})
    r.hold = ph.Hold(True, (), 9)
    return r


@pytest.mark.parametrize("new, marked, woken", [(True, False, True), (True, True, False), (False, False, False)])
def test_a_held_sprint_lane_is_restarted_only_without_a_mark_in_new_records(run, new, marked, woken):
    """版の印の後の記録では、範囲の中の 1 スプリントのレーンを再開の印を持つときだけ打たない。前の記録ではすべて打たない。"""
    run.sprint_hold_wake = new
    run.marks = {"la": {"lane": "la", "why": "answered"}} if marked else {}
    run.wake = ["la"]
    run.launch_wakes()
    assert (run.launched == ["la"]) is woken and (run.wake == []) is woken
