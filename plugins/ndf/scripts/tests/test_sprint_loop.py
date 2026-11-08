"""開発ループ（lib/sprint_loop.py と sprint-state.py loop。#2089）。gh・claude・upkeep.py rank・supervise.py は差し替える。"""

from __future__ import annotations

import hashlib
import json
import re
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
import lap_cost  # noqa: E402
import usage_ledger  # noqa: E402

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
def env(tmp_path, mvv, monkeypatch):
    # 中継の下で起動したシェルの値を受け継ぐと、周回がその中継の記録を指し、状態の表示が変わる
    monkeypatch.delenv("NDF_RELAY_DIR", raising=False)
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
    e.heads = {}  # 設計 PR の先端（gh pr view --json headRefOid）。無ければ HEAD
    e.run = fake_run(e)
    e.merges = []  # 承認した設計のマージの呼び出し (pr, stamp)。結果は merge_box（#2507）
    e.budgets = []  # マージの呼び出しへ渡した基盤待ちの再実行の残り
    e.merge_box = {"result": "merged", "class": "passed", "reason": ""}
    monkeypatch.setattr(
        sl.design_pr_merge,
        "merge_approved",
        lambda root, pr, stamp, run=None, infra_budget=3: (
            e.merges.append((pr, stamp)) or e.budgets.append(infra_budget) or {"pr": pr, "head": stamp, "at": "t", **e.merge_box}
        ),
    )
    return e


HEAD = "h" * 40


def fake_run(e):
    """sprint-state.py init・supervise.py new sprint / new close の差し替え。--out にマニフェストを書く。"""

    def run(cmd, cwd=None, stdin=None):
        e.calls.append(cmd)
        if cmd[:3] == ["gh", "pr", "view"] and "headRefOid" in cmd:
            return 0, e.heads.get(int(cmd[3]), HEAD), ""
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
                (out / "dev.json").write_text(json.dumps({"フェーズ": "リリース", "課題": issues}))
                (out / "prod.json").write_text(json.dumps({"フェーズ": "リリース", "課題": issues}))
                design = [str(plans[f"design-{n}"]) for n in issues]
                stages = [
                    {"name": "設計", "plans": design, "command": "queue " + " ".join(design) + " --max 3"},
                    {"name": "設計承認ゲート", "gate": "..."},
                    {"name": "実装", "plans": [str(plans[f"impl-{n}"]) for n in issues], "then_of": "設計"},
                    {"name": "検査", "plans": [str(out / "check.json")], "then_of": "設計"},
                    {"name": "本番", "plans": [str(out / "prod.json")], "then_of": "設計"},
                ]
                if "--version" not in cmd:  # 版を渡さない周回の開発版のプラン（#2354）
                    stages.insert(-1, {"name": "開発版", "plans": [str(out / "dev.json")], "then_of": "設計"})
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


# ---------------- #2604: 前提の課題の実装待ち ----------------


def with_prereqs(m, prereqs):
    for row in m["rows"]:
        if row["number"] in prereqs:
            row["prerequisites"] = prereqs[row["number"]]
    return m


def test_rows_with_open_prerequisites_are_dropped_and_do_not_use_up_max_issues(env):
    # 受け入れ条件 1・2・6: 境界が無く max_issues が 1。前提で外れた 1 位・2 位は枠を使わず、3 位が選ばれる
    env.rank_box["out"] = rank_out(with_prereqs(ms("01 近い", [11, 12, 13, 14]), {11: [90], 12: [91, 95]}))
    start(env)
    r = rec(env)
    r["max_issues"] = 1
    sl.write_record(env, r)
    got = sl_select.cmd_select(env, "l1")
    assert got["metrics"]["selected"] == [13]
    rows = {x["number"]: x for x in got["metrics"]["excluded"]}
    assert rows[11]["reason"] == "前提の課題の実装待ち（#90）" and rows[11]["decision"] == sl_select.DROPPED
    assert rows[12]["reason"] == "前提の課題の実装待ち（#91 #95）"
    assert rows[14]["reason"].startswith("順位")  # 枠の外


def test_first_issues_with_prerequisites_are_dropped_and_earlier_reasons_are_kept(env):
    # 受け入れ条件 7 と I3: 先頭に選ぶ課題（承認とマージが済んだ設計・revise）も外れる。手の除外・差し戻しの理由は残す
    m = with_prereqs(ms("01 近い", [11, 12, 13, 14, 15]), {n: [90] for n in (11, 12, 13, 14)})
    start(env)
    r = rec(env)
    r["max_issues"] = 5
    r["waiting"] = [
        {"number": 11, "answer": "approved", "merged_at": "t", "lap": 0},
        {"number": 12, "answer": "revise"},
        {"number": 13, "answer": "rejected"},
    ]
    r["excluded"] = [{"number": 14, "lap": 1, "reason": "モードが対象外（operation）"}]
    got = sl_select.choose_issues(env, r, rank_out(m), 1, {})
    reasons = {x["number"]: x["reason"] for x in got["selection"]}
    assert reasons[11] == reasons[12] == "前提の課題の実装待ち（#90）"
    assert reasons[13] == "差し戻し" and reasons[14] == "モードが対象外（operation）" and reasons[15] == "順位"


def test_all_rows_waiting_for_prerequisites_stop_outside_mvv_with_counts(env):
    # 受け入れ条件 10
    env.rank_box["out"] = rank_out(with_prereqs(ms("01 近い", [11, 12]), {11: [90], 12: [90]}))
    start(env)
    out = sl_select.cmd_select(env, "l1")
    assert out["exit"] == 3 and out["metrics"]["stop"]["condition"] == "outside_mvv"
    assert out["metrics"]["stop"]["values"]["reasons"] == {"前提の課題の実装待ち（#90）": 2}


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
    # 尽きた最初の選定は止まらず、見直しを 1 回挟む（#2315 の決定 12）。見直しの後も 0 件なら止まる
    out = sl_select.cmd_select(env, "l1")
    assert out["metrics"]["status"] == "review_first" and "depth due review --no-issue" in out["next"]
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


def test_excluded_rows_of_the_lap_do_not_use_up_max_issues_without_a_boundary(env):
    # #2627: 容量の宣言が無いとき、今の周回で loop exclude した行で枠を埋めない（埋めると後ろの課題が候補に上がらず outside_mvv で止まった）
    env.rank_box["out"] = rank_out(ms("01 近い", [11, 12, 13, 14]))
    start(env)
    assert sl_select.cmd_select(env, "l1")["metrics"]["selected"] == [11, 12]
    out = sl_select.cmd_exclude(env, "l1", 11, "モードが対象外（operation）")
    assert out["metrics"]["selected"] == [12, 13]
    out = sl_select.cmd_exclude(env, "l1", 12, "MVV に反する疑い")
    assert out["metrics"]["selected"] == [13, 14]
    rows = {r["number"]: r["reason"] for r in out["metrics"]["excluded"]}
    assert rows[11] == "モードが対象外（operation）" and rows[12] == "MVV に反する疑い"


def test_exclusions_of_an_earlier_lap_still_use_max_issues(env):
    # 前の周回の除外は今の周回の選定に効かない（候補の枠も、外す理由も）
    env.rank_box["out"] = rank_out(ms("01 近い", [11, 12, 13]))
    start(env)
    r = rec(env)
    r["excluded"] = [{"number": 11, "reason": "モードが対象外（operation）", "lap": 1}]
    got = sl_select.choose_issues(env, r, env.rank_box["out"], 2, {})
    assert [x["number"] for x in got["selection"] if x["decision"] == sl_select.SELECTED] == [11, 12]
    assert {x["number"]: x["reason"] for x in got["selection"]}[13].startswith("順位")


def test_env_exclusions_drop_issues_held_by_other_runs(env, monkeypatch):
    # #2347 の決定 3: Factory が同じリポジトリの別の実行の課題を NDF_LOOP_EXCLUDE で渡し、その周回の選定から外す
    env.rank_box["out"] = rank_out(ms("01 近い", [11, 12, 13]))
    monkeypatch.setenv("NDF_LOOP_EXCLUDE", "11, x 13")
    start(env)
    r = rec(env)
    r["max_issues"] = 5
    sl.write_record(env, r)
    out = sl_select.cmd_select(env, "l1")
    assert out["metrics"]["selected"] == [12]
    rows = {r["number"]: r["reason"] for r in out["metrics"]["excluded"]}
    assert rows[11] == rows[13] == "別の実行が持つ課題"
    assert [e["number"] for e in rec(env)["excluded"]] == [11, 13]


def test_env_exclusions_are_not_added_twice_on_reselection(env, monkeypatch):
    env.rank_box["out"] = rank_out(ms("01 近い", [11, 12, 13]))
    monkeypatch.setenv("NDF_LOOP_EXCLUDE", "11")
    start(env)
    r = rec(env)
    r["max_issues"] = 5
    sl.write_record(env, r)
    sl_select.cmd_select(env, "l1")
    out = sl_select.cmd_exclude(env, "l1", 12, "モードが対象外（operation）")
    assert out["metrics"]["selected"] == [13]
    assert [e["number"] for e in rec(env)["excluded"]] == [11, 12]


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


def test_budget_default_returns_the_unit_to_the_repository_default_and_judges_with_it(env):
    start(env, usd=10)
    n = len(rec(env)["budget_history"])
    assert sl.cmd_start(env, "l1", [], {"usd": sl.BUDGET_DEFAULT})["status"] == "ok"
    r = rec(env)
    assert r["budget"] == {"laps": 3, "usd": 60, "hours": 24}
    assert len(r["budget_history"]) == n + 1 and r["budget_history"][-1]["to"]["usd"] == 60
    out = run_lap(env, cost=15.0)
    assert out["status"] == "ok" and rec(env)["stop"] is None


def declare(env, **loop):
    (env.root / ".ndf" / "pace.json").write_text(json.dumps({**DECL, "loop": {"enabled": True, **loop}}))


def closed_laps(n, hours):
    """1 周回が `hours` 時間かかった `n` 周の締めた周回。"""
    return [
        {
            "n": i + 1,
            "outcome": sl.DONE,
            "started_at": f"2026-10-{i + 1:02d}T00:00:00Z",
            "ended_at": f"2026-10-{i + 1:02d}T{hours:02d}:00:00Z",
        }
        for i in range(n)
    ]


@pytest.mark.parametrize("budget", [{}, {"laps": None, "usd": None, "hours": None}])
def test_units_not_written_never_stop_the_loop(env, budget):
    """書かない宣言と null の宣言は同じ意味で、周回の数・時間・費用のどれでも止まらない。"""
    declare(env, budget=budget)
    start(env)
    r = rec(env)
    assert r["budget"] == {"laps": None, "usd": None, "hours": None}
    r["laps"] = closed_laps(4, 7)
    r["laps"][0]["cost"] = 100000
    used = sl.consumed(r)
    assert used["laps"] == 4 and used["hours"] > 25
    assert sl_record.boundary_stop(env, r) is None


def test_a_null_budget_passes_every_path_that_reads_the_budget(env):
    declare(env, budget={"laps": None, "usd": None, "hours": None})
    start(env)
    assert run_lap(env)["status"] == "ok" and rec(env)["stop"] is None
    assert (env.root / ".ndf/loop/l1" / sl.LAPS_MD).is_file()
    assert sl.cmd_status(env, "l1")["metrics"]["budget"]["laps"] is None
    r = stopped_on(env, "budget_laps")  # 版を上げる前に止まった記録を再開する
    out = sl.resume_stopped(env, r, json.loads(json.dumps(r)))
    assert out["metrics"]["status"] == "resumed"


def test_a_written_unit_stops_at_its_value_when_others_are_unlimited(env):
    declare(env, budget={"laps": 2})
    start(env)
    run_lap(env)
    assert rec(env)["stop"] is None
    env.rank_box["out"] = rank_out(ms("01 近い", [12, 13]))
    run_lap(env)
    assert rec(env)["stop"]["condition"] == "budget_laps"


