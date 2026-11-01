"""開発ループ（lib/sprint_loop.py と sprint-state.py loop。#2089）。gh・claude・upkeep.py rank・supervise.py は差し替える。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import supervise_lib  # noqa: E402,F401  lib/ を sys.path へ足す
import pace  # noqa: E402
import project_mvv as pm  # noqa: E402
import sprint_loop as sl  # noqa: E402
import sprint_loop_gate as sl_gate  # noqa: E402
import sprint_loop_record as sl_record  # noqa: E402
import sprint_loop_select as sl_select  # noqa: E402

MVV_TEXT = "## Mission\n速く\n\n## Vision\n回る\n\n## Value\n実測\n"
DECL = {
    "version": 1,
    "auto": {"enabled": True, "verify": "true"},
    "loop": {"enabled": True, "budget": {"laps": 3, "usd": 60, "hours": 24}, "same_failure": 2, "max_issues": 2},
}


class Clock:
    def __init__(self):
        self.t = 0

    def __call__(self):
        self.t += 60
        h, m = divmod(self.t // 60, 60)
        d, h = divmod(h, 24)
        return f"2026-10-{25 + d:02d}T{h:02d}:{m:02d}:00Z"


@pytest.fixture
def mvv(monkeypatch):
    box = {"p": pm.ProjectMvv("approved", version=1, sha256="s1")}
    monkeypatch.setattr(sl.pm, "load_mvv", lambda root: box["p"])
    return box


def rank_out(*milestones, order=None):
    return {"metrics": {"order": order or [m["title"] for m in milestones], "milestones": list(milestones), "digest": "dg"}}


def ms(title, nums, boundary=None, sizes=None, **extra):
    rows = [{"rank": i, "number": n, "size": (sizes or {}).get(n, 1), "waiting_decision": False} for i, n in enumerate(nums, 1)]
    return {"title": title, "rows": rows, "boundary": {"number": boundary} if boundary else None, "large_slot": None, **extra}


@pytest.fixture
def env(tmp_path, mvv):
    (tmp_path / ".ndf").mkdir()
    (tmp_path / ".ndf" / "pace.json").write_text(json.dumps(DECL))
    e = sl.Env(tmp_path, main=tmp_path, now=Clock(), upkeep_dir=lambda repo: tmp_path / "upkeep")
    e.rank_box = {"out": rank_out(ms("01 近い", [11, 12, 13]))}
    e.rank = lambda root: (0, e.rank_box["out"])
    e.descs = {}
    e.milestones = lambda repo: e.descs
    e.prs = {}
    e.pr_state = lambda n: e.prs.get(n, "OPEN")
    e.bodies = {}
    e.issue_body = lambda n: e.bodies.get(n, "本文\n")
    e.edit_issue = lambda n, b: e.bodies.__setitem__(n, b)
    e.calls = []
    e.run = fake_run(e)
    return e


def fake_run(e):
    """sprint-state.py init・supervise.py new sprint / new close の差し替え。--out にマニフェストを書く。"""

    def run(cmd, cwd=None, stdin=None):
        e.calls.append(cmd)
        if "init" in cmd:
            Path(cmd[cmd.index("init") + 1]).write_text(
                json.dumps({"plans": [], "done": [], "gates": [], "goal_template": "", "versions": {}})
            )
            return 0, "{}", ""
        if "new" in cmd:
            out = Path(cmd[cmd.index("--out") + 1])
            out.mkdir(parents=True, exist_ok=True)
            kind = cmd[cmd.index("new") + 1]
            issues = [int(x) for x in cmd[cmd.index("--issue") + 1 :] if x.isdigit()][:5]
            if kind == "close":
                p = out / "1-close.json"
                p.write_text(json.dumps({"フェーズ": "まとめ", "課題": issues}))
                stages = [{"name": "まとめ", "plans": [str(p)], "command": f"queue {p}"}]
            else:
                plans = {}
                for n in issues:
                    for k, phase in (("design", "設計"), ("impl", "実装")):
                        plans[f"{k}-{n}"] = out / f"{k}-{n}.json"
                        plans[f"{k}-{n}"].write_text(json.dumps({"フェーズ": phase, "課題": [n]}))
                (out / "check.json").write_text(json.dumps({"フェーズ": "検査", "課題": issues}))
                (out / "prod.json").write_text(json.dumps({"フェーズ": "配布（本番）", "課題": issues}))
                design = [str(plans[f"design-{n}"]) for n in issues]
                stages = [
                    {"name": "設計", "plans": design, "command": "queue " + " ".join(design) + " --max 3"},
                    {"name": "関門 1", "gate": "..."},
                    {"name": "実装", "plans": [str(plans[f"impl-{n}"]) for n in issues], "then_of": "設計"},
                    {"name": "検査", "plans": [str(out / "check.json")], "then_of": "設計"},
                    {"name": "本番", "plans": [str(out / "prod.json")], "then_of": "設計"},
                ]
            (out / "sprint.json").write_text(json.dumps({"ステージ": stages}, ensure_ascii=False))
            return 0, "{}", ""
        return 0, "{}", ""

    return run


def start(env, name="l1", **budget):
    return sl.cmd_start(env, name, [], {"laps": None, "usd": None, "hours": None, **budget})


def rec(env, name="l1"):
    return sl.read_loop_record(env, name)


def set_state(env, lap, cost=1.0, seconds=10, result="完了", kind="実装", issues=(11,)):
    Path(lap["state_path"]).write_text(
        json.dumps(
            {
                "plans": [{"kind": kind, "issues": list(issues), "row": {"result": result, "cost": cost, "seconds": seconds}}],
                "versions": {"dev": "1.0.0-dev.1"},
                "gates": [],
            }
        )
    )


def run_lap(env, failed=False, result="完了", cost=1.0, kind="実装"):
    """1 周回: 選ぶ → 組む → 締める。"""
    got = sl_select.cmd_select(env, "l1")
    if got["status"] != "ok":
        return got
    sl.cmd_sprint(env, "l1", "standard", "1.0.0-dev.1", None, [])
    lap = sl.current_lap(rec(env))
    set_state(env, lap, cost=cost, result=result, kind=kind)
    return sl.cmd_lap(env, "l1", failed, None, [])


# ---------------- 受け入れ条件 2: 断る ----------------


@pytest.mark.parametrize(
    "decl, why",
    [
        ({**DECL, "loop": {"enabled": False}}, "loop.enabled"),
        ({k: v for k, v in DECL.items() if k != "auto"}, "auto 節"),
        ({**DECL, "loop": {"enabled": True, "max_issues": 0}}, "loop.max_issues"),
        ({**DECL, "loop": {"enabled": True, "budget": {"usd": -1}}}, "loop.budget"),
    ],
)
def test_start_refuses_without_permission_and_writes_nothing(env, decl, why):
    (env.root / ".ndf" / "pace.json").write_text(json.dumps(decl))
    out = start(env)
    assert out["exit"] == 1 and out["metrics"]["status"] == "refused"
    assert why in json.dumps(out["items"], ensure_ascii=False)
    assert "/ndf:development-workflow #" in out["next"]
    assert not (env.root / ".ndf" / "loop" / "l1" / "loop.json").exists()


def test_start_refuses_when_the_project_mvv_is_not_approved(env, mvv):
    mvv["p"] = pm.ProjectMvv("mismatch", version=1, sha256="x")
    out = start(env)
    assert out["exit"] == 1 and not (env.root / ".ndf" / "loop" / "l1").joinpath("loop.json").exists()


def test_broken_loop_section_does_not_change_read_pace(env):
    (env.root / ".ndf" / "pace.json").write_text(json.dumps({**DECL, "loop": "壊れた"}))
    assert pace.read_pace(env.root)["auto"]["enabled"] is True
    with pytest.raises(pace.PaceError):
        pace.read_loop(env.root)


def test_start_creates_the_record_handoff_and_laps(env):
    out = start(env)
    assert out["status"] == "ok" and out["metrics"]["status"] == "new"
    r = rec(env)
    assert r["budget"] == {"laps": 3, "usd": 60, "hours": 24} and r["max_issues"] == 2 and r["project_mvv"]["sha256"] == "s1"
    doc = (env.root / ".ndf" / "handoff" / "loop-l1.md").read_text()
    assert "--loop l1 -- 終わり: [ndf-loop] stopped l1" in doc
    assert (env.root / ".ndf" / "loop" / "l1" / "laps.md").is_file()


# ---------------- 受け入れ条件 4・5・I4: 選定 ----------------


def test_select_is_deterministic_and_stops_at_the_boundary_and_max_issues(env):
    env.rank_box["out"] = rank_out(ms("01 近い", [11, 12, 13, 14], boundary=13), ms("02 次", [21]))
    start(env)
    got = sl_select.cmd_select(env, "l1")
    assert got["metrics"]["selected"] == [11, 12] and got["metrics"]["milestone"] == "01 近い"
    rows = {r["number"]: r for r in got["metrics"]["excluded"]}
    assert rows[13]["reason"] == "容量" and rows[14]["reason"].startswith("順位")
    assert 21 not in rows  # 1 つのスプリントは 1 つのマイルストーンから
    again = sl_select.choose_issues(env, rec(env), env.rank_box["out"], 1, {})
    assert [r for r in again["selection"]] == rec(env)["laps"][0]["selection"]


def test_waiting_rows_do_not_use_up_max_issues_without_a_boundary(env):
    # 容量の宣言が無いと境界が無く、先頭から max_issues 件が候補になる。判断待ちの行で枠を埋めない（m2171 の後のループで 0 件になった）
    m = ms("01 近い", [11, 12, 13, 14, 15])
    for row in m["rows"][:2]:
        row["waiting_decision"] = True
    env.rank_box["out"] = rank_out(m)
    start(env)
    got = sl_select.cmd_select(env, "l1")
    assert got["metrics"]["selected"] == [13, 14]
    rows = {r["number"]: r["reason"] for r in got["metrics"]["excluded"]}
    assert rows[11] == rows[12] == "人の判断待ち（棚卸し）" and rows[15].startswith("順位")


def test_select_falls_through_to_the_next_milestone_and_marks_unapproved_sprint_mvv(env):
    env.rank_box["out"] = rank_out(ms("01 近い", [11]), ms("02 次", [21]))
    env.descs = {"01 近い": MVV_TEXT}
    start(env)
    got = sl_select.cmd_select(env, "l1")
    assert got["metrics"]["selected"] == [21]
    assert {r["number"]: r["reason"] for r in got["metrics"]["excluded"]}[11] == "スプリント MVV が未承認"


def test_approved_sprint_mvv_is_selected_and_copied_to_the_state(env):
    env.rank_box["out"] = rank_out(ms("01 近い", [11]))
    env.descs = {"01 近い": MVV_TEXT}
    start(env)
    assert sl.cmd_approve_mvv(env, "l1", "01 近い")["status"] == "ok"
    assert sl_select.cmd_select(env, "l1")["metrics"]["selected"] == [11]
    sl.cmd_sprint(env, "l1", "standard", "1.0.0-dev.1", None, [])
    state = json.loads(Path(sl.current_lap(rec(env))["state_path"]).read_text())
    assert [g["name"] for g in state["gates"]] == ["MVV"]


def test_quality_slot_and_large_slice_are_candidates(env):
    m = ms("01 近い", [11, 12, 13, 14], boundary=11, large_slot={"status": "当てた", "slice": 13}, quality_slot={"issues": [14]})
    m["rows"][3]["quality_slot"] = True
    env.rank_box["out"] = rank_out(m)
    start(env)
    r = rec(env)
    r["max_issues"] = 5
    sl.write_record(env, r)
    got = sl_select.cmd_select(env, "l1")
    assert got["metrics"]["selected"] == [11, 13, 14]
    assert {x["number"]: x["reason"] for x in rec(env)["laps"][0]["selection"]}[14] == "品質の枠"


def test_select_refuses_while_a_lap_is_in_progress(env):
    start(env)
    sl_select.cmd_select(env, "l1")
    assert sl_select.cmd_select(env, "l1")["exit"] == 1


# ---------------- 受け入れ条件 9・10: 選べない ----------------


def test_no_candidates_is_no_issue_and_all_excluded_is_outside_mvv(env):
    env.rank_box["out"] = rank_out(ms("01 近い", []))
    start(env)
    out = sl_select.cmd_select(env, "l1")
    assert out["exit"] == 3 and out["metrics"]["stop"]["condition"] == "no_issue" and rec(env)["laps"] == []
    assert out["metrics"]["stop_line"] == "[ndf-loop] stopped l1 no_issue"

    env.rank_box["out"] = rank_out(ms("01 近い", [11]))
    env.descs = {"01 近い": MVV_TEXT}
    start(env, "l2")
    out = sl_select.cmd_select(env, "l2")
    assert out["metrics"]["stop"]["condition"] == "outside_mvv" and out["metrics"]["stop"]["values"]["reasons"] == {
        "スプリント MVV が未承認": 1
    }


def test_exclude_redoes_the_selection_and_all_excluded_stops(env):
    env.rank_box["out"] = rank_out(ms("01 近い", [11, 12]))
    start(env)
    sl_select.cmd_select(env, "l1")
    out = sl_select.cmd_exclude(env, "l1", 11, "モードが対象外（operation）")
    assert out["metrics"]["selected"] == [12]
    out = sl_select.cmd_exclude(env, "l1", 12, "MVV に反する疑い")
    assert out["exit"] == 3 and out["metrics"]["stop"]["condition"] == "outside_mvv"


# ---------------- 受け入れ条件 7・I3: 予算 ----------------


@pytest.mark.parametrize("budget, cond", [({"laps": 1}, "budget_laps"), ({"usd": 2}, "budget_usd")])
def test_budget_is_judged_at_the_boundary_with_ge(env, budget, cond):
    start(env, **budget)
    out = run_lap(env, cost=2.0)
    assert out["exit"] == 3 and out["metrics"]["stop"]["condition"] == cond
    assert rec(env)["laps"][0]["outcome"] == "完了"


def test_budget_below_the_limit_continues_with_ndf_next(env):
    start(env)
    out = run_lap(env, cost=2.0)
    assert out["status"] == "ok" and "```ndf-next" in out["metrics"]["ndf_next"] and "--loop l1" in out["metrics"]["ndf_next"]


def test_stopped_time_inside_a_lap_is_not_counted_but_boundary_stops_are_not_subtracted():
    r = {
        "laps": [
            {"n": 1, "outcome": "完了", "started_at": "2026-10-25T00:00:00Z", "ended_at": "2026-10-26T01:00:00Z"},
            {"n": 2, "outcome": "完了", "started_at": "2026-10-27T00:00:00Z", "ended_at": "2026-10-27T01:00:00Z"},
        ],
        "stops": [
            {"condition": "gate2_returned", "at": "2026-10-25T00:30:00Z", "resumed_at": "2026-10-26T00:30:00Z"},  # 周回 1 の途中で一晩
            {"condition": "user_stop", "at": "2026-10-26T02:00:00Z", "resumed_at": "2026-10-26T23:00:00Z"},  # 境
        ],
    }
    assert sl.consumed(r)["hours"] == pytest.approx(2.0)


# ---------------- 受け入れ条件 8: 同じ失敗 ----------------


def test_same_failure_twice_stops_and_a_done_lap_resets(env):
    start(env, laps=10)
    assert run_lap(env, result="止まった")["status"] == "ok"
    assert run_lap(env)["status"] == "ok"  # 完了で数え直す
    assert run_lap(env, result="止まった")["status"] == "ok"
    out = run_lap(env, result="止まった")
    assert out["exit"] == 3 and out["metrics"]["stop"]["condition"] == "same_failure" and out["metrics"]["stop"]["values"]["count"] == 2


def test_after_resuming_from_same_failure_the_first_failure_does_not_stop(env):
    start(env, laps=10)
    run_lap(env, result="止まった")
    run_lap(env, result="止まった")
    assert sl.cmd_start(env, "l1", [], {})["metrics"]["status"] == "resumed"
    assert run_lap(env, result="止まった")["status"] == "ok"


def test_different_failure_keys_do_not_add_up(env):
    start(env, laps=10)
    run_lap(env, result="止まった", kind="実装")
    assert run_lap(env, result="止まった", kind="検査")["status"] == "ok"


# ---------------- 受け入れ条件 11・12・13・I7 ----------------


def test_stop_file_works_at_the_next_boundary_and_resume_removes_it(env):
    start(env)
    sl_select.cmd_select(env, "l1")
    sl.cmd_stop(env, "l1")
    assert sl.current_lap(rec(env))["outcome"] == "進行中"  # 途中では止めない
    sl.cmd_sprint(env, "l1", "standard", "1.0.0-dev.1", None, [])
    set_state(env, sl.current_lap(rec(env)))
    out = sl.cmd_lap(env, "l1", False, None, [])
    assert out["metrics"]["stop"]["condition"] == "user_stop"
    assert any("--loop l1" in i["resume"] for i in out["items"])
    doc = (env.root / ".ndf" / "handoff" / "loop-l1.md").read_text()
    assert "ndf-next\n/goal /ndf:development-workflow --loop l1" in doc
    assert sl_select.cmd_select(env, "l1")["exit"] == 3  # 止まったまま（I7）
    assert sl.cmd_start(env, "l1", [], {})["metrics"]["status"] == "resumed"
    assert not (env.root / ".ndf" / "loop" / "l1" / "stop").exists()
    assert run_lap(env)["status"] == "ok"
    r = rec(env)
    assert [lap["n"] for lap in r["laps"]] == [1, 2] and sl.consumed(r)["laps"] == 2


def test_budget_resume_needs_an_override(env):
    start(env, laps=1)
    run_lap(env)
    assert sl.cmd_start(env, "l1", [], {})["exit"] == 3
    out = sl.cmd_start(env, "l1", [], {"laps": 2})
    assert out["metrics"]["status"] == "resumed" and rec(env)["budget_history"][-1]["to"]["laps"] == 2


def test_mvv_change_stops_and_is_not_resumed(env, mvv):
    start(env)
    mvv["p"] = pm.ProjectMvv("approved", version=2, sha256="s2")
    out = sl_select.cmd_select(env, "l1")
    assert out["metrics"]["stop"]["condition"] == "mvv_changed"
    assert sl.cmd_start(env, "l1", [], {})["exit"] == 3


# ---------------- 受け入れ条件 3・17・I5・I6: 承認ゲート ----------------


def write_done(env, items):
    p = env.root / "done.json"
    p.write_text(json.dumps({"items": items}))
    return str(p)


def test_gate_1_moves_the_issue_to_waiting_and_continues_without_the_issue(env):
    env.rank_box["out"] = rank_out(ms("01 近い", [11, 12]))
    start(env)
    sl_select.cmd_select(env, "l1")
    sl.cmd_sprint(env, "l1", "standard", "1.0.0-dev.1", None, [])
    lap = sl.current_lap(rec(env))
    design11 = str(Path(lap["out"]) / "design-11.json")
    out = sl_gate.cmd_gate(env, "l1", write_done(env, [{"plan": design11, "result": "関門"}]))
    assert out["status"] == "ok" and out["metrics"]["waiting"] == [11]
    q = out["metrics"]["queue"]
    assert "impl-11.json" not in q and "impl-12.json" in q and "--done" in q and "loop lap l1" in out["metrics"]["pipeline"]
    assert "## 人の判断待ち（開発ループ l1）" in env.bodies[11]
    assert not any("--by" in c and "mvv" in c for c in env.calls)  # 承認ゲートを自分で通さない
    # 答えの前は選ばれない
    r = rec(env)
    assert sl_select.exclusion_reason(env, r, {"number": 11}, 2, set()) == "人の判断待ち"


def test_gate_1_with_every_issue_returned_closes_the_lap_and_continues(env):
    env.rank_box["out"] = rank_out(ms("01 近い", [11]))
    start(env)
    sl_select.cmd_select(env, "l1")
    sl.cmd_sprint(env, "l1", "standard", "1.0.0-dev.1", None, [])
    lap = sl.current_lap(rec(env))
    out = sl_gate.cmd_gate(env, "l1", write_done(env, [{"plan": str(Path(lap["out"]) / "design-11.json"), "result": "関門"}]))
    assert out["status"] == "ok" and rec(env)["laps"][0]["outcome"] == "人へ戻した" and "ndf_next" in out["metrics"]


def test_gate_2_stops_in_the_middle_of_the_lap(env):
    start(env)
    sl_select.cmd_select(env, "l1")
    sl.cmd_sprint(env, "l1", "standard", "1.0.0-dev.1", None, [])
    lap = sl.current_lap(rec(env))
    out = sl.cmd_lap(env, "l1", False, write_done(env, [{"plan": str(Path(lap["out"]) / "prod.json"), "result": "関門"}]), [])
    assert out["exit"] == 3 and out["metrics"]["stop"]["condition"] == "gate2_returned"
    assert sl.current_lap(rec(env)) is not None  # 周回は進行中のまま
    assert sl.cmd_start(env, "l1", [], {})["exit"] == 3  # 答えが無いうちは止まったまま


def built_lap(env, nums=(11, 12)):
    env.rank_box["out"] = rank_out(ms("01 近い", list(nums)))
    start(env)
    sl_select.cmd_select(env, "l1")
    sl.cmd_sprint(env, "l1", "standard", "1.0.0-dev.1", None, [])
    return sl.current_lap(rec(env))


def test_gate_1_drops_issues_whose_design_did_not_finish(env):
    (env.root / ".ndf" / "pace.json").write_text(json.dumps({**DECL, "loop": {**DECL["loop"], "max_issues": 3}}))
    lap = built_lap(env, (11, 12, 13))
    out = sl_gate.cmd_gate(
        env,
        "l1",
        write_done(
            env,
            [
                {"plan": str(Path(lap["out"]) / "design-11.json"), "result": "関門"},
                {"plan": str(Path(lap["out"]) / "design-12.json"), "result": "失敗"},
                {"plan": str(Path(lap["out"]) / "design-13.json"), "result": "完了"},
            ],
        ),
    )
    assert out["metrics"]["status"] == "continue" and out["metrics"]["waiting"] == [11]
    q = out["metrics"]["queue"]
    assert "impl-12.json" not in q and "impl-11.json" not in q and "impl-13.json" in q  # 設計と承認を経ない #12 を実装しない
    r = rec(env)
    assert [w["number"] for w in r["waiting"]] == [11] and r["laps"][-1]["issues_kept"] == [13]
    assert json.loads((Path(lap["out"]) / "check.json").read_text())["課題"] == [13]


def test_gate_1_closes_the_lap_as_failed_when_only_unfinished_designs_remain(env):
    lap = built_lap(env)
    items = [
        {"plan": str(Path(lap["out"]) / "design-11.json"), "result": "関門"},
        {"plan": str(Path(lap["out"]) / "design-12.json"), "result": "流さなかった"},
    ]
    sl_gate.cmd_gate(env, "l1", write_done(env, items))
    assert rec(env)["laps"][0]["outcome"] == sl.FAILED


def test_env_runs_gh_in_the_root_repository(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(sl_record, "_run_loop_cmd", lambda cmd, cwd=None, stdin=None: (seen.append((cmd[1:3], cwd)), (0, "[]", ""))[1])
    e = sl.Env(tmp_path)
    e.issue_body(3)
    e.edit_issue(3, "本文")
    e.pr_state(501)
    e.milestones("o/r")
    assert [c for _, c in seen] == [str(tmp_path)] * 4  # 別リポジトリの --root でも、呼んだ側のリポジトリの課題を触らない


def test_gate_refuses_when_done_has_no_gated_plan_and_writes_nothing(env):
    lap = built_lap(env)
    before = rec(env)
    out = sl_gate.cmd_gate(env, "l1", write_done(env, [{"plan": str(Path(lap["out"]) / "design-11.json"), "result": "完了"}]))
    assert out["exit"] == 1 and out["status"] == "stopped" and out["metrics"] == {"status": "refused"}
    assert rec(env) == before


def test_gate_stops_with_mvv_changed_when_the_project_mvv_was_revised(env, mvv):
    lap = built_lap(env)
    mvv["p"] = pm.ProjectMvv("approved", version=2, sha256="s2")
    out = sl_gate.cmd_gate(env, "l1", write_done(env, [{"plan": str(Path(lap["out"]) / "design-11.json"), "result": "関門"}]))
    stop = out["metrics"]["stop"]
    assert out["exit"] == 3 and stop["condition"] == "mvv_changed" and stop["values"] == {"started": "s1", "now": "s2"}
    r = rec(env)
    assert r["status"] == "stopped" and r["stop"]["condition"] == "mvv_changed" and r["waiting"] == []


def test_gate_stops_with_the_mvv_status_when_the_project_mvv_is_not_approved(env, mvv):
    lap = built_lap(env)
    mvv["p"] = pm.ProjectMvv("draft")
    out = sl_gate.cmd_gate(env, "l1", write_done(env, [{"plan": str(Path(lap["out"]) / "design-11.json"), "result": "関門"}]))
    assert out["exit"] == 3 and out["metrics"]["stop"]["values"] == {"started": "s1", "now": "draft"}


def test_gate_2_records_the_gate_copy_and_the_stop_values(env):
    lap = built_lap(env)
    out = sl_gate.cmd_gate(env, "l1", write_done(env, [{"plan": str(Path(lap["out"]) / "prod.json"), "result": "関門"}]))
    assert out["exit"] == 3 and out["metrics"]["gate"] == 2
    stop = out["metrics"]["stop"]
    assert stop["condition"] == "gate2_returned" and stop["values"] == {"pr": None, "verdict": "unknown", "reasons": []}
    r = rec(env)
    gates = r["laps"][-1]["gates"]
    assert gates == [{"name": "関門 2", "by": "mvv", "verdict": "unknown", "basis": [], "pr": None, "at": ""}]
    assert r["waiting"] == [] and r["laps"][-1]["outcome"] == "進行中"


def test_gate_1_mixed_with_a_release_plan_is_treated_as_gate_2(env):
    lap = built_lap(env)
    items = [
        {"plan": str(Path(lap["out"]) / "design-11.json"), "result": "関門"},
        {"plan": str(Path(lap["out"]) / "prod.json"), "result": "関門"},
    ]
    out = sl_gate.cmd_gate(env, "l1", write_done(env, items))
    assert out["exit"] == 3 and out["metrics"]["stop"]["condition"] == "gate2_returned" and rec(env)["waiting"] == []


def test_gate_1_stops_with_refused_when_the_continuation_close_is_refused(env):
    lap = built_lap(env)
    base = env.run

    def run(cmd, cwd=None, stdin=None):
        if "new" in cmd and cmd[cmd.index("new") + 1] == "close":
            env.calls.append(cmd)
            return 2, "", "断った理由"
        return base(cmd, cwd=cwd, stdin=stdin)

    env.run = run
    out = sl_gate.cmd_gate(env, "l1", write_done(env, [{"plan": str(Path(lap["out"]) / "design-11.json"), "result": "関門"}]))
    stop = out["metrics"]["stop"]
    assert out["exit"] == 3 and stop["condition"] == "refused"
    assert stop["values"]["step"] == "続きの queue" and stop["values"]["exit"] == 1 and "断った理由" in stop["values"]["summary"]
    assert "周回を組めなかった（続きの queue）" in out["summary"]
    r = rec(env)
    assert r["status"] == "stopped" and [w["number"] for w in r["waiting"]] == [11]  # before は検査用。移した課題は記録に残る


def test_gate_1_keeps_going_and_notes_when_the_issue_body_cannot_be_written(env):
    lap = built_lap(env)

    def fail(n):
        raise OSError("gh が落ちた")

    env.issue_body = fail
    out = sl_gate.cmd_gate(env, "l1", write_done(env, [{"plan": str(Path(lap["out"]) / "design-11.json"), "result": "関門"}]))
    assert out["status"] == "ok" and out["metrics"]["status"] == "continue" and out["metrics"]["gate"] == 1
    assert {"note": "gh が落ちた"} in out["items"] and {"waiting": [11]} in out["items"]
    r = rec(env)
    assert [w["number"] for w in r["waiting"]] == [11] and r["laps"][-1]["issues_kept"] == [12]
    assert r["laps"][-1]["gates"][0]["name"] == "関門 1" and r["laps"][-1]["dones"][-1].endswith("done-2.json")


def test_answers_control_reselection(env):
    env.rank_box["out"] = rank_out(ms("01 近い", [11, 12]))
    start(env)
    sl_select.cmd_select(env, "l1")
    sl.cmd_sprint(env, "l1", "standard", "1.0.0-dev.1", None, [])
    lap = sl.current_lap(rec(env))
    sl_gate.cmd_gate(env, "l1", write_done(env, [{"plan": str(Path(lap["out"]) / f"design-{n}.json"), "result": "関門"} for n in (11, 12)]))
    assert sl.cmd_answer(env, "l1", 11, True, 501)["status"] == "ok"
    assert sl.cmd_answer(env, "l1", 12, False, None)["status"] == "ok"
    assert sl.cmd_answer(env, "l1", 12, True, None)["exit"] == 1  # もう待っていない
    r = rec(env)
    assert sl_select.exclusion_reason(env, r, {"number": 11}, 2, set()) == "設計 PR がマージ待ち"
    assert sl_select.exclusion_reason(env, r, {"number": 12}, 2, set()) == "差し戻し"
    env.prs[501] = "MERGED"
    assert sl_select.exclusion_reason(env, r, {"number": 11}, 2, set()) is None and r["waiting"][0]["merged_at"]
    assert any(c[2:4] == ["gate", lap["state_path"]] and "approved" in c for c in env.calls)
    # 選び直した周回では、承認済みの #11 を --design から外して --designed で設計の結果を読む
    argv = sl.new_sprint_argv(str(env.root), r, {**lap, "selection": [{"number": n, "decision": "選んだ"} for n in (11, 13)]}, [])
    assert argv[argv.index("--design") + 1 :][:1] == ["13"] and argv[argv.index("--designed") + 1] == "11"


def test_created_issues_are_not_selected_in_the_same_lap(env):
    start(env)
    r = rec(env)
    assert sl_select.exclusion_reason(env, r, {"number": 99}, 1, {99}).startswith("周回の中で起票された")


# ---------------- 受け入れ条件 14・15・16・21 ----------------


def test_lap_copies_values_carry_reaches_the_next_close_and_list_shows_laps(env):
    (env.root / "upkeep").mkdir()
    (env.root / "upkeep" / "candidates.json").write_text(json.dumps({"metrics": {"deferred": [31, 32]}}))
    (env.root / "upkeep" / "rank.json").write_text(json.dumps({"metrics": {"changes": [{"number": 11}]}}))
    start(env)
    sl_select.cmd_select(env, "l1")
    sl.cmd_sprint(env, "l1", "standard", "1.0.0-dev.1", None, [])
    set_state(env, sl.current_lap(rec(env)), cost=1.5, seconds=20)
    sl.cmd_lap(env, "l1", False, None, [41, 42])
    lap = rec(env)["laps"][0]
    assert lap["cost"] == 1.5 and lap["seconds"] == 20 and lap["versions"]["dev"] == "1.0.0-dev.1"
    assert lap["refine"]["deferred"] == [31, 32] and lap["refine"]["rank_changes"] == [{"number": 11}]
    assert lap["deferred_issues"] == [41, 42]
    sl_select.cmd_select(env, "l1")
    out = sl.cmd_sprint(env, "l1", "standard", "1.0.0-dev.2", None, [])
    assert "--carry 31 32" in out["metrics"]["close_command"] and "--pace auto" in out["metrics"]["close_command"]
    assert "1-close.json" in out["metrics"]["queue"].split("--then")[-1]
    assert out["metrics"]["pipeline"].count("&&") == 2 and "wait" in out["metrics"]["pipeline"]
    rows = sl.cmd_list(env, "l1")["items"]
    assert rows[0]["n"] == 1 and rows[0]["issues"] == [11, 12] and rows[0]["deferred_issues"] == [41, 42]
    assert "#41 #42" in (env.root / ".ndf" / "loop" / "l1" / "laps.md").read_text()


def test_new_sprint_refusal_stops_with_refused_and_resume_retries(env):
    start(env)
    sl_select.cmd_select(env, "l1")
    env.run = lambda cmd, cwd=None, stdin=None: (1, "", "断った")
    out = sl.cmd_sprint(env, "l1", "standard", "1.0.0-dev.1", None, [])
    assert out["exit"] == 3 and out["metrics"]["stop"]["condition"] == "refused"
    res = sl.cmd_start(env, "l1", [], {})
    assert res["metrics"]["status"] == "resumed" and "loop sprint l1" in res["metrics"]["next"]


# ---------------- I1 ----------------


def test_closed_laps_are_not_rewritten(env):
    start(env)
    run_lap(env)
    r = rec(env)
    before = json.loads(json.dumps(r))
    r["laps"][0]["cost"] = 99
    with pytest.raises(sl.LoopError):
        sl.write_record(env, r, before)
    with pytest.raises(sl.LoopError):
        sl.close_lap(env, r, r["laps"][0], "完了")


# 現状固定: render_laps の出力（全分岐を通す記録と、空の記録）
def test_render_laps_characterizes_every_section():
    import sprint_loop_record as slr

    rec = {
        "name": "l1",
        "status": "stopped",
        "budget": {"laps": 3},
        "goal": True,
        "laps": [
            {
                "n": 1,
                "sprint": "s1",
                "outcome": slr.DONE,
                "cost": 1.5,
                "seconds": 10,
                "started_at": "2026-10-25T00:00:00Z",
                "ended_at": "2026-10-25T00:10:00Z",
                "versions": {"dev": "1.0.0-dev.1", "prod": "1.0.0"},
                "deferred_issues": [7, 8],
                "selection": [
                    {"number": 3, "milestone": "01 a|b", "rank": 1, "decision": slr.SELECTED, "reason": "r"},
                    {"number": 4, "decision": slr.DROPPED},
                ],
                "gates": [{"name": "gate1", "verdict": "ok", "basis": ["x", "y"]}, {"name": "gate2", "by": "mvv"}],
                "refine": {"deferred": [5], "rank_changes": [1, 2]},
                "retro": "良い",
            },
            {"n": 2, "outcome": slr.IN_PROGRESS},
        ],
        "waiting": [{"number": 9, "gate": "gate1", "reason": "why"}],
        "stops": [{"lap": 1, "condition": "budget"}],
        "stop": {"condition": "same_failure", "values": {"k": "失敗"}, "lap": 2},
    }
    assert slr.render_laps(rec) == (
        "# 開発ループ l1\n"
        "\n"
        "- 状態: stopped。予算: {'laps': 3}。消費: {'laps': 1, 'usd': 1.5, 'hours': 0.166667}\n"
        "\n"
        "| 周回 | 課題 | 版 | 費用 | 秒 | 結果 | 止まった理由 | 後へ回した Issue |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| 1 | #3 | 1.0.0-dev.1 / 1.0.0 | 1.5 | 10 | 完了 | budget | #7 #8 |\n"
        "| 2 | — | — | — | — | 進行中 | same_failure | — |\n"
        "\n"
        "## 周回 1（s1）\n"
        "\n"
        "| 課題 | マイルストーン | 順位 | 選定 | 理由 |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| #3 | 01 a\\|b | 1 | 選んだ | r |\n"
        "| #4 | — | — | 外した | — |\n"
        "\n"
        "- gate1: user ok（根拠 x, y）\n"
        "\n"
        "- gate2: mvv —（根拠 —）\n"
        "- 棚卸し: 持ち越し [5]・順位の変化 2 件\n"
        "- 振り返り: 良い\n"
        "\n"
        "## 周回 2（）\n"
        "\n"
        "| 課題 | マイルストーン | 順位 | 選定 | 理由 |\n"
        "| --- | --- | --- | --- | --- |\n"
        "\n"
        "## 人の判断待ち\n"
        "\n"
        "| 課題 | 承認ゲート | 理由 | 答え |\n"
        "| --- | --- | --- | --- |\n"
        "| #9 | gate1 | why | — |\n"
        "\n"
        "## 止まった理由: same_failure\n"
        "\n"
        '- 値: {"k": "失敗"}\n'
        "- 再開: `/goal /ndf:development-workflow --loop l1 -- 終わり: [ndf-loop] stopped l1 の行を出したとき`\n"
    )
    assert slr.render_laps({"name": "e", "goal": False}) == (
        "# 開発ループ e\n"
        "\n"
        "- 状態: None。予算: None。消費: {'laps': 0, 'usd': 0, 'hours': 0.0}\n"
        "\n"
        "| 周回 | 課題 | 版 | 費用 | 秒 | 結果 | 止まった理由 | 後へ回した Issue |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
    )


# ---------------- CLI ----------------


def test_cli_status_reports_a_missing_record_and_start_refuses(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".ndf").mkdir()
    sp = SCRIPTS / "sprint-state.py"
    p = subprocess.run([sys.executable, str(sp), "loop", "status", "x1", "--json", "--root", str(tmp_path)], capture_output=True, text=True)
    assert p.returncode == 1 and json.loads(p.stdout)["status"] == "stopped"
    p = subprocess.run([sys.executable, str(sp), "loop", "start", "x1", "--root", str(tmp_path)], capture_output=True, text=True)
    assert p.returncode == 1 and json.loads(p.stdout)["metrics"]["status"] == "refused"


# ---------------- #2103 の指摘 ----------------


def test_cli_sprint_reads_options_after_the_loop_name_and_passes_only_what_follows_the_separator():
    import argparse

    import sprint_loop_cli

    ap = argparse.ArgumentParser()
    sprint_loop_cli.add_loop_parser(ap.add_subparsers(dest="cmd"), lambda a: None)
    head, extra = sprint_loop_cli.split_extra(["loop", "sprint", "l1", "--mode", "standard", "--dev", "1.0.0-dev.1"])
    a = ap.parse_args(head)
    assert extra is None and (a.loop_name, a.mode, a.dev, a.extra) == ("l1", "standard", "1.0.0-dev.1", [])
    head, extra = sprint_loop_cli.split_extra(["loop", "sprint", "l1", "--mode", "fast", "--dev", "2.0.0-dev.1", "--", "--design", "11"])
    a = ap.parse_args(head)
    assert (a.mode, a.dev, extra) == ("fast", "2.0.0-dev.1", ["--design", "11"])


def test_gate_1_drops_the_waiting_issue_from_the_shared_plan_and_its_closes(env):
    lap = built_lap(env)
    check = Path(lap["out"]) / "check.json"
    check.write_text(json.dumps({"フェーズ": "検査", "課題": [11, 12], "steps": [{"id": "pr", "materials": {"closes": [11, 12]}}]}))
    out = sl_gate.cmd_gate(env, "l1", write_done(env, [{"plan": str(Path(lap["out"]) / "design-11.json"), "result": "関門"}]))
    assert out["status"] == "ok" and "check.json" in out["metrics"]["queue"]
    got = json.loads(check.read_text())
    assert got["課題"] == [12] and got["steps"][0]["materials"]["closes"] == [12]


def design_report(env, name, pr_url):
    p = env.root / f"{name}-report.md"
    p.write_text(f"# 報告\n\n- 結果: 関門\n- Pull Request: {pr_url}\n")
    return str(p)


def test_gate_1_takes_the_verdict_of_the_plan_design_pr_and_saves_the_pr_as_a_number(env, monkeypatch):
    lap = built_lap(env)
    log = env.root / "mvv-gate.jsonl"
    rows = [
        {
            "gate": "design",
            "sprint": lap["state_path"],
            "pr": [501],
            "verdict": "従わない",
            "reasons": ["11 の理由"],
            "material": ["#501 a.md"],
        },
        {
            "gate": "design",
            "sprint": lap["state_path"],
            "pr": [502],
            "verdict": "従う",
            "reasons": ["12 の理由"],
            "material": ["#502 b.md"],
        },
    ]
    log.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    monkeypatch.setattr(sl_gate.pm, "gate_log_path", lambda: log)
    item = {
        "plan": str(Path(lap["out"]) / "design-11.json"),
        "result": "関門",
        "report": design_report(env, "d11", "https://github.com/o/r/pull/501"),
    }
    assert sl_gate.cmd_gate(env, "l1", write_done(env, [item]))["status"] == "ok"
    w = rec(env)["waiting"][0]
    assert (w["number"], w["pr"], w["reason"], w["verdict"]) == (11, 501, "11 の理由", "従わない")
    env.calls.clear()
    sl.cmd_answer(env, "l1", 11, True, None)
    gate = next(c for c in env.calls if "gate" in c)
    assert gate[gate.index("--pr") + 1] == "501"
    assert sl_select.exclusion_reason(env, rec(env), {"number": 11}, 2, set()) == "設計 PR がマージ待ち"


def approve_gate_2(lap):
    state = json.loads(Path(lap["state_path"]).read_text())
    state["gates"].append({"name": "関門 2", "by": "user", "outcome": "approved", "at": "2026-12-01T00:00:00Z"})
    Path(lap["state_path"]).write_text(json.dumps(state))


@pytest.mark.parametrize(
    "plan, steps, want",
    [
        ("prod.json", ["mvv", "note", "bump", "release"], "--from bump"),
        ("prod.json", ["verify", "prepare", "mvv", "note", "promote", "promote-approved", "record"], "--from promote-approved"),
    ],
)
def test_gate_2_resume_continues_from_the_step_of_the_route(env, plan, steps, want):
    lap = built_lap(env)
    p = Path(lap["out"]) / plan
    p.write_text(json.dumps({"フェーズ": "配布（本番）", "課題": [11, 12], "steps": [{"id": s} for s in steps]}))
    assert sl_gate.cmd_gate(env, "l1", write_done(env, [{"plan": str(p), "result": "関門"}]))["exit"] == 3
    approve_gate_2(sl.current_lap(rec(env)))
    nxt = sl.cmd_start(env, "l1", [], {})["metrics"]["next"]
    assert f"run {p} {want}" in nxt and "loop lap l1" in nxt


def test_gate_2_at_the_merge_of_the_check_plan_also_runs_the_rest_of_the_queue(env):
    lap = built_lap(env)
    check = Path(lap["out"]) / "check.json"
    check.write_text(json.dumps({"フェーズ": "検査", "課題": [11, 12], "steps": [{"id": "merge-gate"}, {"id": "merge-approved"}]}))
    assert sl_gate.cmd_gate(env, "l1", write_done(env, [{"plan": str(check), "result": "関門"}]))["exit"] == 3
    approve_gate_2(sl.current_lap(rec(env)))
    nxt = sl.cmd_start(env, "l1", [], {})["metrics"]["next"]
    run, queue = nxt.split(" && ")[:2]
    assert run.endswith(f"run {check} --from merge-approved")
    assert "prod.json" in queue.split("--then")[0] and "close" in queue.split("--then")[-1]


def test_lap_with_a_gate_updates_the_sprint_state_before_closing(env):
    lap = built_lap(env, nums=(11,))
    seen = []
    done = write_done(env, [{"plan": str(Path(lap["out"]) / "design-11.json"), "result": "関門"}])
    out = sl.cmd_lap(env, "l1", False, done, [], update=lambda state, dones: seen.append(state))
    assert seen == [lap["state_path"]] and rec(env)["laps"][0]["outcome"] == "人へ戻した" and out["status"] == "ok"


def test_gate_updates_the_sprint_state_before_closing(env):
    lap = built_lap(env, nums=(11,))
    seen = []
    done = write_done(env, [{"plan": str(Path(lap["out"]) / "design-11.json"), "result": "関門"}])
    out = sl_gate.cmd_gate(env, "l1", done, update=lambda state, dones: seen.append(state))
    assert seen == [lap["state_path"]] and rec(env)["laps"][0]["outcome"] == "人へ戻した" and out["status"] == "ok"


def test_quality_slot_is_kept_inside_max_issues(env):
    m = ms("01 近い", [11, 12, 13, 14], quality_slot={"issues": [14]})
    m["rows"][3]["quality_slot"] = True
    env.rank_box["out"] = rank_out(m)
    start(env)
    r = rec(env)
    r["max_issues"] = 3
    sl.write_record(env, r)
    got = sl_select.cmd_select(env, "l1")
    assert got["metrics"]["selected"] == [11, 12, 14]
    assert {x["number"]: x["reason"] for x in rec(env)["laps"][0]["selection"]}[13] == "容量"


def test_handoff_keeps_the_concrete_next_step_for_resuming_inside_a_lap(env):
    start(env)
    sl_select.cmd_select(env, "l1")
    doc = sl.handoff_path(env, "l1")
    assert "loop sprint l1 --mode <モード>" in doc.read_text().split("## 次にやること")[1].split("## ")[0]
    out = sl.cmd_sprint(env, "l1", "standard", "1.0.0-dev.1", None, [])
    body = doc.read_text().split("## 次にやること")[1].split("## ")[0]
    assert out["metrics"]["pipeline"] in body


def test_gate_2_answer_ignores_an_approval_from_before_the_stop(env):
    lap = built_lap(env)
    state = json.loads(Path(lap["state_path"]).read_text())
    state["gates"].append({"name": "関門 2", "by": "user", "outcome": "approved", "at": "2026-10-01T00:00:00Z"})  # 前の関門への承認
    Path(lap["state_path"]).write_text(json.dumps(state))
    p = Path(lap["out"]) / "prod.json"
    assert sl_gate.cmd_gate(env, "l1", write_done(env, [{"plan": str(p), "result": "関門"}]))["exit"] == 3
    assert sl.cmd_start(env, "l1", [], {})["exit"] == 3  # 止まった後の答えはまだ無い
    approve_gate_2(sl.current_lap(rec(env)))
    assert sl.cmd_start(env, "l1", [], {})["status"] == "ok"


def test_manual_release_stage_holds_the_close_until_it_is_done(env):
    base = env.run

    def run(cmd, cwd=None, stdin=None):
        got = base(cmd, cwd, stdin)
        if "new" in cmd and cmd[cmd.index("new") + 1] == "sprint":
            m = Path(cmd[cmd.index("--out") + 1]) / "sprint.json"
            index = json.loads(m.read_text())
            index["ステージ"].append({"name": "リリース", "manual": "/ndf:release", "note": "手で届ける"})
            m.write_text(json.dumps(index, ensure_ascii=False))
        return got

    env.run = run
    start(env)
    sl_select.cmd_select(env, "l1")
    out = sl.cmd_sprint(env, "l1", "standard", "1.0.0-dev.1", None, [])
    pipe = out["metrics"]["pipeline"]
    first, rest = pipe.split("を終えてから: ")
    assert "/ndf:release" in first and "close" not in first.split(" を流し")[0]
    assert rest.startswith("python3 ") and "1-close.json" in rest and "loop lap l1" in rest


def test_sprint_pipeline_chains_close_after_the_first_command_without_hands(tmp_path):
    done, rec_ = tmp_path / "done-1.json", {"name": "l1"}
    index = [{"name": "設計", "gate": True}, {"name": "実装", "command": "Q1"}]
    queue, pipeline = sl.sprint_pipeline(index, ["c1", "c 2"], done, rec_)
    assert queue == f"Q1 --then c1 'c 2' --done {done}"
    assert pipeline == f"{queue} && {sl_gate.wait_lap(rec_, done)}"


def test_sprint_pipeline_waits_for_hands_before_the_close(tmp_path):
    done, rec_ = tmp_path / "done-1.json", {"name": "l1"}
    index = [{"name": "実装", "command": "Q1"}, {"name": "ゲート 2", "command": "Q2"}, {"name": "リリース", "manual": "/ndf:release"}]
    queue, pipeline = sl.sprint_pipeline(index, ["c1"], done, rec_)
    assert queue == f"python3 {sl.SUPERVISE} queue c1 --max 3 --done {done}"
    assert pipeline == f"Q1 を流し、続けて Q2 → リリース（/ndf:release で行う） を終えてから: {queue} && {sl_gate.wait_lap(rec_, done)}"


def test_refused_continuation_after_gate_1_is_rebuilt_from_the_kept_issues(env, monkeypatch):
    lap = built_lap(env)
    real = sl_gate.continuation
    monkeypatch.setattr(sl_gate, "continuation", lambda *a: (_ for _ in ()).throw(ValueError("組めない")))
    item = {"plan": str(Path(lap["out"]) / "design-11.json"), "result": "関門"}
    out = sl_gate.cmd_gate(env, "l1", write_done(env, [item]))
    assert out["exit"] == 3 and rec(env)["stop"]["values"]["step"] == sl_gate.CONTINUATION_STEP
    assert sl.cmd_sprint(env, "l1", "standard", "1.0.0-dev.1", None, [])["exit"] == sl.EXIT_REFUSED  # 全課題で組み直さない
    monkeypatch.setattr(sl_gate, "continuation", real)
    got = sl.cmd_start(env, "l1", [], {})
    assert got["status"] == "ok" and "loop lap l1" in got["metrics"]["next"]
    close = next(c for c in reversed(env.calls) if "close" in c)
    assert close[close.index("--issue") + 1 : close.index("--state")] == ["12"]


def test_gate_2_resume_builds_the_close_without_the_check_pr_as_the_record(env):
    """#2230 の受け入れ条件 2: 承認ゲート 2 の後のまとめへ、止まった記録の values.pr（検査のスプリント PR）を --record-pr で渡さない。"""
    lap = built_lap(env)
    assert sl_gate.cmd_gate(env, "l1", write_done(env, [{"plan": str(Path(lap["out"]) / "prod.json"), "result": "関門"}]))["exit"] == 3
    r = rec(env)
    r["stop"]["values"]["pr"] = 2103
    sl.write_record(env, r)
    approve_gate_2(sl.current_lap(rec(env)))
    env.calls.clear()
    nxt = sl.cmd_start(env, "l1", [], {})["metrics"]["next"]
    closes = [c for c in env.calls if "new" in c and "close" in c]
    assert closes and all("--record-pr" not in c for c in closes) and "loop lap l1" in nxt


