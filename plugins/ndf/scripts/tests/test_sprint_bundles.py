"""課題のまとまりでスプリントを組む（#3081 の I8a・I8b・I8c・I9）と、壊れた計画のレーンで止まること。

レーンのまとまりの読み出し（`parallel_bundles.bundles_of`）を差し替え、`supervise.py new sprint` をプロセスの中で打つ。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(SCRIPTS))
import inproc  # noqa: E402
import supervise_lib  # noqa: E402,F401  lib/ を sys.path へ足す
import parallel_bundles as pb  # noqa: E402
import design_results  # noqa: E402
import pr_mode  # noqa: E402
from supervise_lib import design_stage, procedures  # noqa: E402
from pace_state import sprint_state  # noqa: E402
from test_supervise_pace_verify import PRD, STG, make_repo  # noqa: E402

SUPERVISE = SCRIPTS / "supervise.py"
BUNDLES = [pb.Bundle(name="review-scope", issues=[12, 11, 99]), pb.Bundle(name="issue-13", issues=[13])]

pytestmark = pytest.mark.usefixtures("issue_bodies")


def build(tmp_path, monkeypatch, bundles, pace="normal", design=(), absorb=()):
    monkeypatch.setattr(pb, "bundles_of", lambda main, lane=None, status=None: bundles)
    repo = make_repo(tmp_path, [STG, PRD])
    out = tmp_path / "m"
    args = ["new", "sprint", "--name", "m2", "--worktree", str(repo), "--issue", "11", "12", "13", "--out", str(out)]
    args += ["--design", *design] if design else []
    args += ["--absorb", *absorb] if absorb else []
    if pace != "normal":
        args += ["--pace", pace, "--state", str(sprint_state(tmp_path, pace))]
    p = inproc.run(SUPERVISE, *args, cwd=repo)
    return p, out


def stages(out: Path) -> dict[str, dict]:
    """ステージ名 → {プランのキー: プラン}。"""
    index = json.loads((out / "sprint.json").read_text(encoding="utf-8"))["ステージ"]
    got = {}
    for e in index:
        plans = {}
        for path in e.get("plans") or []:
            key = Path(path).stem.split("-", 1)[1]
            plans[key] = json.loads(Path(path).read_text(encoding="utf-8"))
        got[e["name"]] = plans
    return got


def ids(plan: dict) -> list[str]:
    return [s["id"] for s in plan["steps"]]


def targets(plan: dict) -> set[str]:
    out = set()
    for s in plan["steps"]:
        out |= {s[k] for k in ("next", "on_fail", "skip_to", "gate_next") if k in s}
        out |= set((s.get("on_exit") or {}).values())
    return out


@pytest.mark.parametrize("pace", ["normal", "auto"])
def test_bundled_sprint_has_one_reviewed_impl_plan_per_bundle(tmp_path, monkeypatch, pace):
    """I8a・I8b・I8c: まとまり 1 つに実装のプラン 1 本（課題はこのスプリントの課題に絞る）。実装のプランは ready の前に
    cross-review を通し、検査のプランは cross-review を回さない。"""
    p, out = build(tmp_path, monkeypatch, BUNDLES, pace)
    assert p.returncode == 0, p.stdout + p.stderr
    got = stages(out)
    impl = got["実装"]
    assert sorted(impl) == ["impl-12", "impl-13"]
    assert impl["impl-12"]["課題"] == [12, 11] and impl["impl-12"]["branch"] == "feat/issue-12-m2"
    for plan in impl.values():
        review = next(s for s in plan["steps"] if s["id"] == "review")
        assert review["drive"] == "cross-review" and review["args"] == "{pr} --max-rounds 4" and review["next"] == "ready"
        assert ids(plan).index("review") < ids(plan).index("ready") and plan["steps"][ids(plan).index("ready") - 1]["id"] == "review"
        assert all(s.get("next") != "ready" for s in plan["steps"] if s["id"] != "review")
        if pace == "auto":
            assert plan["進め方"] == "auto"
    check = got["検査"]["check"]
    assert "review" not in ids(check) and "review" not in targets(check)


@pytest.mark.ci_only_route
def test_bundled_impl_plan_reviews_before_ready_on_the_ci_route(tmp_path, monkeypatch):
    """I8b: CI テストの宣言のあるリポジトリの並びでも、ready の前に cross-review を通す。"""
    from supervise_lib import templates, verify_steps

    for module in (templates, verify_steps):
        monkeypatch.setattr(module, "ci_only", lambda a: True)
    p, out = build(tmp_path, monkeypatch, BUNDLES)
    assert p.returncode == 0, p.stdout + p.stderr
    plan = stages(out)["実装"]["impl-13"]
    assert "ci-peek-2" in ids(plan) and ids(plan).index("review") < ids(plan).index("ready")
    assert all(s.get("next") != "ready" for s in plan["steps"] if s["id"] != "review")


def test_design_issues_go_to_the_lead(tmp_path, monkeypatch):
    """設計の課題はまとまりの代表の課題へ寄せる。"""
    p, out = build(tmp_path, monkeypatch, BUNDLES, design=("11",))
    assert p.returncode == 0, p.stdout + p.stderr
    design = stages(out)["設計"]
    assert sorted(design) == ["design-12"]
    # まとまりの課題も設計の入力に渡す（設計が要ると指した #11 を落とさない）
    assert design["design-12"]["課題"] == [12, 11]
    assert all("#11 の本文も読む" in s["prompt"] for s in design["design-12"]["steps"] if s["id"] in ("requirements", "design"))


def test_bundles_without_sprint_issues_are_dropped(tmp_path, monkeypatch):
    """このスプリントの課題を 1 つも持たないまとまりは外し、組み立てを止めない。"""
    bundles = [pb.Bundle(name="review-scope", issues=[12, 11]), pb.Bundle(name="other", issues=[99])]
    p, out = build(tmp_path, monkeypatch, bundles)
    assert p.returncode == 0, p.stdout + p.stderr
    assert sorted(stages(out)["実装"]) == ["impl-12", "impl-13"]


def test_absorbing_into_a_bundle_member_goes_to_the_lead(tmp_path, monkeypatch):
    """--absorb の取り込み先がまとまりの課題なら、代表の設計と実装のプランへ寄せる。取り込んだ 1 件のまとまりのプランは飛ぶ。"""
    p, out = build(tmp_path, monkeypatch, [pb.Bundle(name="review-scope", issues=[12, 11])], design=("11",), absorb=("13:11",))
    assert p.returncode == 0, p.stdout + p.stderr
    got = stages(out)
    assert got["設計"]["design-12"]["課題"] == [12, 13, 11]
    assert got["実装"]["impl-12"]["課題"] == [12, 11, 13] and "実行の条件" not in got["実装"]["impl-12"]
    assert got["実装"]["impl-13"]["実行の条件"] == procedures.absorbed_condition(13, 11)


def test_absorbing_a_bundle_member_drops_only_that_issue(tmp_path, monkeypatch):
    """取り込む課題が複数件のまとまりにいれば、まとまりのプランを飛ばさずにその課題だけを `課題` から外す。"""
    p, out = build(tmp_path, monkeypatch, [pb.Bundle(name="review-scope", issues=[12, 11])], absorb=("12:13",))
    assert p.returncode == 0, p.stdout + p.stderr
    impl = stages(out)["実装"]
    assert impl["impl-12"]["課題"] == [11] and "実行の条件" not in impl["impl-12"] and "取り込み先" not in impl["impl-12"]
    assert impl["impl-13"]["課題"] == [13, 12]


def test_design_results_keep_an_absorbed_lead_out_of_its_bundle():
    """--absorb で `課題` から外したまとまりの代表を、設計の結果の書き込みで戻さない。"""
    plans = {12: {"課題": [11], "steps": []}, 13: {"課題": [13, 12], "steps": []}}
    design_stage.rewrite(plans, rows("| #11 | 実装する | — | `a.py` |\n| #13 | 実装する | — | `b.py` |\n"))
    assert plans[12]["課題"] == [11] and plans[13]["課題"] == [13, 12]


def rows(text: str) -> design_results.DesignResults:
    res = design_results.DesignResults(design_prs=[40])
    head = "## 設計の結果\n\n| 課題 | 扱い | 取り込み先 | 触るファイル |\n| --- | --- | --- | --- |\n"
    res.add(design_results.parse_results(head + text))
    return res


def bundle_plans() -> dict[int, dict]:
    return {12: {"課題": [12, 11], "steps": []}, 13: {"課題": [13], "steps": []}}


def test_design_results_resolve_hosts_inside_a_bundle():
    """まとまりの課題への取り込みは、その課題を実装するまとまりのプランへ寄せる（止まらない）。"""
    plans = bundle_plans()
    design_stage.rewrite(plans, rows("| #12 | 取り込む | #11 | — |\n| #11 | 実装する | — | `a.py` |\n| #13 | 実装する | — | `b.py` |\n"))
    assert "実行の条件" not in plans[12] and plans[12]["課題"] == [11, 12] and plans[12]["触るファイル"] == ["a.py"]


def test_closed_lead_keeps_the_bundle_running():
    """代表を閉じても、まとまりに実装する課題が残ればプランは流れる。"""
    plans = bundle_plans()
    design_stage.rewrite(plans, rows("| #12 | 閉じる | — | — |\n| #11 | 実装する | — | `a.py` |\n"))
    assert "実行の条件" not in plans[12] and plans[12]["課題"] == [11]


def test_bundle_touched_files_come_from_every_issue():
    """まとまりのプランの触るファイルは、実装するすべての課題の設計の結果から集める。"""
    plans = bundle_plans()
    design_stage.rewrite(plans, rows("| #12 | 実装する | — | `x.py` |\n| #11 | 実装する | — | `y.py` |\n"))
    assert plans[12]["触るファイル"] == ["x.py", "y.py"] and plans[12]["課題"] == [12, 11]


def test_bundle_with_every_issue_closed_skips():
    """まとまりのすべての課題を閉じれば、プランは代表の扱いで飛ぶ。"""
    plans = bundle_plans()
    design_stage.rewrite(plans, rows("| #12 | 閉じる | — | — |\n| #11 | 閉じる | — | — |\n"))
    assert plans[12]["実行の条件"] == procedures.closed_condition(12)


def test_sprint_without_bundles_is_unchanged(tmp_path, monkeypatch):
    """受け入れ条件 11・I9: まとまりの記録が無ければ課題ごとの実装のプランで、検査のプランが cross-review を 1 回回す。"""
    p, out = build(tmp_path, monkeypatch, None)
    assert p.returncode == 0, p.stdout + p.stderr
    got = stages(out)
    assert sorted(got["実装"]) == ["impl-11", "impl-12", "impl-13"]
    assert all("review" not in ids(plan) for plan in got["実装"].values())
    assert "review" in ids(got["検査"]["check"])


def test_a_broken_plan_stops_without_writing(tmp_path, monkeypatch):
    """壊れた計画のレーンはプランを書かずに stopped で止まる。"""

    def broken(main, lane=None, status=None):
        raise pb.BrokenPlanError("p1", "2 つ以上のまとまりに入る課題がある: #1")

    monkeypatch.setattr(pb, "bundles_of", broken)
    repo = make_repo(tmp_path, [STG, PRD])
    out = tmp_path / "m"
    p = inproc.run(SUPERVISE, "new", "sprint", "--name", "m2", "--worktree", str(repo), "--issue", "11", "--out", str(out), cwd=repo)
    got = json.loads(p.stdout)
    assert got["status"] == "stopped" and "p1" in got["summary"] and not (out / "sprint.json").exists()


def test_bundled_sprint_branch_pr_needs_review(monkeypatch, tmp_path):
    """I8b: まとまりの記録のあるスプリントブランチ宛ては `metrics.review` が真。宛て先の区分は sprint のまま。"""
    assert pr_mode.needs_review("sprint/m2", bundled=True) and not pr_mode.needs_review("sprint/m2")
    steps = inproc.load(SCRIPTS / "pr-steps.py")
    monkeypatch.setattr(pb, "bundles_of", lambda main, lane=None, status=None: BUNDLES)
    assert steps.bundled_sprint(tmp_path, "sprint/m2") and not steps.bundled_sprint(tmp_path, "develop")
    monkeypatch.setattr(pb, "bundles_of", lambda main, lane=None, status=None: None)
    assert not steps.bundled_sprint(tmp_path, "sprint/m2")
    assert pr_mode.pr_target("sprint/m2") == "sprint"