def test_budget_override_and_default_on_an_unlimited_loop_leave_history(env):
    declare(env)
    start(env)
    sl.cmd_start(env, "l1", [], {"laps": 5})
    assert rec(env)["budget"]["laps"] == 5
    sl.cmd_start(env, "l1", [], {"laps": sl.BUDGET_DEFAULT})
    r = rec(env)
    assert r["budget"]["laps"] is None
    assert [h["to"]["laps"] for h in r["budget_history"]] == [5, None]


def fake_survey(candidates, current, unreadable=()):
    return {"candidates": candidates, "current": current, "reason": "理由", "unreadable": list(unreadable)}


def test_loop_start_reports_the_environment_and_keeps_the_declaration(env, monkeypatch):
    decl = env.root / ".ndf" / "pace.json"
    before = hashlib.sha256(decl.read_bytes()).hexdigest()
    report = fake_survey({"max_issues": 6, "max_plans": 6, "max_named": 3}, {"max_issues": 2, "max_plans": 6, "max_named": 3})
    monkeypatch.setattr(sl.run_environment, "survey", lambda root: report)
    out = start(env)
    assert out["metrics"]["environment"] == report and len(out["items"]) == 1
    assert rec(env)["environments"] == [report]
    assert hashlib.sha256(decl.read_bytes()).hexdigest() == before


def test_loop_start_records_nothing_when_the_candidates_match(env, monkeypatch):
    same = {"max_issues": 2, "max_plans": 6, "max_named": 3}
    monkeypatch.setattr(sl.run_environment, "survey", lambda root: fake_survey(same, same))
    assert start(env)["items"] == [] and "environments" not in rec(env)


def test_loop_start_goes_on_when_the_environment_cannot_be_read(env, monkeypatch):
    monkeypatch.setattr(sl.run_environment, "cpus", lambda: 1 / 0)
    out = start(env)
    assert out["status"] == "ok" and "cpus" in out["metrics"]["environment"]["unreadable"]


def ledger_row(env, runtime, plan, **row):
    d = env.root / "usage" / runtime
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "o__r.jsonl", "a") as f:
        f.write(
            json.dumps({"schema": 2, "at": "2026-11-06T00:00:00Z", "source": "supervise", "kind": "work", "plan": plan, "usage": {}, **row})
            + "\n"
        )


@pytest.fixture
def ledger(env, monkeypatch):
    monkeypatch.setenv("NDF_USAGE_DIR", str(env.root / "usage"))
    assert usage_ledger.ledger_dir() == env.root / "usage"
    return env


def lap_plan(env):
    lap = sl.current_lap(rec(env))
    return lap, str(Path(lap["out"]) / "1-impl.json")


def close(env, cost):
    lap = sl.current_lap(rec(env))
    set_state(env, lap, cost=cost)
    sl.cmd_lap(env, "l1", False, None, [])
    return rec(env)["laps"][0]


def open_lap(env):
    start(env)
    sl_select.cmd_select(env, "l1")
    sl.cmd_sprint(env, "l1", "standard", "1.0.0-dev.1", None, [])


def test_lap_cost_is_the_ledger_sum_without_unpriced_runs(ledger):
    open_lap(ledger)
    _, plan = lap_plan(ledger)
    ledger_row(ledger, "claude", plan, cost_usd=1.0)
    ledger_row(ledger, "claude", plan, cost_usd=0.5, source="cross-review")
    ledger_row(ledger, "claude", plan, cost_usd=None, kind="subagent")
    ledger_row(ledger, "claude", "/other/lap-1/sprint/1-impl.json", cost_usd=9.0)
    ledger_row(ledger, "codex", plan, usage={"total_tokens": 100})
    ledger_row(ledger, "kiro", plan, usage="unknown")
    lap = close(ledger, 1.0)
    assert lap["cost"] == 1.5
    assert lap["cost_parts"] == {
        "source": "ledger",
        "plans": 1.0,
        "others": 0.5,
        "unpriced": {"codex": {"calls": 1, "tokens": 100}, "kiro": {"calls": 1}},
    }


def test_lap_cost_falls_back_to_the_reports_without_a_ledger(ledger):
    open_lap(ledger)
    lap = close(ledger, 2.5)
    assert lap["cost"] == 2.5 and lap["cost_parts"]["source"] == "reports"


def test_lap_cost_takes_the_reports_when_the_ledger_lost_rows_on_a_resume(ledger):
    open_lap(ledger)
    _, plan = lap_plan(ledger)
    ledger_row(ledger, "claude", plan, cost_usd=1.0)
    lap = close(ledger, 4.0)
    assert lap["cost"] == 4.0 and lap["cost_parts"]["source"] == "reports" and lap["cost_parts"]["plans"] == 1.0


def test_lap_close_survives_an_unreadable_ledger(ledger, monkeypatch):
    open_lap(ledger)

    def broken(*a, **k):
        raise OSError("読めない")

    monkeypatch.setattr(lap_cost.usage_ledger, "read_usage_ledger", broken)
    lap = close(ledger, 2.0)
    assert lap["cost"] == 2.0 and lap["cost_parts"]["source"] == "reports"


def test_loop_environment_prints_the_report_and_exits_zero(env):
    sp = SCRIPTS / "sprint-state.py"
    got = subprocess.run(
        [sys.executable, str(sp), "loop", "environment", "--root", str(env.root), "--json"], capture_output=True, text=True
    )
    out = json.loads(got.stdout.strip().splitlines()[-1])
    assert got.returncode == 0 and set(out["metrics"]["candidates"]) == {"max_issues", "max_plans", "max_named"}


def test_a_stale_release_pending_max_is_not_read(env):
    declare(env, release_pending_max="何でも")
    assert start(env)["metrics"]["status"] == "new"
    assert "release_pending_max" not in rec(env)


def test_budget_default_on_a_default_record_or_a_new_loop_adds_no_history(env):
    start(env, laps=sl.BUDGET_DEFAULT, usd=sl.BUDGET_DEFAULT, hours=sl.BUDGET_DEFAULT)
    assert rec(env)["budget"] == {"laps": 3, "usd": 60, "hours": 24} and rec(env)["budget_history"] == []
    sl.cmd_start(env, "l1", [], {"laps": sl.BUDGET_DEFAULT, "usd": sl.BUDGET_DEFAULT, "hours": sl.BUDGET_DEFAULT})
    assert rec(env)["budget_history"] == []


def test_budget_default_uses_builtin_defaults_for_undeclared_units(env):
    (env.root / ".ndf" / "pace.json").write_text(json.dumps({**DECL, "loop": {"enabled": True, "budget": {"usd": 80}}}))
    start(env, laps=1, usd=1, hours=1)
    sl.cmd_start(env, "l1", [], {"laps": sl.BUDGET_DEFAULT, "usd": sl.BUDGET_DEFAULT, "hours": sl.BUDGET_DEFAULT})
    assert rec(env)["budget"] == {"laps": None, "usd": 80, "hours": None}


def test_cli_budget_takes_a_number_or_default_and_refuses_anything_else():
    import argparse

    import sprint_loop_cli

    ap = argparse.ArgumentParser()
    sprint_loop_cli.add_loop_parser(ap.add_subparsers(dest="cmd"), lambda a: None)
    a = ap.parse_args(["loop", "start", "l1", "--budget-usd", "default", "--budget-laps", "2"])
    assert (a.budget_usd, a.budget_laps, a.budget_hours) == (sl.BUDGET_DEFAULT, 2.0, None)
    with pytest.raises(SystemExit) as e:
        ap.parse_args(["loop", "start", "l1", "--budget-usd", "abc"])
    assert e.value.code == 2


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
    assert gates == [{"name": "リリース承認ゲート", "by": "mvv", "verdict": "unknown", "basis": [], "pr": None, "at": ""}]
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
    assert r["laps"][-1]["gates"][0]["name"] == "設計承認ゲート" and r["laps"][-1]["dones"][-1].endswith("done-2.json")


def test_answers_control_reselection(env):
    env.rank_box["out"] = rank_out(ms("01 近い", [11, 12]))
    start(env)
    sl_select.cmd_select(env, "l1")
    sl.cmd_sprint(env, "l1", "standard", "1.0.0-dev.1", None, [])
    lap = sl.current_lap(rec(env))
    sl_gate.cmd_gate(env, "l1", write_done(env, [{"plan": str(Path(lap["out"]) / f"design-{n}.json"), "result": "関門"} for n in (11, 12)]))
    env.merge_box = {"result": "not_green", "class": "running", "reason": "検査が緑でない（running）"}
    # 行に承認した先端が無い（設計 PR を答えで渡した）: 答えを当てずに今の先端を記録し、答え直しを求める（#2507）
    assert sl.cmd_answer(env, "l1", 11, True, 501)["metrics"]["status"] == "regated" and rec(env)["waiting"][0]["stamp"] == HEAD
    assert sl.cmd_answer(env, "l1", 11, True, None)["status"] == "ok" and env.merges == [(501, HEAD)]
    assert sl.cmd_answer(env, "l1", 12, False, None)["status"] == "ok"
    assert sl.cmd_answer(env, "l1", 12, True, None)["exit"] == 1  # もう待っていない
    r = rec(env)
    assert sl_select.exclusion_reason(env, r, {"number": 11}, 2, set()) == "設計 PR がマージ待ち（検査が緑でない（running））"
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


def test_exclusion_reason_reads_each_pr_once_in_rank_order(env):
    """候補ごとに 1 回、順位の順に PR の状態を読み、マージ済みの行にだけ merged_at を書く。"""
    start(env)
    r = rec(env)
    r["waiting"] = [{"number": 11, "answer": "approved", "pr": 501}, {"number": 12, "answer": "approved", "pr": 502}]
    env.prs[502] = "MERGED"
    seen, read = [], env.pr_state
    env.pr_state = lambda n: seen.append(n) or read(n)
    cands = [{"number": 12, "rank": 1}, {"number": 11, "rank": 2}]
    picked, _ = sl_select._select_candidate_rows(cands, "M", 2, lambda row: sl_select.exclusion_reason(env, r, row, 2, set()))
    assert seen == [502, 501] and picked == [12]
    assert r["waiting"][1]["merged_at"] and "merged_at" not in r["waiting"][0]


def test_exclusion_reason_keeps_scanning_rows_of_the_same_issue(env):
    """マージ済みの承認の行は理由にならず、同じ課題の後ろの行と次の段階へ進む。"""
    start(env)
    r = rec(env)
    merged = {"number": 11, "answer": "approved", "merged_at": "t"}
    r["waiting"] = [merged, {"number": 11}]
    assert sl_select.exclusion_reason(env, r, {"number": 11}, 2, set()) == "人の判断待ち"
    r["waiting"] = [merged]
    r["excluded"] = [{"number": 11, "lap": 2, "reason": "対象外"}]
    assert sl_select.exclusion_reason(env, r, {"number": 11}, 2, set()) == "対象外"


# ---------------- 受け入れ条件 14・15・16・21 ----------------


