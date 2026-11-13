"""開発ループのモードの判定の根拠と決め直し（`loop sprint --judgments`・設計承認ゲートの続き。#2923）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_sprint_loop as base  # noqa: E402
from test_sprint_loop import judgments_file, ms, rank_out, rec, sl, sl_gate, sl_record, sl_select, sprint, start, write_done  # noqa: E402

env, mvv = base.env, base.mvv  # フィクスチャを受け継ぐ


def selected(env, nums):
    env.rank_box["out"] = rank_out(ms("01 近い", list(nums)))
    start(env)
    sl_select.cmd_select(env, "l1")


def record_bytes(env):
    return sl_record.loop_record_path(env, "l1").read_bytes()


def new_sprints(env):
    """ループの 1 回の進行表を作る呼び出し（progress.py new。#3092）と、モードを下げたときの作り直しの new sprint。"""
    return [c for c in env.calls if "new" in c and (str(sl_record.PROGRESS) in c or c[c.index("new") + 1] == "sprint")]


def syncs(env):
    return [c[2:] for c in env.calls if c[:1] == ["bash"] and c[1].endswith("projects-sync.sh")]


def gate_out(env, issue):
    lap = sl.current_lap(rec(env))
    return sl_gate.cmd_gate(env, "l1", write_done(env, [{"plan": str(Path(lap["out"]) / f"design-{issue}.json"), "result": "関門"}]))


# ---------------- 受け入れ条件 1・I1〜I3・I8: 根拠の無いモードで組まない ----------------


def test_a_missing_judgment_refuses_without_touching_the_record_or_new_sprint(env):
    selected(env, [11, 12])
    before = record_bytes(env)
    out = sl.cmd_sprint(env, "l1", None, None, None, [], judgments_file(env, {11: "standard"}))
    assert out["exit"] == 1 and out["metrics"]["status"] == "refused"
    assert any("#12" in i["reason"] for i in out["items"])
    assert record_bytes(env) == before and not new_sprints(env)


def test_no_judgments_file_and_an_unreadable_one_refuse(env):
    selected(env, [11])
    before = record_bytes(env)
    assert sl.cmd_sprint(env, "l1", "standard", None, None, [], None)["exit"] == 1
    (env.root / "broken.json").write_text("{")
    assert sl.cmd_sprint(env, "l1", None, None, None, [], str(env.root / "broken.json"))["exit"] == 1
    assert record_bytes(env) == before and not new_sprints(env)


def test_a_mode_that_disagrees_with_the_judgments_is_refused(env):
    selected(env, [11, 12])
    before = record_bytes(env)
    out = sl.cmd_sprint(env, "l1", "light", None, None, [], judgments_file(env, {11: "standard", 12: "light"}))
    assert out["exit"] == 1 and record_bytes(env) == before and not new_sprints(env)


def test_documentation_mixed_with_another_mode_is_refused_with_both_issues(env):
    selected(env, [11, 12])
    before = record_bytes(env)
    out = sl.cmd_sprint(env, "l1", None, None, None, [], judgments_file(env, {11: "documentation", 12: "standard"}))
    reason = " ".join(i["reason"] for i in out["items"])
    assert out["exit"] == 1 and "#11" in reason and "#12" in reason
    assert record_bytes(env) == before and not new_sprints(env)
    assert sprint(env, "l1", None, None, None, [], modes={11: "documentation", 12: "documentation"})["status"] == "ok"


# ---------------- 受け入れ条件 4・7: 根拠とモードを残す ----------------


def test_the_sprint_mode_is_the_highest_row_and_is_recorded_with_the_judgments(env):
    selected(env, [11, 12])
    out = sprint(env, "l1", None, None, None, ["--x"], modes={11: "light", 12: "standard"})
    assert out["status"] == "ok" and out["metrics"]["mode"] == "standard" and out["metrics"]["judgments"] == {"11": 5, "12": 3}
    lap = sl.current_lap(rec(env))
    assert lap["mode"] == "standard" and [x["number"] for x in lap["judgments"]] == [11, 12]
    assert lap["judgments"][0] == {"number": 11, "row": 5, "mode": "light", "condition": "条件", "files": ["src/11.py"]}
    assert [(d["trigger"], d["issues"], d["from_mode"], d["mode"]) for d in lap["mode_decisions"]] == [
        ("loop sprint", [11, 12], None, "standard")
    ]
    assert lap["sprint_extra"] == ["--x"]
    new = new_sprints(env)[0]
    assert new[new.index("--mode") + 1] == "standard"
    assert syncs(env) == [["11", "mode", "standard"], ["12", "mode", "standard"]]


