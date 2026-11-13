"""組み換え（lib/reshuffle.py・progress_activities.act_reshuffle。#3092 の受け入れ条件 18〜21）。Claude は起動しない。"""

from __future__ import annotations

import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))
import progress_activities as acts  # noqa: E402
import progress_db as pdb  # noqa: E402
import progress_model as pm  # noqa: E402
import reshuffle  # noqa: E402
import workflow_def as wd  # noqa: E402

D = pm.EventDraft


def _world(tmp_path):
    store = pdb.connect(f"sqlite:///{tmp_path}/p.sqlite")
    with pdb.transaction(store) as c:
        plan = pdb.create_table(
            c,
            "plan",
            "r",
            "p",
            None,
            None,
            [
                D("created", "organizer", payload={"max_lanes": 3}),
                D("lane_added", "organizer", payload={"lane": "a", "touches": ["a/"]}),
                D("lane_added", "organizer", payload={"lane": "b", "touches": ["b/"]}),
                D("plan_started", "organizer"),
            ],
        )
        lane = pdb.create_table(
            c,
            "lane",
            "r",
            "p/a",
            plan,
            None,
            [D("created", "organizer"), D("created", "organizer", "1", payload={"row": 1, "issues": [1]})],
        )
        pdb.create_table(
            c,
            "lane",
            "r",
            "p/b",
            plan,
            None,
            [D("created", "organizer"), D("created", "organizer", "1", payload={"row": 1, "issues": [5]})],
        )
        sid = pdb.create_table(c, "sprint", "r", "s", lane, {"snapshot": {}}, [D("created", "conductor", None, 1, payload={"lane_row": 1})])
        pdb.lane_write(c, lane, D("row_started", "conductor", "1", payload={"row": 1, "sprint": sid}))
    return store, plan, lane, sid


def test_reshuffle_stage_follows_retro_in_definitions():
    for mode in ("operation", "legacy-refactor", "standard", "documentation"):
        names = wd.load_mode_definition(mode).stage_names
        assert names.index("組み換え") == names.index("振り返り") + 1 and names.index("棚卸し") == names.index("組み換え") + 1
    assert "組み換え" not in wd.load_mode_definition("light").stage_names


def test_needs_reshuffle_is_pure():
    assert not reshuffle.needs_reshuffle({}, None, {})
    assert reshuffle.needs_reshuffle({"dropped": [3]}, None, {})
    plan = {"lanes": {"a": {}, "b": {}}, "max_lanes": None}
    lanes = {
        "a": [{"row": 1, "issues": [], "touches": ["x/"], "after": [], "state": pm.PENDING}],
        "b": [{"row": 1, "issues": [], "touches": ["x/"], "after": [], "state": pm.PENDING}],
    }
    assert reshuffle.needs_reshuffle({"lane": "a"}, plan, lanes)  # 入力が空でも重なりがあれば真


def test_unchanged_without_claude(tmp_path):
    store, plan, lane, sid = _world(tmp_path)
    calls = []
    out = acts.act_reshuffle(store, sid, {"reshuffle": {}}, propose=lambda *a: calls.append(a) or {})
    assert out["decision"] == "unchanged" and calls == []  # 受け入れ条件 19
    snap = wd.load_mode_definition("standard")
    assert wd.validate_io(snap, snap.stage("組み換え").activities[0].output_ref, out) == []


def test_apply_inside_lane_request_across_and_refused(tmp_path):
    store, plan, lane, sid = _world(tmp_path)
    proposal = {
        "lane_changes": [
            {"op": "add", "issues": [2], "reason": "外した課題を次へ"},
            {"op": "cancel", "row": 1, "reason": "実行中の行を消す案"},
        ],
        "requests": [{"op": "add", "issues": [3], "lane": "b", "reason": "別のレーンへ"}],
    }
    out = acts.act_reshuffle(store, sid, {"reshuffle": {"carry": [2]}}, propose=lambda inp, rows: proposal)
    assert out["decision"] == "changed"
    assert [c["issues"] for c in out["lane_changes"]] == [[2]]  # 受け入れ条件 20
    assert len(out["refused"]) == 1 and "I12a" in out["refused"][0]["reason"]  # 受け入れ条件 21
    assert out["requests"][0]["lane"] == "b"
    with pdb.transaction(store) as c:
        view, lanes, _ = pdb.plan_and_lanes(c, plan)
    assert [r["issues"] for r in lanes["a"]] == [[1], [2]]
    opened = [q for q in view["requests"].values() if q["open"]]
    assert len(opened) == 2  # レーンをまたぐ案と断られた案がオーガナイザーへの依頼になる
