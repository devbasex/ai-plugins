"""並列の計画の検査の課題の見込みの重なり（I21。#2997）: 同じ時間帯のレーンの組・直し方の案・after で順序の付いた組。"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
import parallel_plan as pp  # noqa: E402
import touch_estimate as te_mod  # noqa: E402


def plan(*lanes):
    return {"version": 1, "name": "p1", "rules": ["r"], "lanes": list(lanes)}


def sprint(name, issues, after=()):
    return {"name": name, "input": {"kind": "sprint", "issues": issues}, "plans": 1, "touches": [f"{name}/"], "after": list(after)}


def loop(name, milestone, after=()):
    args = ["--milestones", milestone]
    return {"name": name, "input": {"kind": "loop", "loop": name, "args": args}, "plans": 1, "touches": [f"{name}/"], "after": list(after)}


def i21(items):
    return [i for i in items if i["rule"] == "I21"]


EST = {
    "lcost": {2913: ["plugins/ndf/scripts/lib/sprint_loop_select.py"]},
    "lflow": {2997: ["plugins/ndf/scripts/lib/sprint_loop_select.py"], 2998: ["docs/x.md"]},
}


def test_lanes_running_together_get_the_overlapping_pair_and_proposals():
    """受け入れ条件 6・I8: after の無い 2 レーンの課題の見込みが重なれば、組と after・move の案を警告に載せる。"""
    got, items, metrics = pp.check_data(plan(sprint("lcost", [2913]), sprint("lflow", [2997, 2998])), estimates=EST)
    assert got is not None and metrics["errors"] == 0
    (it,) = i21(items)
    assert it["level"] == "warning" and it["lanes"] == ["lcost", "lflow"] and it["issues"] == [2913, 2997]
    assert it["paths"] == ["plugins/ndf/scripts/lib/sprint_loop_select.py", "plugins/ndf/scripts/lib/sprint_loop_select.py"]
    assert it["proposals"] == [
        {"kind": "after", "lane": "lflow", "after": "lcost"},
        {"kind": "move", "issue": 2997, "from": "lflow", "to": "lcost"},
    ]


def test_lanes_ordered_by_after_even_transitively_get_no_item():
    """受け入れ条件 7・I9: after で（推移的にも）順序の付いたレーンの間では出さない。"""
    est = {**EST, "lmid": {}}
    _, items, _ = pp.check_data(
        plan(sprint("lcost", [2913]), sprint("lmid", [1], ["lcost"]), sprint("lflow", [2997], ["lmid"])), estimates=est
    )
    assert i21(items) == []


def test_loop_lanes_get_no_move_proposal_and_unknown_estimates_are_skipped():
    est = {"lcost": {2913: ["a.py"]}, "lflow": {2997: ["a.py"], 2999: []}}
    _, items, _ = pp.check_data(plan(loop("lcost", "1"), sprint("lflow", [2997, 2999])), estimates=est)
    (it,) = i21(items)
    assert it["proposals"] == [{"kind": "after", "lane": "lflow", "after": "lcost"}]


def test_the_shared_list_counts_as_an_overlap():
    """I10: 共有の一覧に当たる見込みは実装のキューと同じく重なる。"""
    est = {"la": {1: ["lib/a.py"]}, "lb": {2: ["lib/b.py"]}}
    shared = [{"path": "lib/README.md", "touched_by": ["lib/"]}]
    assert i21(pp.check_data(plan(sprint("la", [1]), sprint("lb", [2])), estimates=est)[1]) == []
    assert len(i21(pp.check_data(plan(sprint("la", [1]), sprint("lb", [2])), estimates=est, shared=shared)[1])) == 1
    # レーンごとの一覧（lane_estimates の答え）は、そのレーンのリポジトリの一覧を使う
    by_lane = {"la": shared, "lb": shared}
    assert len(i21(pp.check_data(plan(sprint("la", [1]), sprint("lb", [2])), estimates=est, shared=by_lane)[1])) == 1
    assert i21(pp.check_data(plan(sprint("la", [1]), sprint("lb", [2])), estimates=est, shared={"la": [], "lb": []})[1]) == []


def test_an_estimate_error_is_one_warning_and_the_check_goes_on():
    got, items, metrics = pp.check_data(plan(sprint("la", [1])), estimate_error="マイルストーンを読めない")
    assert got is not None and metrics["errors"] == 0
    assert [i["message"] for i in i21(items)] == ["課題の見込みを集められない: マイルストーンを読めない"]


class FakeEnv:
    def __init__(self, root, descs, bodies, fail=False):
        self.root, self.descs, self.bodies, self.fail = Path(root), descs, bodies, fail

    def main_dir(self):
        return self.root

    def milestones(self, repo):
        if self.fail:
            raise OSError("マイルストーンを読めない")
        return self.descs

    def issue_body(self, n):
        if n not in self.bodies and self.fail:
            raise OSError(f"課題 #{n} を読めない")
        return self.bodies.get(n, "")

    def run(self, cmd, cwd=None, stdin=None):
        return 0, "a/x.py\nb/y.py\n", ""


def test_lane_estimates_reads_sprint_issues_and_loop_milestone_groups(tmp_path):
    desc = "### 並列の組（見込み）\n\n| 組 | 課題 | 触る場所の見込み | 依存 |\n| --- | --- | --- | --- |\n| 1 | #7 | `a/` | なし |\n"
    p, _, _ = pp.check_data(plan(loop("lloop", "M1"), sprint("lsp", [8])))
    env = FakeEnv(tmp_path, {"M1": desc}, {8: "## 目的\n\n`b/y.py`\n"})
    est, shared, error = te_mod.lane_estimates(tmp_path, p.lanes, source_of=lambda root: env)
    assert est == {"lloop": {7: ["a/"]}, "lsp": {8: ["b/y.py"]}} and shared == {"lloop": [], "lsp": []} and error is None
    est, _, error = te_mod.lane_estimates(tmp_path, p.lanes, source_of=lambda root: FakeEnv(tmp_path, {}, {8: "`b/y.py`"}, fail=True))
    assert est == {"lloop": {}, "lsp": {8: ["b/y.py"]}} and "マイルストーンを読めない" in error


def test_lane_estimates_reports_an_unreadable_issue_body(tmp_path):
    p, _, _ = pp.check_data(plan(sprint("lsp", [8, 9])))
    env = FakeEnv(tmp_path, {}, {8: "`b/y.py`"}, fail=True)
    env.milestones = lambda repo: {}
    est, _, error = te_mod.lane_estimates(tmp_path, p.lanes, source_of=lambda root: env)
    assert est == {"lsp": {8: ["b/y.py"], 9: []}} and error == "課題 #9 の本文を読めない"


def test_bundle_overlap_pairs_match_i21_pairs_for_the_same_estimates():
    """#3081 の受け入れ条件 6・I6: 同じ見込みと共有の一覧に対し、同じレーンの異なるまとまりの組（bundle-overlap）と、
    同じ 2 課題を別のレーンに置いた組（I21）が一致する。判定は同じ `admission.overlap_of` を通る。"""
    est = {
        1: ["plugins/ndf/scripts/lib/a.py"],
        2: ["plugins/ndf/scripts/lib/a.py"],
        3: ["lib/README.md"],
        4: ["lib/x.py"],
        5: ["docs/x.md"],
    }
    shared = [{"path": "lib/README.md", "touched_by": ["lib/"]}]
    hits = []
    for a, b in [(1, 2), (1, 5), (3, 4), (4, 5)]:
        lane = sprint("s1", [a, b]) | {"input": {"kind": "sprint", "issues": [a, b], "bundles": []}}
        one = pp.check_data(plan(lane), estimates={"s1": {a: est[a], b: est[b]}}, shared=shared)[1]
        two = pp.check_data(plan(sprint("s1", [a]), sprint("s2", [b])), estimates={"s1": {a: est[a]}, "s2": {b: est[b]}}, shared=shared)[1]
        got = [(it["issues"], it["paths"]) for it in one if it["rule"] == "bundle-overlap"]
        assert got == [(it["issues"], it["paths"]) for it in i21(two)], (a, b)
        hits.append(bool(got))
    assert hits == [True, False, True, False]