def test_lap_with_an_unfilled_close_is_closed_as_failed(env):
    """#2230 の決定 7: 差し込みを埋められずに流さなかったまとめ（記録の PR が見つからない）がある周回は完了で締めない。"""
    lap = built_lap(env, nums=(11,))
    set_state(env, lap)
    items = [
        {"plan": str(Path(lap["out"]) / "check.json"), "result": "完了"},
        {
            "plan": str(Path(lap["out"]) / "close" / "1-close.json"),
            "result": "流さなかった",
            "reason": "{queue_record}（…）",
            "unfilled": True,
        },
    ]
    sl.cmd_lap(env, "l1", False, write_done(env, items), [])
    assert rec(env)["laps"][0]["outcome"] == sl.FAILED


def test_lap_with_a_plan_skipped_after_a_finished_stage_is_still_done(env):
    lap = built_lap(env, nums=(11,))
    set_state(env, lap)
    items = [
        {"plan": str(Path(lap["out"]) / "check.json"), "result": "完了"},
        {"plan": str(Path(lap["out"]) / "prod.json"), "result": "流さなかった", "reason": "前のステージを流さなかった"},
    ]
    sl.cmd_lap(env, "l1", False, write_done(env, items), [])
    assert rec(env)["laps"][0]["outcome"] == sl.DONE


