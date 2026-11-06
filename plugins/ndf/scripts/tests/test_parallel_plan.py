"""並列の計画の検査と作成（#2436 の受け入れ条件 1〜3、I1〜I5・I19）と、統括役の数え方（受け入れ条件 7、I13）。"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
import parallel_plan as pp  # noqa: E402
import parallel_release as prl  # noqa: E402
from relay_lib import hub as hb  # noqa: E402

BASE = {
    "version": 1,
    "name": "p1",
    "rules": ["取り込みの衝突は base-sync で解く"],
    "lanes": [
        {
            "name": "lroot",
            "input": {"kind": "loop", "loop": "lroot", "args": ["--milestones", "26"]},
            "plans": 2,
            "touches": ["plugins/ndf/"],
        },
        {"name": "lflow", "input": {"kind": "loop", "loop": "lflow", "args": ["--milestones", "23"]}, "plans": 2, "touches": ["docs/"]},
        {"name": "lfac", "input": {"kind": "sprint", "issues": [2400]}, "plans": 2, "touches": ["web/"], "after": ["lflow"]},
    ],
}


def rules_of(data):
    _, items, _ = pp.check_data(data)
    return {(it["level"], it["rule"]) for it in items}


def variant(**edit):
    d = copy.deepcopy(BASE)
    for k, v in edit.items():
        v(d) if callable(v) else d.__setitem__(k, v)
    return d


def test_valid_plan_passes():
    plan, items, metrics = pp.check_data(BASE)
    assert plan is not None and metrics["errors"] == 0 and items == []


@pytest.mark.parametrize(
    "data,rule",
    [
        (variant(name="a" * 25), "I1"),
        (variant(x=lambda d: d["lanes"][0].__setitem__("name", "p1-rel1")), "I1"),
        (variant(x=lambda d: d["lanes"][1].__setitem__("name", "lroot")), "I1"),
        (variant(x=lambda d: d["lanes"][2].__setitem__("handoff", "loop-lflow")), "I1"),
        (variant(x=lambda d: d["lanes"][0].__setitem__("plans", 3)), "I2"),
        (variant(x=lambda d: d["lanes"].append({**d["lanes"][0], "name": "l4", "plans": 0})), "form"),
        (variant(x=lambda d: d["lanes"][2].__setitem__("after", ["nope"])), "I3"),
        (variant(x=lambda d: d["lanes"][1].__setitem__("after", ["lfac"])), "I3"),
        (variant(x=lambda d: d["lanes"][1]["input"].__setitem__("args", [])), "I19"),
        (variant(x=lambda d: d["lanes"][1]["input"].__setitem__("args", ["--milestones", "26"])), "I19"),
    ],
)
def test_errors_carry_rule(data, rule):
    assert ("error", rule) in rules_of(data)


def test_name_at_limit_passes():
    assert pp.check_data(variant(name="a" * 24))[0] is not None


def test_total_plans_boundary(monkeypatch):
    assert pp.check_data(BASE)[0] is not None  # 合計 6 は上限ちょうど
    monkeypatch.setenv("NDF_PARALLEL_MAX_PLANS", "5")
    assert ("error", "I2") in rules_of(BASE)
    monkeypatch.setenv("NDF_PARALLEL_MAX_PLANS", "zero")
    _, _, metrics = pp.check_data(BASE)
    assert metrics["max_plans"] == 6 and metrics["notes"]


def test_lane_count_over_named_cap(monkeypatch):
    monkeypatch.setenv("NDF_RELAY_MAX_NAMED", "2")
    assert ("error", "I2") in rules_of(BASE)


def test_touch_overlap_is_warning():
    d = variant(x=lambda d: d["lanes"][1].__setitem__("touches", ["plugins/ndf/skills/"]))
    plan, items, metrics = pp.check_data(d)
    assert plan is not None and metrics["errors"] == 0 and ("warning", "I4") in {(i["level"], i["rule"]) for i in items}


def test_ready_lanes_waits_for_dependency():
    plan = pp.check_data(BASE)[0]
    assert [ln.name for ln in pp.ready_lanes(plan, set(), set())] == ["lroot", "lflow"]
    assert [ln.name for ln in pp.ready_lanes(plan, {"lroot", "lflow"}, {"lflow"})] == ["lfac"]


def _git_main(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".ndf").mkdir()  # 引継ぎ文書の置き場（development-workflow の手順 0 が作る）
    return tmp_path


def _cli(cwd: Path, *args: str, stdin: str | None = None):
    got = subprocess.run([sys.executable, str(SCRIPTS / "parallel.py"), *args], cwd=cwd, input=stdin, capture_output=True, text=True)
    return got.returncode, json.loads(got.stdout.strip().splitlines()[-1])


def test_init_writes_plan_and_handoffs_without_copying_rules(tmp_path):
    main = _git_main(tmp_path)
    code, res = _cli(main, "init", "p1", stdin=json.dumps(BASE))
    assert code == 0, res
    d = main / ".ndf" / "parallel" / "p1"
    assert (d / "plan.json").is_file() and (d / "plan.md").is_file()
    docs = [main / ".ndf" / "handoff" / f for f in ("loop-lroot.md", "loop-lflow.md", "issue-2400.md")]
    for p in docs:
        text = p.read_text()
        assert str(d / "plan.md") in text and BASE["rules"][0] not in text
    assert json.loads((d / "plan.json").read_text())["lanes"][2]["handoff"] == "issue-2400"
    code, res = _cli(main, "check", "p1")
    assert code == 0 and res["metrics"]["errors"] == 0


def test_init_refuses_existing_handoff_and_writes_nothing(tmp_path):
    main = _git_main(tmp_path)
    (main / ".ndf" / "handoff").mkdir(parents=True)
    (main / ".ndf" / "handoff" / "loop-lflow.md").write_text("前からある\n")
    code, res = _cli(main, "init", "p1", stdin=json.dumps(BASE))
    assert code == 1 and "loop-lflow.md" in res["summary"]
    assert not (main / ".ndf" / "parallel" / "p1").exists()
    assert not (main / ".ndf" / "handoff" / "loop-lroot.md").exists()
    assert (main / ".ndf" / "handoff" / "loop-lflow.md").read_text() == "前からある\n"


def test_init_with_error_writes_nothing(tmp_path):
    main = _git_main(tmp_path)
    code, res = _cli(main, "init", "p1", stdin=json.dumps(variant(x=lambda d: d["lanes"][1].__setitem__("after", ["lfac"]))))
    assert code == 1 and any(i["rule"] == "I3" for i in res["items"])
    assert not (main / ".ndf" / "parallel").exists() and not (main / ".ndf" / "handoff").exists()


def test_run_outside_wrapper_returns_manual_lines(tmp_path, monkeypatch):
    main = _git_main(tmp_path)
    assert _cli(main, "init", "p1", stdin=json.dumps(BASE))[0] == 0
    monkeypatch.delenv("NDF_RELAY_DIR", raising=False)
    code, res = _cli(main, "run", "p1")
    assert code == 3 and res["metrics"]["manual"] is True
    assert [i["lane"] for i in res["items"]] == ["lroot", "lflow", "lfac"]


# ---- 統括役の数え方（I13） ----


def _w(name, role=None):
    return hb.Wrapper(f"/x/{name}", name, True, False, None, None, role)


def test_organizer_is_not_counted_once():
    now = hb.live([_w("org", "organizer"), _w("a"), _w("b"), _w("c")], {}, {})
    assert now.count == 3
    assert hb.live([_w("o1", "organizer"), _w("o2", "organizer"), _w("a")], {}, {}).count == 2


def test_helper_passes_once_over_cap(monkeypatch):
    monkeypatch.setattr(hb.tmux, "binary", lambda: "/usr/bin/tmux")
    monkeypatch.setenv("NDF_RELAY_MAX_NAMED", "3")
    full = hb.live([_w("org", "organizer"), _w("a"), _w("b"), _w("c")], {}, {})
    assert hb.Hub.judge("p1-rel1", full, "helper") is None
    assert hb.Hub.judge("d", full) is not None
    with_helper = hb.live([_w("a"), _w("b"), _w("c"), _w("p1-rel1", "helper")], {}, {})
    assert with_helper.count == 3 and hb.Hub.judge("p1-rel2", with_helper, "helper") is not None


def test_registry_role_counts_before_role_file():
    now = hb.live([_w("a"), _w("p1-rel1")], {"p1-rel1": {"role": "helper"}}, {})
    assert now.count == 1 and now.helpers == 1


# ---- まとめたリリース（I9・I14・I15） ----


def test_range_maps_unrequested_lane_and_orphans():
    plan = pp.check_data(BASE)[0]
    reqs = [{"id": "lroot-r1", "lane": "lroot", "prs": [10]}]
    got = prl.map_lanes(plan, [10, 11, 12], {11: [2400], 12: []}, reqs)
    assert got["lanes"]["lroot"]["requested"] is True and got["lanes"]["lfac"] == {"prs": [11], "requested": False, "requests": []}
    assert got["orphans"] == [12]


def test_answerable_and_moved():
    pending = [{"id": "a", "prs": [1]}, {"id": "b", "prs": [5]}]
    assert prl.answerable(pending, [1, 2]) == ["a"]
    # 一部だけが範囲に入る依頼は答えない（残りを出さずにリリース済みと答えない）
    assert prl.answerable([{"id": "c", "prs": [1, 3]}, {"id": "d", "prs": []}], [1, 2]) == []
    # 先の PR が前のリリースで出た依頼は、今回の範囲と合わせて全部が入れば答える。前の範囲だけの依頼はこのリリースで答えない
    assert prl.answerable([{"id": "e", "prs": [1, 3]}, {"id": "f", "prs": [3]}], [1, 2], done=[3]) == ["e"]
    assert prl.moved("abc", "abc") is False and prl.moved("abc", "def") is True


@pytest.mark.parametrize("name,ok", [("loop-lroot-lane", True), ("sprint-p3", True), ("lane-lroot", False), ("loop-lroot-history", False)])
def test_handoff_name_follows_handoff_py(name, ok):
    """レーンの引継ぎ文書の名は handoff.py の名の形だけを受ける（handoff.py の副命令で扱えない名を作らない）。"""
    plan, _, metrics = pp.check_data(variant(x=lambda d: d["lanes"][0].__setitem__("handoff", name)))
    assert (plan is not None) is ok
    assert (metrics["errors"] == 0) is ok