def test_lap_records_depths_and_research_and_counts_research_in_the_budget(env):
    # 周回の記録に深さの行と調査の費用・秒が別の行で残り、予算に数える（#2315 の受け入れ条件 7）
    start(env)
    sl_select.cmd_select(env, "l1")
    out = sl.cmd_sprint(env, "l1", "standard", "1.0.0-dev.1", None, [])
    research = out["metrics"]["research"]
    assert "research run" in research and "--until" in research and "--lap 1 --loop l1" in research
    assert "--max 3" in research  # まとめの queue と同じ並列の上限
    b = env.root / ".ndf" / "backlog"
    (b / "research").mkdir(parents=True)
    (b / "depths.jsonl").write_text(
        json.dumps({"at": "2999-01-01T00:00:00Z", "depth": "review", "event": "ran", "trigger": ["count"]}) + "\n"
    )
    # 結果を捨てた呼び出し（ok: false）も支払った費用として数える
    calls = [
        {"id": "seeds.library:pytest", "ok": True, "cost": 0.5, "seconds": 30, "lap": 1, "by": {"run": "r", "loop": "l1"}},
        {"id": "needs.market:x", "ok": False, "cost": 0.25, "seconds": 10, "lap": 1, "by": {"run": "r", "loop": "l1"}},
    ]
    (b / "research" / "calls.jsonl").write_text("".join(json.dumps(c) + "\n" for c in calls))
    set_state(env, sl.current_lap(rec(env)), cost=1.5, seconds=20)
    sl.cmd_lap(env, "l1", False, None, [])
    lap = rec(env)["laps"][0]
    assert lap["research"] == {"items": ["seeds.library:pytest"], "cost": 0.75, "seconds": 40.0}
    assert lap["depths"][0]["depth"] == "review" and sl.consumed(rec(env))["usd"] == 2.25
    assert "調査: 1 項目" in (env.root / ".ndf" / "loop" / "l1" / "laps.md").read_text()
    # 周回を締めた後に終わった背景の調査の費用は、次に締めるときに締め済みの周回へ精算する
    late = {"id": "seeds.alternatives:pytest", "ok": True, "cost": 1.0, "seconds": 5, "lap": 1, "by": {"run": "r", "loop": "l1"}}
    with (b / "research" / "calls.jsonl").open("a") as f:
        f.write(json.dumps(late) + "\n")
    r = rec(env)
    sl_gate.backlog_summary(env, r, {"n": 2, "started_at": ""})
    assert r["laps"][0]["research"]["cost"] == 1.75 and sl.consumed(r)["usd"] == 3.25
    assert sl.cmd_status(env, "l1")["items"][-1] == "見直しの持ち主: なし"


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
    assert "--loop l1 --lap 2" in out["metrics"]["close_command"]  # 差分の棚卸しの行に開発ループと周回を残す（#2315 の 17b-2）
    # 周回の版は出し終えているので、まとめへ明示の版として渡さない（流す時点に次に空いている版で決める。#2164）
    assert "--version" not in out["metrics"]["close_command"] and "--prod" not in out["metrics"]["close_command"]
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


def test_sprint_without_a_version_runs_when_no_template_route_needs_it(env):
    """#2623 の受け入れ条件 4: 版数の要らない経路では --dev なしで周回を始め、状態にも new sprint にも版を渡さない。"""
    (env.main_dir() / ".ndf" / "project.json").write_text(json.dumps({"delivery": []}))
    start(env)
    sl_select.cmd_select(env, "l1")
    seen = []
    base = env.run

    def run(cmd, cwd=None, stdin=None):
        seen.append(cmd)
        return base(cmd, cwd=cwd, stdin=stdin)

    env.run = run
    out = sl.cmd_sprint(env, "l1", "standard", None, None, [])
    assert out["status"] == "ok", out
    assert not rec(env)["laps"][-1]["versions"]["dev"]
    assert not [c for c in seen if "--dev" in c or "--version" in c or "--prod" in c]


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


def test_gate_1_points_the_held_close_at_the_close_without_the_waiting_issue(env):
    lap = built_lap(env)
    first = lap["close_plans"]
    sl_gate.cmd_gate(env, "l1", write_done(env, [{"plan": str(Path(lap["out"]) / "design-11.json"), "result": "関門"}]))
    now = rec(env)["laps"][-1]["close_plans"]
    assert now != first and all(Path(p).parent.name.startswith("close-") for p in now)


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
    env.merge_box = {"result": "conflict", "class": "passed", "reason": "宛先と衝突している"}
    sl.cmd_answer(env, "l1", 11, True, None)
    gate = next(c for c in env.calls if "gate" in c)
    assert gate[gate.index("--pr") + 1] == "501"
    assert sl_select.exclusion_reason(env, rec(env), {"number": 11}, 2, set()) == "設計 PR がマージ待ち（宛先と衝突している）"


def approve_gate_2(lap):
    state = json.loads(Path(lap["state_path"]).read_text())
    state["gates"].append({"name": "リリース承認ゲート", "by": "user", "outcome": "approved", "at": "2026-12-01T00:00:00Z"})
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
    p.write_text(json.dumps({"フェーズ": "リリース", "課題": [11, 12], "steps": [{"id": s} for s in steps]}))
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
    state["gates"].append(
        {"name": "リリース承認ゲート", "by": "user", "outcome": "approved", "at": "2026-10-01T00:00:00Z"}
    )  # 前の承認ゲートへの承認
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
    index = [
        {"name": "実装", "command": "Q1"},
        {"name": "リリース承認ゲート", "command": "Q2"},
        {"name": "リリース", "manual": "/ndf:release"},
    ]
    queue, pipeline = sl.sprint_pipeline(index, ["c1"], done, rec_)
    assert queue == f"python3 {sl_record.SUPERVISE} queue c1 --max 3 --done {done}"
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


def test_continuation_finds_the_design_gate_stage_under_the_old_name(env):
    """#2619 の I2: 旧い版が書いた目録の設計承認ゲートのステージでも、続きの queue の組み立てが変わらない。"""
    lap = built_lap(env)
    manifest = Path(lap["manifest"])
    new = sl_gate.continuation(env, rec(env), lap, [], [11, 12])
    index = json.loads(manifest.read_text(encoding="utf-8"))
    for stage in index["ステージ"]:
        if stage.get("name") == sl.DESIGN_GATE:
            stage["name"] = "関門 1"
    manifest.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    numbered = re.compile(r"(close|done)-\d+")
    assert numbered.sub(r"\1", str(sl_gate.continuation(env, rec(env), lap, [], [11, 12]))) == numbered.sub(r"\1", str(new))


def test_gate_2_resume_builds_the_close_without_the_check_pr_as_the_record(env):
    """#2230 の受け入れ条件 2: リリース承認ゲートの後のまとめへ、止まった記録の values.pr（検査のスプリント PR）を --record-pr で渡さない。"""
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
        {"name": "リリース承認ゲート", "by": "mvv", "at": "2026-10-05T00:00:00Z"},  # MVV の通過は数えない
        {"name": "設計承認ゲート", "by": "user", "at": "2026-10-05T00:00:00Z"},  # 別の承認ゲートは数えない
        {"name": "リリース承認ゲート", "by": "user", "at": "2026-10-01T00:00:00Z"},  # since より前
    ]
    lap, _ = g2_lap(tmp_path, gates)
    assert sl.gate2_answer(lap, "2026-10-02T00:00:00Z") is None
    assert sl.gate2_answer(lap, "2026-10-01T00:00:00Z") == "approved"  # 同じ時刻は数える
    assert sl.gate2_answer(lap) == "approved"
    lap2, _ = g2_lap(tmp_path, [{"name": "リリース承認ゲート", "by": "user"}])  # 時刻の無い答え
    assert sl.gate2_answer(lap2) == "approved"
    assert sl.gate2_answer(lap2, "2026-10-01T00:00:00Z") is None


def test_gate2_answer_reads_rejections_of_the_same_sprint_from_the_ledger(tmp_path):
    lap, sp = g2_lap(tmp_path)
    g2_ledger(
        {"gate": "リリース承認ゲート", "sprint": sp, "at": "2026-10-03T00:00:00Z"},  # outcome の無い行は差し戻し
        {"gate": "リリース承認ゲート", "sprint": sp, "at": "2026-10-09T00:00:00Z", "outcome": "approved"},  # 承認の行は読まない
        {"gate": "設計承認ゲート", "sprint": sp, "at": "2026-10-09T00:00:00Z"},
        {"gate": "リリース承認ゲート", "sprint": "/other.json", "at": "2026-10-09T00:00:00Z"},
    )
    assert sl.gate2_answer(lap, "2026-10-02T00:00:00Z") == "rejected"
    assert sl.gate2_answer(lap, "2026-10-04T00:00:00Z") is None
    assert sl.gate2_answer(lap) == "rejected"


def test_gate2_answer_takes_the_latest_answer(tmp_path):
    lap, sp = g2_lap(tmp_path, [{"name": "リリース承認ゲート", "by": "user", "at": "2026-10-05T00:00:00Z"}])
    g2_ledger({"gate": "リリース承認ゲート", "sprint": sp, "at": "2026-10-04T00:00:00Z", "outcome": "rejected"})
    assert sl.gate2_answer(lap, "2026-10-01T00:00:00Z") == "approved"
    g2_ledger({"gate": "リリース承認ゲート", "sprint": sp, "at": "2026-10-06T00:00:00Z", "outcome": "rejected"})
    assert sl.gate2_answer(lap, "2026-10-01T00:00:00Z") == "rejected"
    g2_ledger({"gate": "リリース承認ゲート", "sprint": sp, "at": "2026-10-05T00:00:00Z", "outcome": "rejected"})  # 同じ時刻は先の承認
    assert sl.gate2_answer(lap, "2026-10-01T00:00:00Z") == "approved"


def test_gate2_answer_treats_a_broken_ledger_as_empty(tmp_path):
    lap, _ = g2_lap(tmp_path, [{"name": "リリース承認ゲート", "by": "user", "at": "2026-10-05T00:00:00Z"}])
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
    assert out["exit"] == 3 and "リリース承認ゲートの答え（承認か差し戻し）がまだ無い" in out["summary"]
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
    assert out["metrics"]["status"] == "resumed" and "loop sprint l1 --mode <モード>（" in nxt and "--dev" not in nxt


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
    assert out["exit"] == 3 and "設計承認ゲートの後の続きをまだ組めない: " in out["summary"]
    assert rec(env)["status"] == "stopped"


# ---------------- #2436 の受け入れ条件 6a・6d・6e: レーンのリリース待ちと質問待ち ----------------

REQ = "lroot-r000000000001"


def lane_lap(env, nums=(11, 12), rid=REQ, rank=None, dev="1.0.0-dev.1"):
    """並列の計画のレーンの周回: 本番のステージがリリースの依頼のプラン（フェーズ「リリースの依頼」）で、依頼の記録がある。"""
    env.rank_box["out"] = rank or rank_out(ms("01 近い", [11, 12, 13]))
    env.answer_box = {}
    env.answers = lambda plan, ids: {i: env.answer_box.get(i, {"answer": None, "defer": None}) for i in ids}
    if not (env.root / ".ndf/loop/l1/loop.json").exists():
        start(env)
    sl_select.cmd_select(env, "l1")
    sl.cmd_sprint(env, "l1", "standard", dev, None, [])
    lap = sl.current_lap(rec(env))
    prod = Path(lap["out"]) / "prod.json"
    prod.write_text(json.dumps({"フェーズ": "リリースの依頼", "課題": list(nums), "並列の計画": "p1"}, ensure_ascii=False))
    work = prod.with_name("prod-state") / "work"
    work.mkdir(parents=True, exist_ok=True)
    (work / "request.txt").write_text(json.dumps({"id": rid, "plan": "p1", "lane": "lroot", "prs": [5]}))
    return lap


def request_lap(env, rid=REQ):
    lap = lane_lap(env, rid=rid)
    set_state(env, lap)
    out = sl.cmd_lap(env, "l1", False, write_done(env, [{"plan": str(Path(lap["out"]) / "prod.json"), "result": "関門"}]), [])
    return lap, out


def test_release_request_closes_the_lap_as_release_pending_and_goes_on(env):
    lap, out = request_lap(env)
    assert out["status"] == "ok" and "ndf_next" in out["metrics"]  # gate2_returned で止まらない
    r = rec(env)
    done = r["laps"][0]
    assert done["outcome"] == "リリース待ち" and r["status"] == "running"
    assert done["release"]["plan_name"] == "p1" and done["release"]["request"] == REQ
    assert done["release"]["close_plans"]  # まとめは流さずに残す
    assert sl.current_lap(r) is None


