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
    return sl.read_record(env, name)


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
    got = sl.cmd_select(env, "l1")
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
    got = sl.cmd_select(env, "l1")
    assert got["metrics"]["selected"] == [11, 12] and got["metrics"]["milestone"] == "01 近い"
    rows = {r["number"]: r for r in got["metrics"]["excluded"]}
    assert rows[13]["reason"] == "容量" and rows[14]["reason"].startswith("順位")
    assert 21 not in rows  # 1 つのスプリントは 1 つのマイルストーンから
    again = sl.choose(env, rec(env), env.rank_box["out"], 1, {})
    assert [r for r in again["selection"]] == rec(env)["laps"][0]["selection"]


def test_select_falls_through_to_the_next_milestone_and_marks_unapproved_sprint_mvv(env):
    env.rank_box["out"] = rank_out(ms("01 近い", [11]), ms("02 次", [21]))
    env.descs = {"01 近い": MVV_TEXT}
    start(env)
    got = sl.cmd_select(env, "l1")
    assert got["metrics"]["selected"] == [21]
    assert {r["number"]: r["reason"] for r in got["metrics"]["excluded"]}[11] == "スプリント MVV が未承認"


def test_approved_sprint_mvv_is_selected_and_copied_to_the_state(env):
    env.rank_box["out"] = rank_out(ms("01 近い", [11]))
    env.descs = {"01 近い": MVV_TEXT}
    start(env)
    assert sl.cmd_approve_mvv(env, "l1", "01 近い")["status"] == "ok"
    assert sl.cmd_select(env, "l1")["metrics"]["selected"] == [11]
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
    got = sl.cmd_select(env, "l1")
    assert got["metrics"]["selected"] == [11, 13, 14]
    assert {x["number"]: x["reason"] for x in rec(env)["laps"][0]["selection"]}[14] == "品質の枠"


def test_select_refuses_while_a_lap_is_in_progress(env):
    start(env)
    sl.cmd_select(env, "l1")
    assert sl.cmd_select(env, "l1")["exit"] == 1


# ---------------- 受け入れ条件 9・10: 選べない ----------------


def test_no_candidates_is_no_issue_and_all_excluded_is_outside_mvv(env):
    env.rank_box["out"] = rank_out(ms("01 近い", []))
    start(env)
    out = sl.cmd_select(env, "l1")
    assert out["exit"] == 3 and out["metrics"]["stop"]["condition"] == "no_issue" and rec(env)["laps"] == []
    assert out["metrics"]["stop_line"] == "[ndf-loop] stopped l1 no_issue"

    env.rank_box["out"] = rank_out(ms("01 近い", [11]))
    env.descs = {"01 近い": MVV_TEXT}
    start(env, "l2")
    out = sl.cmd_select(env, "l2")
    assert out["metrics"]["stop"]["condition"] == "outside_mvv" and out["metrics"]["stop"]["values"]["reasons"] == {
        "スプリント MVV が未承認": 1
    }


def test_exclude_redoes_the_selection_and_all_excluded_stops(env):
    env.rank_box["out"] = rank_out(ms("01 近い", [11, 12]))
    start(env)
    sl.cmd_select(env, "l1")
    out = sl.cmd_exclude(env, "l1", 11, "モードが対象外（operation）")
    assert out["metrics"]["selected"] == [12]
    out = sl.cmd_exclude(env, "l1", 12, "MVV に反する疑い")
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
    sl.cmd_select(env, "l1")
    sl.cmd_stop(env, "l1")
    assert sl.current_lap(rec(env))["outcome"] == "進行中"  # 途中では止めない
    sl.cmd_sprint(env, "l1", "standard", "1.0.0-dev.1", None, [])
    set_state(env, sl.current_lap(rec(env)))
    out = sl.cmd_lap(env, "l1", False, None, [])
    assert out["metrics"]["stop"]["condition"] == "user_stop"
    assert any("--loop l1" in i["resume"] for i in out["items"])
    doc = (env.root / ".ndf" / "handoff" / "loop-l1.md").read_text()
    assert "ndf-next\n/goal /ndf:development-workflow --loop l1" in doc
    assert sl.cmd_select(env, "l1")["exit"] == 3  # 止まったまま（I7）
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
    out = sl.cmd_select(env, "l1")
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
    sl.cmd_select(env, "l1")
    sl.cmd_sprint(env, "l1", "standard", "1.0.0-dev.1", None, [])
    lap = sl.current_lap(rec(env))
    design11 = str(Path(lap["out"]) / "design-11.json")
    out = sl.cmd_gate(env, "l1", write_done(env, [{"plan": design11, "result": "関門"}]))
    assert out["status"] == "ok" and out["metrics"]["waiting"] == [11]
    q = out["metrics"]["queue"]
    assert "impl-11.json" not in q and "impl-12.json" in q and "--done" in q and "loop lap l1" in out["metrics"]["pipeline"]
    assert "## 人の判断待ち（開発ループ l1）" in env.bodies[11]
    assert not any("--by" in c and "mvv" in c for c in env.calls)  # 承認ゲートを自分で通さない
    # 答えの前は選ばれない
    r = rec(env)
    assert sl.exclusion_reason(env, r, {"number": 11}, 2, set()) == "人の判断待ち"


