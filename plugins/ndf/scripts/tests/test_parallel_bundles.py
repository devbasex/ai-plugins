"""課題のまとまり（#3081）: 計画の検査（I1〜I4・I5a・I5b・I7）・`plan.md` の表・レーンのまとまりの読み出し（`bundles_of`）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
import parallel_bundles as pb  # noqa: E402
import parallel_homes  # noqa: E402
import parallel_plan as pp  # noqa: E402


def RUNNING(_main, _name):  # noqa: N802  動いている計画の耐久ワークフローの状態
    return "PENDING"


def plan(*lanes, name="p1"):
    return {"version": 1, "name": name, "rules": [], "lanes": list(lanes)}


def sprint(name, issues, bundles=None, **over):
    inp = {"kind": "sprint", "issues": issues, **({"bundles": bundles} if bundles is not None else {})}
    return {"name": name, "input": inp, "plans": 1, "touches": [f"{name}/"], **over}


def errors(data):
    return [it for it in pp.check_data(data)[1] if it["level"] == "error"]


def rules(items, rule):
    return [it for it in items if it["rule"] == rule]


# ---- 形の検査（I1〜I4・I7） ----


def test_an_issue_in_two_bundles_is_a_bundle_error_and_init_writes_nothing():
    """受け入れ条件 2・I1: 同じ課題を 2 つのまとまりに書くと規則 bundle の誤りで、計画を返さない（init は書かない）。"""
    data = plan(sprint("s1", [1, 2, 3], [{"name": "a", "issues": [1, 2]}, {"name": "b", "issues": [2, 3]}]))
    got, items, metrics = pp.check_data(data)
    assert got is None and metrics["errors"] == 1
    (it,) = items
    assert it["rule"] == "bundle" and it["at"].startswith("lanes.0.input") and "#2" in it["message"]


def test_an_issue_outside_the_lane_is_a_bundle_error():
    """I2: `input.issues` に無い課題をまとまりに書くと規則 bundle の誤り。"""
    (it,) = errors(plan(sprint("s1", [1, 2], [{"name": "a", "issues": [1, 9]}])))
    assert it["rule"] == "bundle" and "#9" in it["message"]


@pytest.mark.parametrize(
    "bundles",
    [
        [{"name": "Bad Name", "issues": [1]}],
        [{"name": "a" * 33, "issues": [1]}],
        [{"name": "a", "issues": [1]}, {"name": "a", "issues": [2]}],
        [{"name": "issue-2", "issues": [1]}],  # I4 が #2 に補う名前と重なる
    ],
)
def test_bad_or_repeated_names_in_one_lane_are_bundle_errors(bundles):
    """I3: 名前の形が違う・同じレーンの中で名前が重なる（補う名前を含む）と規則 bundle の誤り。"""
    assert [it["rule"] for it in errors(plan(sprint("s1", [1, 2], bundles)))] == ["bundle"]


def test_same_names_in_lanes_of_other_repos_are_allowed(tmp_path):
    """I3: 一意はレーンの中だけで見る。別のリポジトリの同じ番号の課題を持つ 2 レーンに `bundles: []` を書いても誤りにならない。"""
    other = str(tmp_path / "other")
    data = plan(sprint("s1", [1], []), sprint("s2", [1], [], repo=other))
    got = pp.ParallelPlan.model_validate(data, context=pp._NO_LIMIT)
    assert [b.name for ln in got.lanes for b in ln.input.bundle_list()] == ["issue-1", "issue-1"]


def test_bundle_list_fills_single_bundles_in_issue_order():
    """受け入れ条件 3・I4: どのまとまりにも無い課題は 1 件のまとまり。空の配列はすべて 1 件、欄が無ければ None。"""
    inp = pp.SprintInput.model_validate(
        {"kind": "sprint", "issues": [3001, 3004, 3010], "bundles": [{"name": "rs", "issues": [3004, 3001]}]}
    )
    assert [(b.name, b.issues, b.lead()) for b in inp.bundle_list()] == [("rs", [3004, 3001], 3004), ("issue-3010", [3010], 3010)]
    assert [b.name for b in pp.SprintInput(kind="sprint", issues=[1, 2], bundles=[]).bundle_list()] == ["issue-1", "issue-2"]
    assert pp.SprintInput(kind="sprint", issues=[1]).bundle_list() is None


def test_bundles_on_a_loop_lane_are_a_form_error():
    """決定 5: ループのレーンにまとまりは書けない（形の誤り）。"""
    lane = {"name": "l1", "input": {"kind": "loop", "loop": "l1", "bundles": []}, "plans": 1, "touches": []}
    assert [it["rule"] for it in errors(plan(lane))] == ["form"]


def test_a_plan_without_bundles_reads_as_before(tmp_path):
    """受け入れ条件 10・I7: まとまりの欄の無い計画は今と同じに読め、書き戻しても欄が増えない。"""
    data = plan(sprint("s1", [1]), sprint("s2", [2]))
    got, items, _ = pp.check_data(data, estimates={"s1": {1: ["a.py"]}, "s2": {2: ["a.py"]}})
    assert got is not None and rules(items, "bundle-overlap") == []
    (it,) = rules(items, "I21")
    assert "bundles" not in it and all("bundle" not in p for p in it["proposals"])
    assert "bundles" not in got.lanes[0].input.model_dump(exclude_none=True)
    d = pp.plan_dir(tmp_path, "p1")
    d.mkdir(parents=True)
    (d / "plan.json").write_text(json.dumps(data))
    assert pp.load_valid_plan(tmp_path, "p1").lanes[1].input.bundle_list() is None


# ---- 見込みの重なり（I5a・I5b） ----

EST = {"s1": {3001: ["lib/a.py"], 3004: ["lib/a.py"], 3010: ["lib/a.py"], 3012: ["docs/b.md"]}}


def test_different_bundles_in_one_sprint_overlap_but_one_bundle_does_not():
    """受け入れ条件 4・5・I5a・I5b: 異なるまとまりの重なりは警告と案を出し、同じまとまりの課題どうしは出さない。"""
    data = plan(sprint("s1", [3001, 3004, 3010, 3012], [{"name": "review-scope", "issues": [3004, 3001]}]))
    got, items, metrics = pp.check_data(data, estimates=EST)
    found = rules(items, "bundle-overlap")
    assert got is not None and metrics["errors"] == 0 and metrics["warnings"] == len(found) == 2
    assert sorted(tuple(it["issues"]) for it in found) == [(3001, 3010), (3004, 3010)]
    it = next(it for it in found if it["issues"] == [3004, 3010])
    assert it["level"] == "warning" and it["at"] == "lanes" and it["lanes"] == ["s1"]
    assert it["bundles"] == ["review-scope", "issue-3010"] and it["paths"] == ["lib/a.py", "lib/a.py"]
    assert it["proposals"] == [
        {"kind": "move", "bundle": "issue-3010", "from": "s1"},
        {"kind": "after", "bundle": "issue-3010", "from": "s1"},
    ]


def test_single_bundles_overlap_each_other():
    """受け入れ条件 3: `bundles: []` のレーンでは 1 件のまとまりの間でも重なりが出る。"""
    items = pp.check_data(plan(sprint("s1", [3001, 3004], [])), estimates=EST)[1]
    assert [it["bundles"] for it in rules(items, "bundle-overlap")] == [["issue-3001", "issue-3004"]]


def test_i21_items_get_bundle_names_when_a_lane_has_bundles():
    """受け入れ条件 4: after の付かない 2 レーンの異なるまとまりの重なりは I21 が bundles の欄つきで出し、既存の欄は変えない。"""
    est = {"s1": {1: ["a.py"]}, "s2": {2: ["a.py"]}}
    items = pp.check_data(plan(sprint("s1", [1], [{"name": "x", "issues": [1]}]), sprint("s2", [2])), estimates=est)[1]
    (it,) = rules(items, "I21")
    assert it["lanes"] == ["s1", "s2"] and it["issues"] == [1, 2] and it["bundles"] == ["x", "issue-2"]
    assert it["proposals"] == [
        {"kind": "after", "lane": "s2", "after": "s1"},
        {"kind": "move", "issue": 2, "from": "s2", "to": "s1", "bundle": "issue-2"},
    ]


def test_plan_md_shows_the_bundles_table():
    got = pp.ParallelPlan.model_validate(plan(sprint("s1", [1, 2], [{"name": "a", "issues": [2, 1]}])), context=pp._NO_LIMIT)
    md = pp.render_md(got)
    assert "## 課題のまとまり" in md and "| s1 | a | #2 #1 |" in md
    assert "## 課題のまとまり" not in pp.render_md(pp.ParallelPlan.model_validate(plan(sprint("s1", [1])), context=pp._NO_LIMIT))


# ---- レーンのまとまりの読み出し ----


@pytest.fixture
def homes(tmp_path, monkeypatch):
    """探す置き場を tmp の main だけにする。"""
    main = tmp_path / "main"
    main.mkdir()
    monkeypatch.setattr(parallel_homes, "search_homes", lambda m, env=None: [main])
    return main


def write_plan(main: Path, data: dict) -> None:
    d = pp.plan_dir(main, data["name"])
    d.mkdir(parents=True)
    (d / "plan.json").write_text(json.dumps(data))


def test_bundles_of_reads_the_running_lane(homes):
    write_plan(homes, plan(sprint("la", [1, 2], [{"name": "a", "issues": [2, 1]}]), sprint("lb", [3])))
    assert [b.name for b in pb.bundles_of(homes, "la", status=RUNNING)] == ["a"]
    assert pb.bundles_of(homes, "lb", status=RUNNING) is None  # まとまりの欄が無い
    assert pb.bundles_of(homes, "lc", status=RUNNING) is None  # レーンでない
    assert pb.bundles_of(homes, "la", status=lambda _m, _n: "SUCCESS") is None  # 閉じた計画


def broken(name="p2", repo=None):
    lane = sprint("la", [1, 2], [{"name": "a", "issues": [1]}, {"name": "b", "issues": [1]}], **({"repo": repo} if repo else {}))
    return plan(lane, name=name)


def test_a_broken_running_plan_of_this_lane_stops(homes):
    """I1〜I3 を手で破った動いている計画のこのレーンは、None でなく誤りを投げる。"""
    write_plan(homes, broken())
    with pytest.raises(pb.BrokenPlanError) as exc:
        pb.bundles_of(homes, "la", status=RUNNING)
    assert exc.value.plan == "p2" and "2 つ以上のまとまり" in exc.value.error


def test_broken_plans_outside_this_lane_do_not_stop(homes, tmp_path):
    """別のレーン・閉じた計画・別のリポジトリのレーンの壊れた計画は止める理由にしない。"""
    write_plan(homes, broken())
    write_plan(homes, broken("p3", repo=str(tmp_path / "other")))
    write_plan(homes, plan(sprint("lz", [5], []), name="p4"))
    assert pb.bundles_of(homes, "lz", status=RUNNING)[0].name == "issue-5"
    assert pb.bundles_of(homes, "la", status=lambda _m, _n: "SUCCESS") is None
    (pp.plan_dir(homes, "p2") / "plan.json").unlink()
    assert pb.bundles_of(homes, "la", status=RUNNING) is None  # 残るのは別のリポジトリのレーンの壊れた計画だけ