def test_issues_of_a_pending_lap_are_not_selected(env):
    request_lap(env)
    out = sl_select.cmd_select(env, "l1")
    assert out["metrics"]["selected"] == [13]
    why = {x["number"]: x["reason"] for x in out["items"]}
    assert why[11] == why[12] == "保留の周回の課題"


def test_released_answer_runs_the_close_and_finish_closes_the_lap_with_the_version(env):
    request_lap(env)
    env.answer_box[REQ] = {"answer": {"result": "released", "version": "10.28.0"}, "defer": None}
    out = sl_select.cmd_select(env, "l1")
    assert out["metrics"]["status"] == "finish" and out["metrics"]["lap"] == 1
    pipe = out["metrics"]["pipeline"]
    assert "1-close.json" in pipe and "loop finish l1 1 --done" in pipe
    r = rec(env)
    assert r["laps"][0]["release"]["answer"]["version"] == "10.28.0" and len(r["laps"]) == 1  # 新しい周回を先に始めない
    d = r["laps"][0]["release"]["finish"]["done"]
    Path(d).write_text(json.dumps({"items": [{"plan": "x", "result": "完了"}]}))
    fin = sl.cmd_finish(env, "l1", 1, d)
    lap = rec(env)["laps"][0]
    assert fin["status"] == "ok" and lap["outcome"] == "完了" and lap["versions"]["prod"] == "10.28.0"
    assert sl.cmd_finish(env, "l1", 1, d)["exit"] == 1  # 締め直しは 1 回だけ


def test_released_answer_without_a_version_runs_the_close_and_writes_no_version(env):
    """組まずに答えたリリース済み（#2616 の受け入れ条件 4）は版を持たず、まとめへ進む。"""
    request_lap(env)
    env.answer_box[REQ] = {"answer": {"result": "released", "version": None, "reason": "stg はマージで反映する"}, "defer": None}
    out = sl_select.cmd_select(env, "l1")
    assert out["metrics"]["status"] == "finish" and "1-close.json" in out["metrics"]["pipeline"]
    lap = rec(env)["laps"][0]
    assert lap["release"]["answer"]["version"] is None and "prod" not in lap.get("versions", {})


def test_two_answered_pending_laps_are_finished_oldest_first(env):
    request_lap(env, rid="lroot-r000000000001")
    env.rank_box["out"] = rank_out(ms("01 近い", [13, 14]))
    lap2 = lane_lap(env, nums=(13,), rid="lroot-r000000000002", rank=rank_out(ms("01 近い", [13, 14])))
    set_state(env, lap2)
    sl.cmd_lap(env, "l1", False, write_done(env, [{"plan": str(Path(lap2["out"]) / "prod.json"), "result": "関門"}]), [])
    for rid in ("lroot-r000000000001", "lroot-r000000000002"):
        env.answer_box[rid] = {"answer": {"result": "released", "version": "10.28.0"}, "defer": None}
    assert sl_select.cmd_select(env, "l1")["metrics"]["lap"] == 1


def test_rejected_answer_returns_the_lap_without_the_close(env):
    request_lap(env)
    env.answer_box[REQ] = {"answer": {"result": "rejected", "reason": "直す"}, "defer": None}
    out = sl_select.cmd_select(env, "l1")
    r = rec(env)
    assert r["laps"][0]["outcome"] == "人へ戻した" and "finish" not in r["laps"][0]["release"]
    assert r["fixes"][0]["lap"] == 1 and r["fixes"][0]["reason"] == "直す"
    assert out["metrics"]["status"] == "selected" and out["metrics"]["selected"][0] in (11, 12)  # 直しの周回が先頭に選ぶ


def test_failed_answer_closes_the_lap_as_failed(env):
    request_lap(env)
    env.answer_box[REQ] = {"answer": {"result": "failed", "reason": "CI"}, "defer": None}
    sl_select.cmd_select(env, "l1")
    lap = rec(env)["laps"][0]
    assert lap["outcome"] == "失敗" and lap["failure"]["kind"] == "リリース"


def test_defer_keeps_the_lap_pending(env):
    request_lap(env)
    env.answer_box[REQ] = {"answer": None, "defer": {"seq": 1, "reason": "次のデプロイへ"}}
    out = sl_select.cmd_select(env, "l1")
    assert rec(env)["laps"][0]["outcome"] == "リリース待ち" and out["metrics"]["selected"] == [13]


def test_unanswered_pending_laps_do_not_stop_the_next_selection(env):
    """答えの無い保留の周回がいくつあっても、選べる課題があれば次の周回を選ぶ。"""
    start(env, laps=100)
    request_lap(env, rid="lroot-r000000000001")
    for i, n in enumerate((13, 14), 2):
        lap = lane_lap(env, nums=(n,), rid=f"lroot-r00000000000{i}", rank=rank_out(ms("01 近い", [13, 14, 15])))
        set_state(env, lap)
        sl.cmd_lap(env, "l1", False, write_done(env, [{"plan": str(Path(lap["out"]) / "prod.json"), "result": "関門"}]), [])
    assert len(sl_record.pending_laps(rec(env))) == 3
    env.rank_box["out"] = rank_out(ms("01 近い", [16]))
    out = sl_select.cmd_select(env, "l1")
    assert out["metrics"]["selected"] == [16] and "stop" not in out["metrics"]


def test_waiting_only_instead_of_no_issue_when_only_pending_work_is_left(env):
    request_lap(env)
    env.rank_box["out"] = rank_out(ms("01 近い", [11, 12]))
    out = sl_select.cmd_select(env, "l1")
    assert out["exit"] == 3 and out["metrics"]["stop"]["condition"] == "waiting_only"


def test_a_waiting_only_lane_stops_its_own_wrapper(env, monkeypatch):
    """待ちだけで止まったレーンは自分のラッパーを止める 1 行を返す（判断 fca6fe464619 の (a)）。ラッパーの外と、並列の計画の
    レーンでないラッパーでは返さない。"""
    import parallel_files

    request_lap(env)
    env.rank_box["out"] = rank_out(ms("01 近い", [11, 12]))
    monkeypatch.delenv("NDF_RELAY_NAME", raising=False)
    out = sl_select.cmd_select(env, "l1")
    assert out["metrics"]["stop"]["condition"] == "waiting_only" and out["metrics"].get("stop_self") is None
    sl.cmd_start(env, "l1", [], {})
    monkeypatch.setenv("NDF_RELAY_NAME", "la")
    out = sl_select.cmd_select(env, "l1")  # 並列の計画のレーンでないラッパー（利用者の端末）は止めない
    assert out["metrics"]["stop"]["condition"] == "waiting_only" and out["metrics"].get("stop_self") is None
    plan = env.root / ".ndf" / "parallel" / "p1"
    plan.mkdir(parents=True)
    (plan / "plan.json").write_text(json.dumps({"name": "p1", "lanes": [{"name": "la"}]}))
    sl.cmd_start(env, "l1", [], {})
    out = sl_select.cmd_select(env, "l1")
    assert out["metrics"]["stop"]["condition"] == "waiting_only" and out["metrics"]["stop_self"] == parallel_files.stop_self(env.root)
    assert out["metrics"]["stop_self"]
    sl.cmd_start(env, "l1", [], {})
    sl.cmd_stop(env, "l1")
    assert sl_select.cmd_select(env, "l1")["metrics"].get("stop_self") is None  # 待ちでない止まり方はラッパーを残す


def test_budget_waits_first_but_user_stop_stops_at_once(env):
    request_lap(env)
    r = rec(env)
    r["budget"]["laps"] = 1
    sl.write_record(env, r)
    assert sl_select.cmd_select(env, "l1")["metrics"]["stop"]["condition"] == "waiting_only"
    sl.cmd_start(env, "l1", [], {})
    sl.cmd_stop(env, "l1")
    assert sl_select.cmd_select(env, "l1")["metrics"]["stop"]["condition"] == "user_stop"


def test_pending_counts_as_a_lap_in_the_budget_but_not_as_a_failure(env):
    request_lap(env)
    r = rec(env)
    assert sl.consumed(r)["laps"] == 1 and sl_record.failure_streak(r) == (None, 0)


def test_loop_pending_returns_only_the_answered_close(env):
    request_lap(env)
    assert sl.cmd_pending(env, "l1")["metrics"]["status"] == "none"
    env.answer_box[REQ] = {"answer": {"result": "released", "version": "10.28.0"}, "defer": None}
    out = sl.cmd_pending(env, "l1")
    assert out["metrics"]["status"] == "finish" and "loop finish l1 1" in out["metrics"]["pipeline"]


def test_loop_pending_settles_a_rejected_answer_and_keeps_the_record(env):
    """全レーンが止まった後の入口（loop pending）でも、差し戻しの答えで保留の周回を締め直して記録へ残す。"""
    request_lap(env)
    env.answer_box[REQ] = {"answer": {"result": "rejected", "reason": "直す"}, "defer": None}
    assert sl.cmd_pending(env, "l1")["metrics"]["status"] == "none"
    lap = rec(env)["laps"][0]
    assert lap["outcome"] == "人へ戻した" and lap["release"]["answer"]["result"] == "rejected"


def test_hold_question_closes_the_lap_and_the_answer_resumes_the_stopped_plan(env):
    lap = lane_lap(env)
    impl = str(Path(lap["out"]) / "impl-11.json")
    out = sl.cmd_hold_question(env, "l1", "lroot-q1", impl, "fix")
    r = rec(env)
    assert out["status"] == "ok" and r["laps"][0]["outcome"] == "質問待ち" and r["laps"][0]["question"]["request"] == "lroot-q1"
    assert sl_select.cmd_select(env, "l1")["metrics"]["selected"] == [13]  # 答えの前は流さない
    sl.cmd_sprint(env, "l1", "standard", "1.0.0-dev.2", None, [])
    set_state(env, sl.current_lap(rec(env)))
    sl.cmd_lap(env, "l1", False, None, [])
    env.answer_box["lroot-q1"] = {"answer": {"result": "answered", "choice": ["A"]}, "defer": None}
    got = sl_select.cmd_select(env, "l1")
    pipe = got["metrics"]["pipeline"]
    assert got["metrics"]["status"] == "finish" and f"run {impl} --from fix" in pipe and "loop finish l1 1" in pipe


def test_answer_revise_keeps_the_note_and_selects_the_issue_first(env, tmp_path):
    env.rank_box["out"] = rank_out(ms("01 近い", [12, 11]))
    start(env)
    sl_select.cmd_select(env, "l1")
    sl.cmd_sprint(env, "l1", "standard", "1.0.0-dev.1", None, [])
    lap = sl.current_lap(rec(env))
    sl_gate.cmd_gate(env, "l1", write_done(env, [{"plan": str(Path(lap["out"]) / f"design-{n}.json"), "result": "関門"} for n in (11, 12)]))
    sl.cmd_answer(env, "l1", 12, True, 501)
    note = tmp_path / "note.md"
    note.write_text("API の形を変える")
    out = sl.cmd_answer(env, "l1", 11, False, None, revise=True, note=str(note))
    w = next(w for w in rec(env)["waiting"] if w["number"] == 11)
    assert out["status"] == "ok" and w["answer"] == "revise" and w["note"] == "API の形を変える" and w["revise_count"] == 1
    assert "API の形を変える" in env.bodies[11]
    r = rec(env)
    assert sl_select.exclusion_reason(env, r, {"number": 11}, 2, set()) is None
    assert sl.current_lap(r) is None  # 両方の課題を人の判断待ちへ移した周回は締まっている
    env.rank_box["out"] = rank_out(ms("01 近い", [13, 14, 11]))
    assert sl_select.cmd_select(env, "l1")["metrics"]["selected"][0] == 11


