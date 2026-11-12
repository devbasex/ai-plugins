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
import pr_mode  # noqa: E402
from pace_state import sprint_state  # noqa: E402
from test_supervise_pace_verify import PRD, STG, make_repo  # noqa: E402

SUPERVISE = SCRIPTS / "supervise.py"
BUNDLES = [pb.Bundle(name="review-scope", issues=[12, 11, 99]), pb.Bundle(name="issue-13", issues=[13])]

pytestmark = pytest.mark.usefixtures("issue_bodies")


def build(tmp_path, monkeypatch, bundles, pace="normal", design=()):
    monkeypatch.setattr(pb, "bundles_of", lambda main, lane=None, status=None: bundles)
    repo = make_repo(tmp_path, [STG, PRD])
    out = tmp_path / "m"
    args = ["new", "sprint", "--name", "m2", "--worktree", str(repo), "--issue", "11", "12", "13", "--out", str(out)]
    args += ["--design", *design] if design else []
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
    assert sorted(stages(out)["設計"]) == ["design-12"]


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