def test_gate_1_with_every_issue_returned_closes_the_lap_and_continues(env):
    env.rank_box["out"] = rank_out(ms("01 近い", [11]))
    start(env)
    sl.cmd_select(env, "l1")
    sl.cmd_sprint(env, "l1", "standard", "1.0.0-dev.1", None, [])
    lap = sl.current_lap(rec(env))
    out = sl.cmd_gate(env, "l1", write_done(env, [{"plan": str(Path(lap["out"]) / "design-11.json"), "result": "関門"}]))
    assert out["status"] == "ok" and rec(env)["laps"][0]["outcome"] == "人へ戻した" and "ndf_next" in out["metrics"]


def test_gate_2_stops_in_the_middle_of_the_lap(env):
    start(env)
    sl.cmd_select(env, "l1")
    sl.cmd_sprint(env, "l1", "standard", "1.0.0-dev.1", None, [])
    lap = sl.current_lap(rec(env))
    out = sl.cmd_lap(env, "l1", False, write_done(env, [{"plan": str(Path(lap["out"]) / "prod.json"), "result": "関門"}]), [])
    assert out["exit"] == 3 and out["metrics"]["stop"]["condition"] == "gate2_returned"
    assert sl.current_lap(rec(env)) is not None  # 周回は進行中のまま
    assert sl.cmd_start(env, "l1", [], {})["exit"] == 3  # 答えが無いうちは止まったまま


def test_answers_control_reselection(env):
    env.rank_box["out"] = rank_out(ms("01 近い", [11, 12]))
    start(env)
    sl.cmd_select(env, "l1")
    sl.cmd_sprint(env, "l1", "standard", "1.0.0-dev.1", None, [])
    lap = sl.current_lap(rec(env))
    sl.cmd_gate(env, "l1", write_done(env, [{"plan": str(Path(lap["out"]) / f"design-{n}.json"), "result": "関門"} for n in (11, 12)]))
    assert sl.cmd_answer(env, "l1", 11, True, 501)["status"] == "ok"
    assert sl.cmd_answer(env, "l1", 12, False, None)["status"] == "ok"
    assert sl.cmd_answer(env, "l1", 12, True, None)["exit"] == 1  # もう待っていない
    r = rec(env)
    assert sl.exclusion_reason(env, r, {"number": 11}, 2, set()) == "設計 PR がマージ待ち"
    assert sl.exclusion_reason(env, r, {"number": 12}, 2, set()) == "差し戻し"
    env.prs[501] = "MERGED"
    assert sl.exclusion_reason(env, r, {"number": 11}, 2, set()) is None and r["waiting"][0]["merged_at"]
    assert any(c[2:4] == ["gate", lap["state_path"]] and "approved" in c for c in env.calls)


def test_created_issues_are_not_selected_in_the_same_lap(env):
    start(env)
    r = rec(env)
    assert sl.exclusion_reason(env, r, {"number": 99}, 1, {99}).startswith("周回の中で起票された")


# ---------------- 受け入れ条件 14・15・16・21 ----------------


def test_lap_copies_values_carry_reaches_the_next_close_and_list_shows_laps(env):
    (env.root / "upkeep").mkdir()
    (env.root / "upkeep" / "candidates.json").write_text(json.dumps({"metrics": {"deferred": [31, 32]}}))
    (env.root / "upkeep" / "rank.json").write_text(json.dumps({"metrics": {"changes": [{"number": 11}]}}))
    start(env)
    sl.cmd_select(env, "l1")
    sl.cmd_sprint(env, "l1", "standard", "1.0.0-dev.1", None, [])
    set_state(env, sl.current_lap(rec(env)), cost=1.5, seconds=20)
    sl.cmd_lap(env, "l1", False, None, [41, 42])
    lap = rec(env)["laps"][0]
    assert lap["cost"] == 1.5 and lap["seconds"] == 20 and lap["versions"]["dev"] == "1.0.0-dev.1"
    assert lap["refine"]["deferred"] == [31, 32] and lap["refine"]["rank_changes"] == [{"number": 11}]
    assert lap["deferred_issues"] == [41, 42]
    sl.cmd_select(env, "l1")
    out = sl.cmd_sprint(env, "l1", "standard", "1.0.0-dev.2", None, [])
    assert "--carry 31 32" in out["metrics"]["close_command"] and "--pace auto" in out["metrics"]["close_command"]
    assert "1-close.json" in out["metrics"]["queue"].split("--then")[-1]
    assert out["metrics"]["pipeline"].count("&&") == 2 and "wait" in out["metrics"]["pipeline"]
    rows = sl.cmd_list(env, "l1")["items"]
    assert rows[0]["n"] == 1 and rows[0]["issues"] == [11, 12] and rows[0]["deferred_issues"] == [41, 42]
    assert "#41 #42" in (env.root / ".ndf" / "loop" / "l1" / "laps.md").read_text()


def test_new_sprint_refusal_stops_with_refused_and_resume_retries(env):
    start(env)
    sl.cmd_select(env, "l1")
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


# ---------------- CLI ----------------


def test_cli_status_reports_a_missing_record_and_start_refuses(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".ndf").mkdir()
    sp = SCRIPTS / "sprint-state.py"
    p = subprocess.run([sys.executable, str(sp), "loop", "status", "x1", "--json", "--root", str(tmp_path)], capture_output=True, text=True)
    assert p.returncode == 1 and json.loads(p.stdout)["status"] == "stopped"
    p = subprocess.run([sys.executable, str(sp), "loop", "start", "x1", "--root", str(tmp_path)], capture_output=True, text=True)
    assert p.returncode == 1 and json.loads(p.stdout)["metrics"]["status"] == "refused"