def test_gate1_answers_are_applied_by_the_lane_at_the_boundary(env):
    env.rank_box["out"] = rank_out(ms("01 近い", [11, 12, 13]))
    env.answer_box = {}
    env.answers = lambda plan, ids: {i: env.answer_box.get(i, {"answer": None, "defer": None}) for i in ids}
    start(env)
    sl_select.cmd_select(env, "l1")
    sl.cmd_sprint(env, "l1", "standard", "1.0.0-dev.1", None, [])
    lap = sl.current_lap(rec(env))
    sl_gate.cmd_gate(env, "l1", write_done(env, [{"plan": str(Path(lap["out"]) / "design-11.json"), "result": "関門"}]))
    r = rec(env)
    r["waiting"][0].update(request="lroot-g501-abc", plan="p1", pr=501)
    sl.write_record(env, r)
    set_state(env, sl.current_lap(r))
    sl.cmd_lap(env, "l1", False, None, [])
    env.answer_box["lroot-g501-abc"] = {"answer": {"result": "approved"}, "defer": None}
    sl_select.cmd_select(env, "l1")
    w = rec(env)["waiting"][0]
    assert w["answer"] == "approved" and w["pr"] == 501
    assert any(c[:3] == ["gh", "pr", "edit"] and "design-approved" in c for c in env.calls)


def test_gate1_approval_for_a_moved_design_pr_is_asked_again(env, monkeypatch):
    """問いの後に設計 PR へ push があれば、古い範囲への承認で design-approved と承認の記録を書かず、新しい先端で問い直す。"""
    env.rank_box["out"] = rank_out(ms("01 近い", [11, 12, 13]))
    env.answer_box = {}
    env.answers = lambda plan, ids: {i: env.answer_box.get(i, {"answer": None, "defer": None}) for i in ids}
    start(env)
    sl_select.cmd_select(env, "l1")
    sl.cmd_sprint(env, "l1", "standard", "1.0.0-dev.1", None, [])
    lap = sl.current_lap(rec(env))
    sl_gate.cmd_gate(env, "l1", write_done(env, [{"plan": str(Path(lap["out"]) / "design-11.json"), "result": "関門"}]))
    r = rec(env)
    r["waiting"][0].update(request="lroot-g501-aaaaaaaaaaaa", plan="p1", pr=501, stamp="a" * 40, report="r.md", state="s.json")
    sl.write_record(env, r)
    set_state(env, sl.current_lap(r))
    sl.cmd_lap(env, "l1", False, None, [])
    base = env.run
    env.run = lambda cmd, cwd=None, stdin=None: (0, "b" * 40, "") if cmd[:3] == ["gh", "pr", "view"] else base(cmd, cwd=cwd, stdin=stdin)
    sent = []
    monkeypatch.setattr(
        sl_gate,
        "lane_gate1",
        lambda e, w, report, state: sent.append((report, state)) or {"request": "lroot-g501-bbbbbbbbbbbb", "stamp": "b" * 40},
    )
    env.answer_box["lroot-g501-aaaaaaaaaaaa"] = {"answer": {"result": "approved"}, "defer": None}
    sl_select.cmd_select(env, "l1")
    w = rec(env)["waiting"][0]
    assert not w.get("answer") and w["request"] == "lroot-g501-bbbbbbbbbbbb" and w["restamped"] == "a" * 40
    assert sent == [("r.md", "s.json")]
    assert not any(c[:3] == ["gh", "pr", "edit"] for c in env.calls)


def test_gate_1_of_a_lane_sends_the_request_to_the_organizer(env, monkeypatch):
    lap = built_lap(env)
    report = design_report(env, "design-11", "https://github.com/o/r/pull/501")
    sent = []
    monkeypatch.setattr(
        sl_gate, "lane_gate1", lambda e, w, report, state: sent.append(w["number"]) or {"request": "lroot-g501-x", "plan": "p1"}
    )
    item = {"plan": str(Path(lap["out"]) / "design-11.json"), "result": "関門", "report": report}
    out = sl_gate.cmd_gate(env, "l1", write_done(env, [item]))
    w = rec(env)["waiting"][0]
    assert out["metrics"]["waiting"] == [11] and sent == [11] and w["request"] == "lroot-g501-x" and w["plan"] == "p1"


def test_selecting_a_returned_issue_marks_its_fix(env):
    # 直しの周回: 差し戻された周回の課題を選ぶと、その直しを済みにする。選ばなかった直しと済みの直しは変えない（現状固定。#2422）
    start(env)
    with sl_record.open_record(env, "l1") as (r, before):
        r["fixes"] = [{"lap": 0, "issues": [12]}, {"lap": 0, "issues": [99]}, {"lap": 0, "issues": [11], "fixed_by": 7}]
        sl_record.write_record(env, r, before)
    got = sl_select.cmd_select(env, "l1")
    lap = rec(env)["laps"][-1]
    assert got["metrics"]["selected"] == [12, 11]
    assert (lap["sprint"], lap["milestone"], lap["rank_digest"], lap["state_path"]) == (f"l1-{lap['n']}", "01 近い", "dg", None)
    assert [f.get("fixed_by") for f in rec(env)["fixes"]] == [lap["n"], None, 7]


# ---------------------------------------------------------------- 承認した設計のマージ（#2507）


def gated_both(env):
    """#11（設計 PR 501）と #12（設計 PR 502）を設計承認ゲートで人の判断待ちへ移し、周回を締めた記録。"""
    lap = built_lap(env)
    items = [
        {
            "plan": str(Path(lap["out"]) / f"design-{n}.json"),
            "result": "関門",
            "report": design_report(env, f"d{n}", f"https://github.com/o/r/pull/{pr}"),
        }
        for n, pr in ((11, 501), (12, 502))
    ]
    sl_gate.cmd_gate(env, "l1", write_done(env, items))
    assert sl.current_lap(rec(env)) is None
    return rec(env)


def row(env, n):
    return next(w for w in reversed(rec(env)["waiting"]) if w["number"] == n)


def test_loop_answer_merges_the_approved_design_and_the_next_lap_selects_it_first(env):
    r = gated_both(env)
    assert [w["stamp"] for w in r["waiting"]] == [HEAD, HEAD]  # 並列の計画の外でも承認した先端を書く
    out = sl.cmd_answer(env, "l1", 11, True, None)
    w = row(env, 11)
    assert out["status"] == "ok" and env.merges == [(501, HEAD)] and w["merge"]["result"] == "merged" and w["merged_at"]
    assert out["items"][0]["merge"]["result"] == "merged"
    sl.cmd_answer(env, "l1", 12, False, None)
    env.rank_box["out"] = rank_out(ms("01 近い", [13, 14, 11]))
    got = sl_select.cmd_select(env, "l1")
    assert got["metrics"]["selected"][0] == 11
    first = next(x for x in got["items"] if x["number"] == 11)
    assert first["reason"] == sl_select.APPROVED_REASON
    assert any(c[-2:] == ["cleanup", "501"] for c in env.calls)  # 後片付けは周回の境が打つ
    # 選ばれた後の周回では先頭に選ばない
    assert 11 not in sl_select.approved_designs(rec(env))


def test_loop_exclude_keeps_the_approved_design_first_on_every_reselection(env):
    """同じ周回の取り直し（loop exclude）は、取り直す周回自身の選定を「選ばれた後の周回」に数えない。"""
    gated_both(env)
    sl.cmd_answer(env, "l1", 11, True, None)
    sl.cmd_answer(env, "l1", 12, False, None)
    env.rank_box["out"] = rank_out(ms("01 近い", [13, 14, 15, 16, 17, 11]))
    assert sl_select.cmd_select(env, "l1")["metrics"]["selected"][0] == 11
    for excluded in (13, 14):  # 1 回ごとに先頭から落ちて戻るのではなく、どの取り直しでも先頭に残る
        got = sl_select.cmd_exclude(env, "l1", excluded, "外す")
        assert got["metrics"]["selected"][0] == 11
        assert next(x for x in got["items"] if x["number"] == 11)["reason"] == sl_select.APPROVED_REASON


def test_revise_and_rejected_answers_do_not_merge(env, tmp_path):
    gated_both(env)
    note = tmp_path / "n.md"
    note.write_text("直す")
    sl.cmd_answer(env, "l1", 11, False, None, revise=True, note=str(note))
    sl.cmd_answer(env, "l1", 12, False, None)
    assert env.merges == []


def test_loop_answer_for_a_moved_design_pr_is_regated_without_merging(env):
    gated_both(env)
    env.heads[501] = "b" * 40
    out = sl.cmd_answer(env, "l1", 11, True, None)
    w = row(env, 11)
    assert out["status"] == "ok" and out["metrics"]["status"] == "regated" and not w.get("answer") and env.merges == []
    assert w["stamp"] == "b" * 40 and w["restamped"] == HEAD
    assert sl.cmd_answer(env, "l1", 11, True, None)["metrics"]["status"] == "answered" and env.merges == [(501, "b" * 40)]


def test_a_failed_merge_still_answers_and_is_retried_at_the_next_boundary(env):
    gated_both(env)
    env.merge_box = {"result": "not_green", "class": "running", "reason": "検査が緑でない（running）"}
    out = sl.cmd_answer(env, "l1", 11, True, None)
    assert out["status"] == "ok" and row(env, 11)["answer"] == "approved" and "マージできない" in out["summary"]
    sl.cmd_answer(env, "l1", 12, False, None)
    env.rank_box["out"] = rank_out(ms("01 近い", [11, 13, 14]))
    env.merge_box = {"result": "merged", "class": "passed", "reason": ""}
    got = sl_select.cmd_select(env, "l1")  # 境で 1 回マージし直し、同じ周回の選定に含める（受け入れ条件 7）
    assert len(env.merges) == 2 and got["metrics"]["selected"][0] == 11 and row(env, 11)["merge"]["lap"] == 2


def test_a_merge_that_fails_again_only_drops_that_issue(env):
    gated_both(env)
    env.merge_box = {"result": "not_green", "class": "queued", "reason": "検査が緑でない（queued）"}
    sl.cmd_answer(env, "l1", 11, True, None)
    sl.cmd_answer(env, "l1", 12, False, None)
    env.rank_box["out"] = rank_out(ms("01 近い", [11, 13, 14]))
    got = sl_select.cmd_select(env, "l1")
    drop = next(x for x in got["items"] if x["number"] == 11)
    assert len(env.merges) == 2 and got["metrics"]["selected"] == [13]
    assert drop["reason"] == "設計 PR がマージ待ち（検査が緑でない（queued））" and row(env, 11)["merge"]["streak"] == 2


def boundary(env, n=1):
    """周回の境のマージし直しだけを n 回打つ（選定は流さない）。"""
    for _ in range(n):
        with sl_record.open_record(env, "l1") as (r, before):
            sl_select.settle_approved_designs(env, r)
            sl_record.write_record(env, r, before)


def test_the_same_not_green_for_three_laps_is_given_back_to_the_user(env):
    gated_both(env)
    env.merge_box = {"result": "not_green", "class": "running", "reason": "走っている"}
    sl.cmd_answer(env, "l1", 11, True, None)
    boundary(env, 2)
    assert len(env.merges) == 3 and row(env, 11)["merge"]["streak"] == 3
    boundary(env)
    w = row(env, 11)
    assert len(env.merges) == 3 and not w.get("answer") and w["returned"] == "走っている" and "merge" not in w
    assert "利用者へ戻した理由: 走っている" in env.bodies[11]


@pytest.mark.parametrize("res, cls", [("failed", None), ("not_green", "stale_again")])
def test_results_that_time_does_not_solve_are_given_back_at_once(env, res, cls):
    gated_both(env)
    env.merge_box = {"result": res, "class": cls, "reason": "権限が無い"}
    sl.cmd_answer(env, "l1", 11, True, None)
    boundary(env)
    w = row(env, 11)
    assert len(env.merges) == 1 and not w.get("answer") and w["returned"] == "権限が無い"