def test_a_failed_mode_sync_is_noted_and_does_not_stop_the_sprint(env):
    selected(env, [11])
    run = env.run
    env.run = lambda cmd, cwd=None, stdin=None: (1, "", "gh が無い") if cmd[:1] == ["bash"] else run(cmd, cwd, stdin)
    out = sprint(env, "l1", "light", None, None, [])
    assert out["status"] == "ok" and any("#11" in i.get("note", "") for i in out["items"])


# ---------------- 受け入れ条件 5・6・I4・I5: 課題を外したら決め直す ----------------


def designed_only_manifest(env):
    """作り直しの new sprint（--designed だけ）は設計のステージを持たないマニフェストを書く。"""
    run = env.run

    def wrapped(cmd, cwd=None, stdin=None):
        code, so, se = run(cmd, cwd, stdin)
        if "new" in cmd and cmd[cmd.index("new") + 1] == "sprint" and "--design" not in cmd:
            m = Path(cmd[cmd.index("--out") + 1]) / "sprint.json"
            stages = json.loads(m.read_text())["ステージ"]
            m.write_text(json.dumps({"ステージ": [s for s in stages if s["name"] not in ("設計", "設計承認ゲート")]}, ensure_ascii=False))
        return code, so, se

    env.run = wrapped


def test_dropping_the_standard_issue_lowers_the_mode_and_rebuilds_the_rest(env):
    selected(env, [11, 12])
    sprint(env, "l1", None, None, None, ["--x"], modes={11: "standard", 12: "light"})
    designed_only_manifest(env)
    env.calls.clear()
    out = gate_out(env, 11)
    assert out["status"] == "ok"
    lap = sl.current_lap(rec(env))
    assert lap["mode"] == "light" and lap["manifest"].endswith("sprint-2/sprint.json")
    d = lap["mode_decisions"][-1]
    assert (d["trigger"], d["issues"], d["from_mode"], d["mode"]) == ("設計承認ゲート", [12], "standard", "light") and d["at"]
    new = new_sprints(env)
    assert len(new) == 1 and new[0][new[0].index("--mode") + 1] == "light" and "--design" not in new[0]
    assert new[0][new[0].index("--designed") + 1 :][:1] == ["12"] and new[0][-1] == "--x"
    close = next(c for c in env.calls if "new" in c and c[c.index("new") + 1] == "close")
    assert close[close.index("--mode") + 1] == "light"
    q = out["metrics"]["queue"]
    assert "sprint-2/impl-12.json" in q and "design-12.json" not in q and "impl-11.json" not in q
    assert ["12", "mode", "light"] in syncs(env)


def test_dropping_one_of_two_standard_issues_keeps_the_mode_and_still_records(env):
    selected(env, [11, 12])
    sprint(env, "l1", "standard", None, None, [])
    env.calls.clear()
    assert gate_out(env, 11)["status"] == "ok"
    lap = sl.current_lap(rec(env))
    assert lap["mode"] == "standard" and not new_sprints(env)
    d = lap["mode_decisions"][-1]
    assert (d["trigger"], d["issues"], d["from_mode"], d["mode"]) == ("設計承認ゲート", [12], "standard", "standard")
    assert syncs(env) == []  # モードが変わらなければ本文とボードへ書き直さない


def test_a_failed_rebuild_keeps_the_mode_manifest_and_decisions_and_stops_refused(env):
    selected(env, [11, 12])
    sprint(env, "l1", None, None, None, [], modes={11: "standard", 12: "light"})
    before = sl.current_lap(rec(env))
    run = env.run
    env.run = lambda cmd, cwd=None, stdin=None: (1, "", "断った") if "--designed" in cmd and "--design" not in cmd else run(cmd, cwd, stdin)
    out = gate_out(env, 11)
    r = rec(env)
    lap = next(x for x in r["laps"] if x["n"] == before["n"])
    assert r["status"] == "stopped" and r["stop"]["condition"] == "refused" and out["status"] != "ok"
    assert (lap["mode"], lap["manifest"], lap["mode_decisions"]) == (before["mode"], before["manifest"], before["mode_decisions"])


# ---------------- I6・移行性: 根拠の無い記録 ----------------


def test_a_lap_without_judgments_continues_without_redeciding(env):
    selected(env, [11, 12])
    sprint(env, "l1", "standard", None, None, [])
    with sl_record.open_record(env, "l1") as (r, before):
        lap = sl.current_lap(r)
        for k in ("judgments", "mode_decisions", "sprint_extra"):
            lap.pop(k)
        sl_record.write_record(env, r, before)
    env.calls.clear()
    assert gate_out(env, 11)["status"] == "ok"
    lap = sl.current_lap(rec(env))
    assert "mode_decisions" not in lap and lap["mode"] == "standard" and not new_sprints(env) and not syncs(env)