# ---------------------------------------------------------------- gate2_answer の現状固定（I-004）


def g2_lap(tmp_path, gates=None):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"gates": gates or []}))
    return {"state_path": str(state)}, str(state.resolve())


def g2_ledger(*rows, raw=None):
    import gate_rejections as gr

    p = gr.ledger_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(raw if raw is not None else "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def test_gate2_answer_is_none_without_a_state_path_or_answers(tmp_path):
    assert sl.gate2_answer({}) is None
    assert sl.gate2_answer({}, "2026-10-01T00:00:00Z") is None
    lap, _ = g2_lap(tmp_path)
    assert sl.gate2_answer(lap) is None
    assert sl.gate2_answer({"state_path": str(tmp_path / "missing.json")}) is None


def test_gate2_answer_reads_user_approvals_of_gate_2_only_after_since(tmp_path):
    gates = [
        {"name": "関門 2", "by": "mvv", "at": "2026-10-05T00:00:00Z"},  # MVV の通過は数えない
        {"name": "関門 1", "by": "user", "at": "2026-10-05T00:00:00Z"},  # 別の関門は数えない
        {"name": "関門 2", "by": "user", "at": "2026-10-01T00:00:00Z"},  # since より前
    ]
    lap, _ = g2_lap(tmp_path, gates)
    assert sl.gate2_answer(lap, "2026-10-02T00:00:00Z") is None
    assert sl.gate2_answer(lap, "2026-10-01T00:00:00Z") == "approved"  # 同じ時刻は数える
    assert sl.gate2_answer(lap) == "approved"
    lap2, _ = g2_lap(tmp_path, [{"name": "関門 2", "by": "user"}])  # 時刻の無い答え
    assert sl.gate2_answer(lap2) == "approved"
    assert sl.gate2_answer(lap2, "2026-10-01T00:00:00Z") is None


def test_gate2_answer_reads_rejections_of_the_same_sprint_from_the_ledger(tmp_path):
    lap, sp = g2_lap(tmp_path)
    g2_ledger(
        {"gate": "関門 2", "sprint": sp, "at": "2026-10-03T00:00:00Z"},  # outcome の無い行は差し戻し
        {"gate": "関門 2", "sprint": sp, "at": "2026-10-09T00:00:00Z", "outcome": "approved"},  # 承認の行は読まない
        {"gate": "関門 1", "sprint": sp, "at": "2026-10-09T00:00:00Z"},
        {"gate": "関門 2", "sprint": "/other.json", "at": "2026-10-09T00:00:00Z"},
    )
    assert sl.gate2_answer(lap, "2026-10-02T00:00:00Z") == "rejected"
    assert sl.gate2_answer(lap, "2026-10-04T00:00:00Z") is None
    assert sl.gate2_answer(lap) == "rejected"


def test_gate2_answer_takes_the_latest_answer(tmp_path):
    lap, sp = g2_lap(tmp_path, [{"name": "関門 2", "by": "user", "at": "2026-10-05T00:00:00Z"}])
    g2_ledger({"gate": "関門 2", "sprint": sp, "at": "2026-10-04T00:00:00Z", "outcome": "rejected"})
    assert sl.gate2_answer(lap, "2026-10-01T00:00:00Z") == "approved"
    g2_ledger({"gate": "関門 2", "sprint": sp, "at": "2026-10-06T00:00:00Z", "outcome": "rejected"})
    assert sl.gate2_answer(lap, "2026-10-01T00:00:00Z") == "rejected"
    g2_ledger({"gate": "関門 2", "sprint": sp, "at": "2026-10-05T00:00:00Z", "outcome": "rejected"})  # 同じ時刻は先の承認
    assert sl.gate2_answer(lap, "2026-10-01T00:00:00Z") == "approved"


def test_gate2_answer_treats_a_broken_ledger_as_empty(tmp_path):
    lap, _ = g2_lap(tmp_path, [{"name": "関門 2", "by": "user", "at": "2026-10-05T00:00:00Z"}])
    g2_ledger(raw="{壊れた行\n")
    assert sl.gate2_answer(lap, "2026-10-01T00:00:00Z") == "approved"
    (tmp_path / "x").mkdir()
    lap2, _ = g2_lap(tmp_path / "x")
    assert sl.gate2_answer(lap2) is None


# ---------------- 現状固定: resume_stopped の止まった理由ごとの分岐 ----------------


def stopped_on(env, cond, **stop):
    """記録を cond で止まった状態にして返す（resume_stopped を直接通すため）。"""
    r = rec(env)
    r["status"], r["stop"] = "stopped", {"condition": cond, "at": "2026-10-25T00:00:00Z", "values": {}, **stop}
    sl.write_record(env, r)
    return rec(env)


def test_resume_stopped_mvv_changed_stays_stopped_and_points_to_a_new_loop(env):
    start(env)
    r = stopped_on(env, "mvv_changed")
    out = sl.resume_stopped(env, r, json.loads(json.dumps(r)))
    assert out["exit"] == 3 and "新しい名前でループを始め直す" in out["summary"]
    assert rec(env)["status"] == "stopped" and rec(env)["stop"]["condition"] == "mvv_changed"


def test_resume_stopped_user_stop_removes_the_stop_file_and_resumes(env):
    start(env)
    sl.cmd_stop(env, "l1")
    stop_file = env.root / ".ndf" / "loop" / "l1" / "stop"
    assert stop_file.exists()
    r = stopped_on(env, "user_stop")
    out = sl.resume_stopped(env, r, json.loads(json.dumps(r)))
    assert out["status"] == "ok" and out["metrics"]["status"] == "resumed" and out["metrics"]["resolved"] == "user_stop"
    assert out["metrics"]["next"] == sl.select_cmd("l1") and "user_stop が解けた" in out["summary"]
    assert not stop_file.exists()
    after = rec(env)
    assert after["status"] == "running" and after["stop"] is None and after["stops"][-1]["condition"] == "user_stop"
    assert "resumed_at" in after["stops"][-1]


def test_resume_stopped_budget_still_exceeded_writes_the_record_and_stays_stopped(env):
    start(env, laps=1)
    run_lap(env)
    r = rec(env)
    assert r["stop"]["condition"] == "budget_laps"
    out = sl.resume_stopped(env, r, json.loads(json.dumps(r)))
    assert out["exit"] == 3 and "予算（laps）を超えたまま（消費 1 / 予算 1）" in out["summary"] and "--budget-laps" in out["summary"]
    assert rec(env)["status"] == "stopped"


def test_resume_stopped_budget_within_the_limit_resumes(env):
    start(env, laps=3)
    r = stopped_on(env, "budget_laps")
    out = sl.resume_stopped(env, r, json.loads(json.dumps(r)))
    assert out["metrics"]["status"] == "resumed" and out["metrics"]["resolved"] == "budget_laps"
    assert out["metrics"]["next"] == sl.select_cmd("l1") and rec(env)["status"] == "running"


@pytest.mark.parametrize("cond", ["same_failure", "no_issue", "gate2_returned", "refused"])
def test_resume_stopped_other_conditions_without_a_lap_resume_with_select(env, cond):
    start(env)
    r = stopped_on(env, cond)
    assert sl.current_lap(r) is None
    out = sl.resume_stopped(env, r, json.loads(json.dumps(r)))
    assert out["status"] == "ok" and out["metrics"]["status"] == "resumed" and out["metrics"]["resolved"] == cond
    assert out["metrics"]["next"] == sl.select_cmd("l1") and f"再開した（{cond} が解けた）" in out["summary"]
    assert rec(env)["stops"][-1]["condition"] == cond


def test_resume_stopped_gate2_without_an_answer_stays_stopped(env, monkeypatch):
    built_lap(env)
    monkeypatch.setattr(sl, "gate2_answer", lambda lap, since=None: None)
    r = stopped_on(env, "gate2_returned")
    out = sl.resume_stopped(env, r, json.loads(json.dumps(r)))
    assert out["exit"] == 3 and "承認ゲート 2 の答え（承認か差し戻し）がまだ無い" in out["summary"]
    assert rec(env)["status"] == "stopped"


def test_resume_stopped_gate2_rejected_closes_the_lap_as_returned_and_selects_next(env, monkeypatch):
    built_lap(env)
    seen = []
    monkeypatch.setattr(sl, "gate2_answer", lambda lap, since=None: seen.append(since) or "rejected")
    r = stopped_on(env, "gate2_returned")
    out = sl.resume_stopped(env, r, json.loads(json.dumps(r)))
    assert seen == ["2026-10-25T00:00:00Z"]
    assert out["metrics"]["status"] == "resumed" and out["metrics"]["resolved"] == "gate2_returned"
    assert out["metrics"]["next"] == sl.select_cmd("l1")
    after = rec(env)
    assert after["laps"][0]["outcome"] == sl.RETURNED and sl.current_lap(after) is None and after["status"] == "running"


def test_resume_stopped_gate2_approved_runs_the_gate2_pipeline(env, monkeypatch):
    built_lap(env)
    monkeypatch.setattr(sl, "gate2_answer", lambda lap, since=None: "approved")
    monkeypatch.setattr(sl, "gate2_pipeline", lambda env, rec, lap, stop: f"PIPE {lap['n']} {stop['condition']}")
    r = stopped_on(env, "gate2_returned")
    out = sl.resume_stopped(env, r, json.loads(json.dumps(r)))
    assert out["metrics"]["next"] == "PIPE 1 gate2_returned" and out["metrics"]["status"] == "resumed"
    assert sl.current_lap(rec(env)) is not None


def test_resume_stopped_refused_in_a_lap_asks_to_rebuild_the_sprint(env):
    built_lap(env)
    r = stopped_on(env, "refused", values={"step": "new sprint"})
    out = sl.resume_stopped(env, r, json.loads(json.dumps(r)))
    nxt = out["metrics"]["next"]
    assert out["metrics"]["status"] == "resumed" and "loop sprint l1 --mode <モード> --dev <開発版>" in nxt


def test_resume_stopped_refused_continuation_rebuilds_the_queue_with_kept_issues(env, monkeypatch):
    lap = built_lap(env)
    seen = []

    def cont(env_, rec_, lap_, moved, kept):
        seen.append((lap_["n"], moved, kept))
        return "QUEUE", Path("/tmp/done.json")

    monkeypatch.setattr(sl, "continuation", cont)
    r = rec(env)
    r["waiting"] = [{"number": 11, "lap": lap["n"]}, {"number": 99, "lap": lap["n"] + 1}]
    r["laps"][-1]["issues_kept"] = [12]
    sl.write_record(env, r)
    r = stopped_on(env, "refused", values={"step": sl.CONTINUATION_STEP})
    out = sl.resume_stopped(env, r, json.loads(json.dumps(r)))
    assert seen == [(1, [11], [12])]
    assert out["metrics"]["next"] == f"QUEUE && {sl.wait_lap(r, Path('/tmp/done.json'))}"
    assert out["metrics"]["status"] == "resumed"


@pytest.mark.parametrize("err", [sl.LoopError("組めない"), OSError("os"), ValueError("v"), KeyError("k"), StopIteration()])
def test_resume_stopped_refused_continuation_failure_stays_stopped(env, monkeypatch, err):
    built_lap(env)

    def cont(*a):
        raise err

    monkeypatch.setattr(sl, "continuation", cont)
    r = stopped_on(env, "refused", values={"step": sl.CONTINUATION_STEP})
    out = sl.resume_stopped(env, r, json.loads(json.dumps(r)))
    assert out["exit"] == 3 and "承認ゲート 1 の後の続きをまだ組めない: " in out["summary"]
    assert rec(env)["status"] == "stopped"