def test_infra_reruns_are_counted_across_classes_and_capped(env):
    gated_both(env)
    env.merge_box = {"result": "not_green", "class": "infra", "reason": "基盤待ち", "infra_rerun": True}
    sl.cmd_answer(env, "l1", 11, True, None)
    env.merge_box = {"result": "not_green", "class": "running", "reason": "走っている"}
    boundary(env)  # 実行中に変わっても累計は 0 に戻さない
    env.merge_box = {"result": "not_green", "class": "infra", "reason": "基盤待ち", "infra_rerun": True}
    boundary(env, 2)
    assert row(env, 11)["merge"]["infra_reruns"] == 3 and len(env.merges) == 4
    boundary(env)
    w = row(env, 11)
    assert len(env.merges) == 4 and not w.get("answer") and "基盤待ち" in w["returned"]


def test_the_rerun_budget_is_passed_down_even_after_a_running_lap(env):
    gated_both(env)
    env.merge_box = {"result": "not_green", "class": "running", "reason": "走っている", "infra_rerun": 3}  # 1 回で 3 回打った
    sl.cmd_answer(env, "l1", 11, True, None)
    env.merge_box = {"result": "not_green", "class": "running", "reason": "走っている"}
    boundary(env, 2)  # 前の分類が実行中でも、残りの 0 を再実行する層へ渡す
    assert row(env, 11)["merge"]["infra_reruns"] == 3 and env.budgets[1:] == [0, 0]


def test_reopening_drops_the_previous_answerer(env):
    gated_both(env)
    w = {"answer": "approved", "answered_at": "t", "by": "mvv", "merge": {}, "merged_at": "t"}
    sl_gate._reopen_waiting(w)
    assert w == {}


def test_production_target_is_not_retried_and_waits_for_gate_2(env):
    gated_both(env)
    env.merge_box = {"result": "production", "class": None, "reason": "宛先 main が本番"}
    sl.cmd_answer(env, "l1", 11, True, None)
    boundary(env)
    assert len(env.merges) == 1 and not row(env, 11).get("merged_at")
    assert sl_select.exclusion_reason(env, rec(env), {"number": 11}, 2, set()) == sl_select.PRODUCTION_REASON
    env.prs[501] = "MERGED"
    boundary(env)
    assert len(env.merges) == 1 and row(env, 11)["merged_at"]


def test_head_moved_at_the_boundary_asks_again_with_the_new_head(env):
    gated_both(env)
    env.merge_box = {"result": "head_moved", "class": None, "reason": "動いた"}
    sl.cmd_answer(env, "l1", 11, True, None)
    env.heads[501] = "c" * 40
    boundary(env)
    w = row(env, 11)
    assert not w.get("answer") and w["stamp"] == "c" * 40 and w["restamped"] == HEAD and len(env.merges) == 1


def test_conflict_syncs_the_base_in_the_design_worktree_and_asks_again(env):
    gated_both(env)
    env.merge_box = {"result": "conflict", "class": "passed", "reason": "衝突", "branch": "design/11"}
    sl.cmd_answer(env, "l1", 11, True, None)
    base = env.run

    def run(cmd, cwd=None, stdin=None):
        if cmd[:3] == ["git", "worktree", "list"]:
            return 0, "worktree /m\nbranch refs/heads/develop\n\nworktree /wt/d11\nbranch refs/heads/design/11\n", ""
        if "base-sync" in cmd:
            env.calls.append([*cmd, cwd])
            env.heads[501] = "d" * 40
            return env.sync_code, "{}", ""
        return base(cmd, cwd=cwd, stdin=stdin)

    env.run, env.sync_code = run, 0
    boundary(env)
    w = row(env, 11)
    assert any("base-sync" in c and c[-1] == "/wt/d11" for c in env.calls)
    assert not w.get("answer") and w["stamp"] == "d" * 40


def test_conflict_that_base_sync_cannot_solve_aborts_and_gives_back(env):
    gated_both(env)
    env.merge_box = {"result": "conflict", "class": "passed", "reason": "衝突", "branch": "design/11"}
    sl.cmd_answer(env, "l1", 11, True, None)
    base = env.run

    def run(cmd, cwd=None, stdin=None):
        if cmd[:3] == ["git", "worktree", "list"]:
            return 0, "worktree /wt/d11\nbranch refs/heads/design/11\n", ""
        if "base-sync" in cmd:
            return 1, "{}", "衝突が残る"
        return base(cmd, cwd=cwd, stdin=stdin)

    env.run = run
    boundary(env)
    w = row(env, 11)
    assert ["git", "merge", "--abort"] in env.calls and not w.get("answer") and "衝突が残る" in w["returned"]


def lane_env(env, monkeypatch):
    env.answer_box = {}
    env.answers = lambda plan, ids: {i: env.answer_box.get(i, {"answer": None, "defer": None}) for i in ids}
    sent = []

    def lane_gate1(e, w, report, state, suffix="", stamp=None):
        stamp = stamp or sl_gate.design_head(e, w)
        sent.append({"suffix": suffix, "stamp": stamp, "returned": w.get("returned")})
        return {"request": f"la-g{w['pr']}-{stamp[:12]}{suffix}", "plan": "p1", "stamp": stamp, "report": report, "state": state}

    monkeypatch.setattr(sl_gate, "lane_gate1", lane_gate1)
    return sent


def test_lane_copies_the_organizer_merge_and_does_not_rewrite_an_mvv_approval(env, monkeypatch):
    sent = lane_env(env, monkeypatch)
    r = gated_both(env)
    rid = r["waiting"][0]["request"]
    assert rid == f"la-g501-{HEAD[:12]}" and sent[0]["suffix"] == ""
    merge = {"result": "merged", "class": "passed", "reason": "", "pr": 501, "head": HEAD, "at": "t", "infra_rerun": False}
    env.answer_box[rid] = {"answer": {"result": "approved", "merge": merge, "by": "mvv"}, "defer": None}
    env.answer_box[r["waiting"][1]["request"]] = {"answer": {"result": "rejected"}, "defer": None}
    env.calls.clear()
    env.rank_box["out"] = rank_out(ms("01 近い", [13, 11]))
    got = sl_select.cmd_select(env, "l1")
    w = row(env, 11)
    assert w["answer"] == "approved" and w["by"] == "mvv" and w["merged_at"] and w["merge"]["result"] == "merged"
    assert not any("gate" in c and c[-2:] == ["--pr", "501"] for c in env.calls)  # 承認の記録は mvv-gate.py が書いた
    assert env.merges == [] and got["metrics"]["selected"][0] == 11


def test_lane_gives_back_with_a_new_request_id_for_the_same_head(env, monkeypatch):
    sent = lane_env(env, monkeypatch)
    r = gated_both(env)
    rid = r["waiting"][0]["request"]
    failed = {"result": "failed", "class": None, "reason": "権限が無い", "pr": 501, "head": HEAD, "at": "t"}
    env.answer_box[rid] = {"answer": {"result": "approved", "merge": failed}, "defer": None}
    env.rank_box["out"] = rank_out(ms("01 近い", [13, 14]))
    sl_select.cmd_select(env, "l1")
    w = row(env, 11)
    assert not w.get("answer") and w["request"] == f"la-g501-{HEAD[:12]}-m2-1" and sent[-1]["returned"] == "権限が無い"


def test_lane_gives_back_twice_in_the_same_lap_with_different_request_ids(env, monkeypatch):
    lane_env(env, monkeypatch)
    w = {"number": 11, "pr": 501, "request": "x", "stamp": HEAD, "answer": "approved", "by": "mvv"}
    monkeypatch.setattr(sl_gate, "mark_issue", lambda e, rec, w: None)
    assert sl_gate.giveback_design(env, {}, w, "権限が無い", 2)
    first = w["request"]
    w.update(answer="approved", by="user")
    assert sl_gate.giveback_design(env, {}, w, "権限が無い", 2)
    assert (first, w["request"]) == (f"la-g501-{HEAD[:12]}-m2-1", f"la-g501-{HEAD[:12]}-m2-2") and "by" not in w


@pytest.mark.parametrize("rid", ["x", None])
def test_reasking_on_a_moved_head_drops_the_given_back_mark(env, monkeypatch, rid):
    sent = lane_env(env, monkeypatch)
    w = {"number": 11, "pr": 501, "request": rid, "stamp": "old", "returned": "権限が無い", "answer": "approved"}
    monkeypatch.setattr(sl_gate, "mark_issue", lambda e, rec, w: None)
    assert sl_gate.reask_design(env, {}, w, HEAD)
    assert "returned" not in w and all(s["returned"] is None for s in sent)


def test_regating_on_a_moved_head_drops_the_given_back_mark(env, monkeypatch):
    sent = lane_env(env, monkeypatch)
    w = {"number": 11, "pr": 501, "request": "x", "stamp": "0ld" * 4, "returned": "権限が無い"}
    assert sl_gate.regate1(env, w)
    assert "returned" not in w and sent[-1]["returned"] is None and w["restamped"] == "0ld" * 4


@pytest.mark.parametrize("returned, mark", [("権限が無い", True), (None, None)])
def test_lane_gate1_marks_a_given_back_request_so_the_organizer_does_not_self_approve(env, monkeypatch, returned, mark):
    import parallel_lane

    got = []
    monkeypatch.setattr(parallel_lane, "lane_of", lambda main: ("p1", Path("la")))
    monkeypatch.setattr(parallel_lane, "request_gate1", lambda *a, **kw: got.append(kw) or "rid")
    w = {"number": 11, "pr": 501, "verdict": "unknown", **({"returned": returned} if returned else {})}
    assert sl_gate.lane_gate1(env, w, "r.md", "s.json", stamp=HEAD)["request"] == "rid"
    assert got[0].get("returned") is mark


# ---------------------------------------------------------------- 現状固定: cmd_answer の分岐ごとの出力


@pytest.mark.parametrize(
    "box, after, merged",
    [
        ({"result": "merged", "class": "passed", "reason": ""}, "設計 PR をマージした。次の周回で先頭に選ぶ", True),
        ({"result": "already_merged", "class": "passed", "reason": ""}, "設計 PR をマージした。次の周回で先頭に選ぶ", True),
        (
            {"result": "production", "class": None, "reason": "宛先 main が本番"},
            "設計 PR をマージしない（宛先 main が本番）。リリース承認ゲートで人がマージするまで実装に選ばない",
            False,
        ),
        (
            {"result": "not_green", "class": "running", "reason": "走っている"},
            "設計 PR をマージできない（not_green: 走っている）。次の周回の境でマージし直す",
            False,
        ),
    ],
)
def test_characterize_answer_approved_with_a_design_pr_by_merge_result(env, box, after, merged):
    gated_both(env)
    env.merge_box = box
    out = sl.cmd_answer(env, "l1", 11, True, None)
    w = row(env, 11)
    assert out["status"] == "ok" and out["metrics"]["status"] == "answered" and "exit" not in out
    assert out["summary"] == f"#11 の答え（approved）を書いた。{after}"
    assert out["items"] == [w] and w["answer"] == "approved" and env.merges == [(501, HEAD)]
    assert {k: w["merge"][k] for k in ("result", "class", "reason", "lap", "streak", "infra_reruns", "stamp")} == {
        **{k: box[k] for k in ("result", "class", "reason")},
        "lap": 1,
        "streak": 1,
        "infra_reruns": 0,
        "stamp": HEAD,
    }
    assert bool(w.get("merged_at")) is merged
    assert any(c[2:4] == ["gate", w["state"]] and c[-2:] == ["--pr", "501"] and "approved" in c for c in env.calls)


def test_characterize_answer_approved_without_a_design_pr_does_not_merge(env):
    lap = built_lap(env)
    sl_gate.cmd_gate(env, "l1", write_done(env, [{"plan": str(Path(lap["out"]) / f"design-{n}.json"), "result": "関門"} for n in (11, 12)]))
    out = sl.cmd_answer(env, "l1", 11, True, None)
    w = row(env, 11)
    assert out["status"] == "ok" and out["metrics"]["status"] == "answered"
    assert out["summary"] == "#11 の答え（approved）を書いた。設計 PR のマージの後の周回で選定の対象に戻る"
    assert out["items"] == [w] and w["answer"] == "approved" and w["pr"] is None and "merge" not in w and env.merges == []
    gate = [c for c in env.calls if c[2:3] == ["gate"]]
    assert len(gate) == 1 and "--pr" not in gate[0] and gate[0][gate[0].index("--outcome") + 1] == "approved"


def test_characterize_answer_rejected_and_revise_summaries(env, tmp_path):
    gated_both(env)
    out = sl.cmd_answer(env, "l1", 12, False, None)
    assert out["status"] == "ok" and out["metrics"]["status"] == "answered"
    assert out["summary"] == "#12 の答え（rejected）を書いた。選定の対象に戻さない（差し戻し）"
    assert out["items"] == [row(env, 12)] and row(env, 12)["answer"] == "rejected"
    note = tmp_path / "n.md"
    note.write_text("  直す\n")
    out = sl.cmd_answer(env, "l1", 11, False, None, revise=True, note=str(note))
    w = row(env, 11)
    assert out["summary"] == "#11 の答え（revise）を書いた。次の周回で先頭に選び、指摘を読んで同じ設計 Pull Request を直す"
    assert out["items"] == [w] and w["note"] == "直す" and w["revise_count"] == 1 and env.merges == []


def test_characterize_answer_refusals_write_nothing(env, tmp_path):
    gated_both(env)
    before = rec(env)
    out = sl.cmd_answer(env, "l1", 99, True, None)
    assert out["status"] == "stopped" and out["exit"] == 1 and out["metrics"] == {"status": "refused"} and out["items"] == []
    assert out["summary"] == "#99 は人の判断待ちでない"
    missing = tmp_path / "none.md"
    out = sl.cmd_answer(env, "l1", 11, False, 777, revise=True, note=str(missing))
    assert out["status"] == "stopped" and out["exit"] == 1 and out["metrics"] == {"status": "refused"}
    assert out["summary"].startswith("指摘ファイルを読めない: ") and str(missing) in out["summary"]
    assert rec(env) == before and env.merges == []


def test_characterize_answer_pr_argument_is_written_to_the_row(env):
    gated_both(env)
    out = sl.cmd_answer(env, "l1", 12, False, 777)
    assert out["status"] == "ok" and row(env, 12)["pr"] == 777 and env.merges == []


def test_characterize_answer_appends_notes_after_the_row(env):
    gated_both(env)
    base = env.run

    def run(cmd, cwd=None, stdin=None):
        if cmd[2:3] == ["gate"]:
            env.calls.append(cmd)
            return 2, "", "書けない理由\n"
        return base(cmd, cwd=cwd, stdin=stdin)

    env.run = run
    out = sl.cmd_answer(env, "l1", 12, False, None)
    assert out["status"] == "ok" and out["items"] == [row(env, 12), {"note": "承認の記録を書けない（2）: 書けない理由"}]


# ---------------------------------------------------------------- active_seconds の現状固定


def _lap(s, e=None, **kw):
    return {"started_at": s, "ended_at": e, **kw}


@pytest.mark.parametrize(
    ("rec_", "expected"),
    [
        ({}, 0.0),
        ({"laps": [], "stops": []}, 0.0),
        ({"laps": [_lap("2026-10-25T00:00:00Z", "2026-10-25T01:00:00Z")]}, 3600.0),
        # 進行中の周回（終わりが無い）は数えない
        ({"laps": [_lap("2026-10-25T00:00:00Z")]}, 0.0),
        # 始まりが無い周回も数えない
        ({"laps": [_lap(None, "2026-10-25T01:00:00Z")]}, 0.0),
        # 終わりが始まりより前なら 0 に丸める
        ({"laps": [_lap("2026-10-25T01:00:00Z", "2026-10-25T00:00:00Z")]}, 0.0),
        # タイムゾーンの無い時刻は UTC として読む
        ({"laps": [_lap("2026-10-25T00:00:00", "2026-10-25T00:30:00Z")]}, 1800.0),
        # 複数の周回は足す
        (
            {"laps": [_lap("2026-10-25T00:00:00Z", "2026-10-25T01:00:00Z"), _lap("2026-10-25T02:00:00Z", "2026-10-25T02:30:00Z")]},
            5400.0,
        ),
    ],
)
def test_characterize_active_seconds_laps(rec_, expected):
    assert sl_record.active_seconds(rec_) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("stops", "expected"),
    [
        # 周回の途中の停止は重なりを引く
        ([{"at": "2026-10-25T00:10:00Z", "resumed_at": "2026-10-25T00:20:00Z"}], 3000.0),
        # 再開していない停止は引かない
        ([{"at": "2026-10-25T00:10:00Z"}], 3600.0),
        ([{"at": None, "resumed_at": "2026-10-25T00:20:00Z"}], 3600.0),
        # 周回の外の停止は引かない
        ([{"at": "2026-10-25T02:00:00Z", "resumed_at": "2026-10-25T03:00:00Z"}], 3600.0),
        # 周回にまたがる停止は重なった分だけ引く
        ([{"at": "2026-10-24T23:00:00Z", "resumed_at": "2026-10-25T00:15:00Z"}], 2700.0),
        # 周回より長い停止は 0 に丸める
        ([{"at": "2026-10-24T23:00:00Z", "resumed_at": "2026-10-25T02:00:00Z"}], 0.0),
        # 重なる停止は別々に引き、負になれば 0 に丸める
        (
            [
                {"at": "2026-10-25T00:00:00Z", "resumed_at": "2026-10-25T00:40:00Z"},
                {"at": "2026-10-25T00:20:00Z", "resumed_at": "2026-10-25T01:00:00Z"},
            ],
            0.0,
        ),
        (
            [
                {"at": "2026-10-25T00:00:00Z", "resumed_at": "2026-10-25T00:10:00Z"},
                {"at": "2026-10-25T00:30:00Z", "resumed_at": "2026-10-25T00:40:00Z"},
            ],
            2400.0,
        ),
    ],
)
def test_characterize_active_seconds_subtracts_stop_overlap(stops, expected):
    r = {"laps": [_lap("2026-10-25T00:00:00Z", "2026-10-25T01:00:00Z")], "stops": stops}
    assert sl_record.active_seconds(r) == pytest.approx(expected)


_FIN_R = {"started_at": "2026-10-26T00:00:00Z", "ended_at": "2026-10-26T00:20:00Z"}
_FIN_Q = {"started_at": "2026-10-26T00:00:00Z", "ended_at": "2026-10-26T00:05:00Z"}


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        # held_at で区切り、release.finish の区間を足す。ended_at は使わない
        ({"release": {"finish": _FIN_R}}, 1800.0 + 1200.0),
        # release が無ければ question.finish の区間を足す
        ({"question": {"finish": _FIN_Q}}, 1800.0 + 300.0),
        # 両方あれば release を使う
        ({"release": {"finish": _FIN_R}, "question": {"finish": _FIN_Q}}, 1800.0 + 1200.0),
        # release に finish が無ければ question へ移る
        ({"release": {}, "question": {"finish": _FIN_Q}}, 1800.0 + 300.0),
        ({"release": None, "question": None}, 1800.0),
        # 残りを流している最中（終わりが無い）は足さない
        ({"release": {"finish": {"started_at": "2026-10-26T00:00:00Z"}}}, 1800.0),
    ],
)
def test_characterize_active_seconds_held_lap(extra, expected):
    lap = _lap("2026-10-25T00:00:00Z", "2026-10-27T00:00:00Z", held_at="2026-10-25T00:30:00Z", **extra)
    assert sl_record.active_seconds({"laps": [lap]}) == pytest.approx(expected)


def test_characterize_active_seconds_held_lap_subtracts_stops_from_each_span():
    lap = _lap("2026-10-25T00:00:00Z", None, held_at="2026-10-25T00:30:00Z", release={"finish": _FIN_R})
    stops = [
        {"at": "2026-10-25T00:20:00Z", "resumed_at": "2026-10-26T00:10:00Z"},  # 両方の区間にまたがる
    ]
    assert sl_record.active_seconds({"laps": [lap], "stops": stops}) == pytest.approx(1200.0 + 600.0)


def test_characterize_active_seconds_without_held_at_ignores_finish():
    lap = _lap("2026-10-25T00:00:00Z", "2026-10-25T00:30:00Z", release={"finish": _FIN_R})
    assert sl_record.active_seconds({"laps": [lap]}) == pytest.approx(1800.0)


# ---------------------------------------------------------------- 現状固定: _select_candidate_rows（1 マイルストーンの候補の採否）


def _cand(number, rank, **extra):
    return {"number": number, "rank": rank, **extra}


def _rows(here):
    return [(r["number"], r["decision"], r["reason"]) for r in here]


def test_characterize_select_candidate_rows_fills_max_issues_by_rank_and_drops_the_rest_as_capacity():
    cands = [_cand(10, 1), _cand(11, 2), _cand(12, 3)]
    picked, here = sl_select._select_candidate_rows(cands, "M", 2, lambda r: None)
    assert picked == [10, 11]
    assert _rows(here) == [(10, sl_select.SELECTED, "順位"), (11, sl_select.SELECTED, "順位"), (12, sl_select.DROPPED, "容量")]
    assert here[0] == {"number": 10, "milestone": "M", "rank": 1, "decision": sl_select.SELECTED, "reason": "順位"}


def test_characterize_select_candidate_rows_excluded_rows_keep_their_reason_and_do_not_use_capacity():
    cands = [_cand(10, 1), _cand(11, 2), _cand(12, 3)]
    picked, here = sl_select._select_candidate_rows(cands, "M", 1, lambda r: "差し戻し" if r["number"] == 10 else None)
    assert picked == [11]
    assert _rows(here) == [(10, sl_select.DROPPED, "差し戻し"), (11, sl_select.SELECTED, "順位"), (12, sl_select.DROPPED, "容量")]


def test_characterize_select_candidate_rows_calls_reason_for_once_per_candidate_in_order():
    seen = []
    cands = [_cand(10, 1), _cand(11, 2), _cand(12, 3)]
    sl_select._select_candidate_rows(cands, "M", 0, lambda r: seen.append(r["number"]))
    assert seen == [10, 11, 12]


def test_characterize_select_candidate_rows_zero_max_issues_drops_every_open_row_as_capacity():
    cands = [_cand(10, 1), _cand(11, 2)]
    picked, here = sl_select._select_candidate_rows(cands, "M", 0, lambda r: None, first={11})
    assert picked == []
    assert _rows(here) == [(11, sl_select.DROPPED, "容量"), (10, sl_select.DROPPED, "容量")]


def test_characterize_select_candidate_rows_first_issues_take_capacity_first_and_move_to_the_front():
    cands = [_cand(10, 1), _cand(11, 2), _cand(12, 3)]
    picked, here = sl_select._select_candidate_rows(cands, "M", 2, lambda r: None, first={12})
    assert picked == [12, 10]
    assert _rows(here) == [(12, sl_select.SELECTED, "直しの先頭"), (10, sl_select.SELECTED, "順位"), (11, sl_select.DROPPED, "容量")]


def test_characterize_select_candidate_rows_excluded_first_issue_keeps_its_rank_position_and_reason():
    cands = [_cand(10, 1), _cand(11, 2)]
    picked, here = sl_select._select_candidate_rows(cands, "M", 1, lambda r: "人の判断待ち" if r["number"] == 11 else None, first={11})
    assert picked == [10]
    assert _rows(here) == [
        (10, sl_select.SELECTED, "順位"),
        (11, sl_select.DROPPED, "人の判断待ち"),
    ]  # 前へ移るのは外されなかった先頭の課題だけ


def test_characterize_select_candidate_rows_quality_slot_takes_capacity_before_rank_but_keeps_rank_order():
    cands = [_cand(10, 1), _cand(11, 2), _cand(12, 3, quality_slot=True)]
    picked, here = sl_select._select_candidate_rows(cands, "M", 2, lambda r: None, first={11})
    assert picked == [11, 12]
    assert _rows(here) == [(11, sl_select.SELECTED, "直しの先頭"), (10, sl_select.DROPPED, "容量"), (12, sl_select.SELECTED, "品質の枠")]


def test_characterize_select_candidate_rows_approved_reason_wins_over_first_and_quality_slot():
    cands = [_cand(10, 1, quality_slot=True), _cand(11, 2), _cand(12, 3)]
    picked, here = sl_select._select_candidate_rows(cands, "M", 3, lambda r: None, first={11}, approved={10, 11})
    assert picked == [11, 10, 12]
    assert _rows(here) == [
        (11, sl_select.SELECTED, sl_select.APPROVED_REASON),
        (10, sl_select.SELECTED, sl_select.APPROVED_REASON),
        (12, sl_select.SELECTED, "順位"),
    ]


def test_characterize_select_candidate_rows_approved_alone_does_not_take_capacity_first():
    cands = [_cand(10, 1), _cand(11, 2)]
    picked, here = sl_select._select_candidate_rows(cands, "M", 1, lambda r: None, approved={11})
    assert picked == [10]
    assert _rows(here) == [(10, sl_select.SELECTED, "順位"), (11, sl_select.DROPPED, "容量")]


def test_characterize_select_candidate_rows_empty_candidates():
    assert sl_select._select_candidate_rows([], "M", 2, lambda r: None) == ([], [])


# ---------------- #2354: 開発版を決めずに組み、周回の版は版の記録からコピーする ----------------


def versionless_lap(env):
    """--dev なしで組んだ周回。スプリント状態の版の欄は sprint-state.py init と同じく空文字である。"""
    start(env)
    sl_select.cmd_select(env, "l1")
    out = sl.cmd_sprint(env, "l1", "standard", None, None, [])
    assert out["status"] == "ok"
    lap = sl.current_lap(rec(env))
    versionless_state(lap)
    return lap


def versionless_state(lap, result="完了"):
    row = {"result": result, "cost": 1.0, "seconds": 10}
    state = {"plans": [{"kind": "実装", "issues": [11], "row": row}], "versions": {"dev": "", "prod": ""}, "gates": []}
    Path(lap["state_path"]).write_text(json.dumps(state))


def plan_state(plan, version=None, result=None):
    """プランの状態ディレクトリへ版の記録と report.md を置く。"""
    sd = Path(plan).with_name(Path(plan).stem + "-state")
    sd.mkdir(exist_ok=True)
    if version:
        (sd / "version.json").write_text(json.dumps({"version": version}))
    if result:
        (sd / "report.md").write_text(f"- 結果: {result}\n")
    return sd


def lap_table(env):
    return (env.root / ".ndf" / "loop" / "l1" / "laps.md").read_text()


def test_sprint_without_dev_passes_no_version_to_init_or_new_sprint(env):
    """受け入れ条件 1・2・I1: 省いたら版数をどのコマンドにも周回の組み立てにも持たない。"""
    lap = versionless_lap(env)
    init = next(c for c in env.calls if "init" in c)
    new = next(c for c in env.calls if "new" in c and "sprint" in c)
    assert "--dev" not in init and "--prod" not in init and "--version" not in new
    assert "--version" not in lap["sprint_command"] and lap["versions"] == {"dev": "", "prod": ""}
    assert lap["dev_plan"].endswith("/dev.json") and lap["prod_plan"].endswith("/prod.json")


def test_sprint_treats_an_empty_dev_as_omitted(env):
    start(env)
    sl_select.cmd_select(env, "l1")
    assert sl.cmd_sprint(env, "l1", "standard", "", None, [])["status"] == "ok"
    assert "--version" not in sl.current_lap(rec(env))["sprint_command"]


@pytest.mark.parametrize("prod, want", [(None, "1.0.0"), ("1.0.1", "1.0.1")])
def test_sprint_with_dev_keeps_the_explicit_version(env, prod, want):
    """受け入れ条件 5・6・I2: --dev を渡す組み立てと周回の版は変わらない。"""
    start(env)
    sl_select.cmd_select(env, "l1")
    sl.cmd_sprint(env, "l1", "standard", "1.0.0-dev.1", prod, [])
    lap = sl.current_lap(rec(env))
    assert lap["versions"] == {"dev": "1.0.0-dev.1", "prod": want}
    init = next(c for c in env.calls if "init" in c)
    assert init[init.index("--dev") + 1] == "1.0.0-dev.1" and init[init.index("--prod") + 1] == want
    assert "--version 1.0.0-dev.1" in lap["sprint_command"]


def test_prod_without_dev_is_refused_before_anything_changes(env):
    """受け入れ条件 7・I3: --prod だけなら 1 で断り、状態ファイルも new sprint も周回の記録の変更も無い。"""
    start(env)
    sl_select.cmd_select(env, "l1")
    before = rec(env)
    env.calls.clear()
    out = sl.cmd_sprint(env, "l1", "standard", None, "1.0.0", [])
    assert out["exit"] == 1 and out["metrics"]["status"] == "refused" and "--prod は --dev と一緒に渡す" in out["summary"]
    assert env.calls == [] and rec(env) == before
    assert not Path(sl.current_lap(before)["dir"], "state.json").exists()


def test_select_next_and_rebuild_next_do_not_ask_for_a_version(env):
    """受け入れ条件 11: 案内の 1 行は --dev を求めず、モードを埋めれば組める。"""
    start(env)
    nxt = sl_select.cmd_select(env, "l1")["metrics"]["next"]
    line = re.search(r"python3 \S+ (loop sprint l1 --mode <モード>)", nxt)
    assert line and "--dev" not in nxt
    assert sl.cmd_sprint(env, "l1", "standard", None, None, [])["status"] == "ok"


def test_sprint_help_shows_dev_as_optional():
    """受け入れ条件 12。"""
    got = subprocess.run([sys.executable, str(SCRIPTS / "sprint-state.py"), "loop", "sprint", "--help"], capture_output=True, text=True)
    assert got.returncode == 0 and "[--dev DEV]" in " ".join(got.stdout.split())


def test_closing_copies_the_dev_version_from_the_record(env):
    """受け入れ条件 9・I4・I7: 版の記録の版を締めた周回が持ち、記録は変わらない。本番へ進まなければ本番の版は空。"""
    lap = versionless_lap(env)
    sd = plan_state(lap["dev_plan"], "10.27.7-dev.1")
    kept = (sd / "version.json").read_text()
    sl.cmd_lap(env, "l1", False, None, [])
    closed = rec(env)["laps"][0]
    assert closed["versions"] == {"dev": "10.27.7-dev.1", "prod": ""}
    assert "10.27.7-dev.1" in lap_table(env) and (sd / "version.json").read_text() == kept


def test_prod_version_is_not_copied_when_the_plan_stopped_at_the_release_gate(env):
    """I4: 本番のプランの版の記録があっても、結果が完了でなければ本番を出していない。"""
    lap = versionless_lap(env)
    plan_state(lap["prod_plan"], "10.27.7", "関門")
    sl.cmd_lap(env, "l1", False, None, [])
    assert rec(env)["laps"][0]["versions"]["prod"] == ""


def test_prod_version_is_copied_when_the_continued_plan_finished(env):
    """I4: 続きの実行はスプリント状態の行を変えないが、report.md が完了なら本番の版が載る。"""
    lap = versionless_lap(env)
    versionless_state(lap, result="関門")
    plan_state(lap["dev_plan"], "10.27.7-dev.1")
    plan_state(lap["prod_plan"], "10.27.7", "完了")
    sl.cmd_lap(env, "l1", False, None, [])
    closed = rec(env)["laps"][0]
    assert closed["versions"] == {"dev": "10.27.7-dev.1", "prod": "10.27.7"}
    assert "10.27.7-dev.1 / 10.27.7" in lap_table(env)


@pytest.mark.parametrize("broken", [None, "{", '{"version": 1}'])
def test_closing_without_a_usable_record_leaves_the_version_empty(env, broken):
    """受け入れ条件 10・I5: 版の記録が無い・読めなくても周回は締まり、版の欄は空のまま。"""
    lap = versionless_lap(env)
    if broken:
        plan_state(lap["dev_plan"]).joinpath("version.json").write_text(broken)
    out = sl.cmd_lap(env, "l1", False, None, [])
    closed = rec(env)["laps"][0]
    assert out["status"] == "ok" and closed["outcome"] == "完了" and closed["versions"] == {"dev": "", "prod": ""}


def test_closing_a_lap_without_dev_plan_keeps_its_versions(env):
    """移行性: dev_plan を持たない進行中の周回は今の値で締まる。"""
    start(env)
    sl_select.cmd_select(env, "l1")
    sl.cmd_sprint(env, "l1", "standard", "1.0.0-dev.1", None, [])
    path = env.root / ".ndf" / "loop" / "l1" / "loop.json"
    r = json.loads(path.read_text())
    del r["laps"][-1]["dev_plan"]
    path.write_text(json.dumps(r))
    Path(r["laps"][-1]["state_path"]).unlink()
    sl.cmd_lap(env, "l1", False, None, [])
    assert rec(env)["laps"][0]["versions"] == {"dev": "1.0.0-dev.1", "prod": "1.0.0"}


def versionless_request(env):
    lap = lane_lap(env, dev=None)
    versionless_state(lap)
    plan_state(lap["dev_plan"], "10.27.7-dev.1")
    sl.cmd_lap(env, "l1", False, write_done(env, [{"plan": str(Path(lap["out"]) / "prod.json"), "result": "関門"}]), [])
    return lap


def test_held_lap_keeps_the_copied_dev_and_the_answered_prod(env):
    """I6: 開発版を渡さない周回のリリース待ちは、締め直しでも開発版と答えの本番の版を失わない。"""
    versionless_request(env)
    assert rec(env)["laps"][0]["versions"] == {"dev": "10.27.7-dev.1", "prod": ""}
    env.answer_box[REQ] = {"answer": {"result": "released", "version": "10.27.7"}, "defer": None}
    sl_select.cmd_select(env, "l1")
    d = rec(env)["laps"][0]["release"]["finish"]["done"]
    Path(d).write_text(json.dumps({"items": [{"plan": "x", "result": "完了"}]}))
    sl.cmd_finish(env, "l1", 1, d)
    closed = rec(env)["laps"][0]
    assert closed["outcome"] == "完了" and closed["versions"] == {"dev": "10.27.7-dev.1", "prod": "10.27.7"}


def test_held_lap_keeps_the_dev_version_when_the_record_is_gone_on_the_second_close(env):
    """I6: 開発版を渡したかはスプリント状態で見るため、締め直しで周回の開発版を失わない。"""
    lap = versionless_request(env)
    (Path(lap["dev_plan"]).with_name("dev-state") / "version.json").unlink()
    env.answer_box[REQ] = {"answer": {"result": "released", "version": None}, "defer": None}
    sl_select.cmd_select(env, "l1")
    d = rec(env)["laps"][0]["release"]["finish"]["done"]
    Path(d).write_text(json.dumps({"items": [{"plan": "x", "result": "完了"}]}))
    sl.cmd_finish(env, "l1", 1, d)
    assert rec(env)["laps"][0]["versions"]["dev"] == "10.27.7-dev.1"
