"""drive の後半の工程（予算・安全網・成立の判定・フレーキーの直し・計画のコメントと完了報告・起動の費用）を、担当と
GitHub・CI の代わりで通す。旧い drive の意図した振る舞いをそろえる（既知の不具合は引き継がない）。

- #3747: 起動の費用が常に 0 で記録される（費用を報告する CLI の値を拾わず、報告しない CLI と区別しない）
"""

from __future__ import annotations

import itertools
import json
from dataclasses import replace
from pathlib import Path

import pytest

from ndf.adapters import agents
from ndf.domains.crossrefactor import budget_decide, drive, report_decide, store

from ._drive_world import (
    BRANCH,
    Comments,
    FakeAgents,
    FakeHub,
    Watched,
    World,
    base_acts,
    commit_at,
    commit_in,
    fake_ci,
    make_world,
    no_plan,
    ports,
    sh,
)

LONG = {"src/geo.py": "def area(w, h):\n    a = w\n    b = h\n    c = a * b\n    return c\n\n\ndef untouched():\n    return 0\n"}
# 行数の上限の検査（7 行を超えれば落ちる）
LINES = "import sys\nsys.exit(any(len(open(p).read().splitlines()) > 7 for p in sys.argv[1:]))\n"
# 呼ばれた回数を数え、最初の 2 回だけ落ちる検査（フレーキー）
FLAKY = (
    "import os, sys\np = os.environ['FLAKY_COUNT']\nn = int(open(p).read()) if os.path.exists(p) else 0\n"
    "open(p, 'w').write(str(n + 1))\nsys.exit(1 if n < 2 else 0)\n"
)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    return make_world(tmp_path, monkeypatch)


def _declare(world: World, name: str, script: str) -> None:
    """検査の設定と検査の本体を PR のブランチへ載せる（項目の起点にも在る）。"""
    decl = {
        "test": {
            "suites": [
                {
                    "name": name,
                    "kind": "lint",
                    "command": f"python3 {name}.py src",
                    "scope_command": f"python3 {name}.py {{paths}}",
                    "paths": ["src"],
                }
            ]
        }
    }
    commit_in(world.main, {".ndf/project.json": json.dumps(decl), f"{name}.py": script}, "検査の設定")
    sh(world.main, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    sh(world.main, "fetch", "-q", "origin")


def _acts(**over):
    acts = base_acts()
    acts.update({"judge": no_plan, "flaky-fix": no_plan})
    acts.update(over)
    return acts


def _state(world: World) -> dict:
    return store.load(store.path_of(world.root()))


def _planned(spec, submit):
    return {"items": [
        {"path": "src/geo.py", "symbol": "area", "title": "式に名前を付ける", "reason": "r", "tier": "high", "technique": "extract_variable", "tests": True},
        {"path": "src/geo.py", "symbol": "area", "title": "大きな書き換え", "reason": "r", "tier": "low", "technique": "extract_variable", "tests": True},
    ]}  # fmt: skip


def test_plan_items_are_chosen_by_the_budget_and_the_plan_comment_and_report_are_written(world, monkeypatch) -> None:
    # 設定の配分（テスト 5 分・実装 10 分）で 1 件 15 分。予算 20 分には 1 件だけ入る（#3643）
    (world.main / ".ndf").mkdir(exist_ok=True)
    (world.main / ".ndf" / "estimate.json").write_text(json.dumps({"allocation_minutes": {"test": 5, "structure": 10}}), encoding="utf-8")
    ci = fake_ci(Watched(None))
    comments = Comments()
    got = drive.drive(world.config(budget_minutes=20), ports(FakeAgents(_acts(plan=_planned)), FakeHub("clean"), comments, ci=ci))
    assert got.kind == "ok", got.detail
    state = _state(world)
    assert [(i["title"], i["status"], i["estimate"]) for i in state["items"]] == [
        ("式に名前を付ける", "adopted", {"test": 5.0, "implement": 10.0})
    ]
    assert state["plan"]["table_source"] == "defaults" and state["plan"]["table"]["structure"] == 10.0
    reasons = {s["title"]: s["reason"] for s in state["skipped"] if "title" in s and s["symbol"] == "area"}
    assert reasons["大きな書き換え"].startswith(budget_decide.OVER_BUDGET)
    assert state["budget"] == {"minutes": 20, "extended_to": None, "reason": ""}
    assert got.metrics["budget_minutes"] == 20 and got.metrics["plan_comment"] == "written"
    assert len(comments.bodies) == 1 and comments.bodies[0].startswith(report_decide.MARKER)
    report = Path(got.metrics["report"])
    assert report.parent == world.root() / "logs" and "大きな書き換え" not in report.read_text(encoding="utf-8").split("## 見送り")[0]


def test_an_item_planned_without_tests_does_not_launch_add_tests(world) -> None:
    # 見積りにテストの分を入れない項目（tests: false）は、実行でも add-tests の担当を起動しない
    def untested(spec, submit):
        return {"items": [dict(_planned(spec, submit)["items"][0], tests=False)]}

    fake = FakeAgents(_acts(plan=untested))
    got = drive.drive(world.config(), ports(fake, FakeHub("clean"), Comments(), ci=fake_ci(Watched(None))))
    assert got.kind == "ok", got.detail
    (item,) = _state(world)["items"]
    assert item["estimate"]["test"] == 0.0 and item["status"] == "adopted"
    assert "add-tests" not in {phase for _, phase, _, _ in fake.seen}


def test_a_broken_allocation_declaration_stops_the_plan(world) -> None:
    (world.main / ".ndf").mkdir(exist_ok=True)
    (world.main / ".ndf" / "estimate.json").write_text(json.dumps({"allocation_minutes": {"nope": 1}}), encoding="utf-8")
    got = drive.drive(world.config(), ports(FakeAgents(_acts(plan=_planned)), FakeHub("clean"), Comments(), ci=fake_ci(Watched(None))))
    assert got.kind == "invalid" and "配分の表を作れない" in got.detail


def test_safety_net_stops_without_undoing_items_and_a_larger_limit_resumes(world, monkeypatch) -> None:
    ci = fake_ci(Watched(None))
    comments = Comments()
    tick = itertools.count(step=600)
    slow = replace(ports(FakeAgents(_acts()), FakeHub("clean"), comments, ci=ci), clock=lambda: float(next(tick)))
    got = drive.drive(world.config(safety_net_minutes=5), slow)
    assert got.kind == "fix_limit" and "--safety-net-minutes" in got.detail
    state = _state(world)
    assert state["items"][0]["status"] == "open" and state["coding_seconds"] == 600
    assert "implement" not in state["phase_seconds"]
    assert "止まった" in Path(got.metrics["report"]).read_text(encoding="utf-8")
    again = drive.drive(world.config(safety_net_minutes=60), replace(slow, launcher=FakeAgents(_acts(propose=None, plan=None))))
    assert again.kind == "ok", again.detail
    assert _state(world)["items"][0]["status"] == "adopted"
    assert len(comments.bodies) == 2 and "結果: 完了" in comments.bodies[1]


def test_flaky_fix_queued_by_a_stopped_item_is_kept_when_the_item_is_redone_on_resume(world, monkeypatch) -> None:
    # 再開では途中の項目を起点へ戻してからフレーキーの直しをマージする。直しの後に戻すと直しのコミットが消える
    ci = fake_ci(Watched(None))
    tick = itertools.count(step=600)
    slow = replace(ports(FakeAgents(_acts()), FakeHub("clean"), ci=ci), clock=lambda: float(next(tick)))
    assert drive.drive(world.config(safety_net_minutes=5), slow).kind == "fix_limit"
    path = store.path_of(world.root())
    state = store.load(path)
    assert state["items"][0]["status"] == "open" and "start" in state["items"][0]
    state.update(flaky_requested=["flaky:src/geo.py"], flaky_request={"keys": ["flaky:src/geo.py"], "attempts": 0, "sendback": []})
    store.save(path, state)
    fix = commit_at({"tests/test_geo_flaky.py": "def test_x():\n    pass\n"}, "フレーキーを直す\n\nFlaky-Fix: flaky:src/geo.py")
    again = drive.drive(
        world.config(safety_net_minutes=600), replace(slow, launcher=FakeAgents(_acts(propose=None, plan=None, **{"flaky-fix": fix})))
    )
    assert again.kind == "ok", again.detail
    state = _state(world)
    assert state["items"][0]["status"] == "adopted"
    assert [(f["key"], f["result"]) for f in state["flaky_fixes"]] == [("flaky:src/geo.py", "kept")]
    assert "tests/test_geo_flaky.py" in sh(world.origin, "ls-tree", "-r", "--name-only", f"refs/heads/{BRANCH}")


def test_coding_launch_that_runs_out_the_safety_net_stops_and_the_item_is_redone_on_resume(world, monkeypatch) -> None:
    # 安全網の残りで縮めた監視の上限に達した担当は、結果が無くても項目を見送らずに止める（打ち直すとやり直す）
    ci = fake_ci(Watched(None))
    fake = FakeAgents(_acts(**{"add-tests": no_plan}))

    def timed_out(spec: agents.Launch) -> agents.AgentStatus:
        status = fake(spec)
        if "-add-tests-" not in spec.paths.out.name:
            return status
        return agents.AgentStatus(spec.agent, agents.AgentState.TIMEOUT, None, "監視の上限を超えた", ())

    got = drive.drive(world.config(safety_net_minutes=5), replace(ports(fake, FakeHub("clean"), ci=ci), launcher=timed_out))
    assert got.kind == "fix_limit" and "--safety-net-minutes" in got.detail
    state = _state(world)
    assert state["items"][0]["status"] == "open" and "add-tests" in state["phase_seconds"]
    # 結果を変える引数を変えて打ち直すと、前回の範囲で選んだ項目を続けずに止まる（状態は残す）
    narrowed = drive.drive(world.config(scope=("src/geo.py",), safety_net_minutes=60), ports(FakeAgents(_acts()), FakeHub("clean"), ci=ci))
    assert narrowed.kind == "invalid" and "状態ファイル" in narrowed.detail
    assert _state(world)["items"][0]["status"] == "open"
    again = drive.drive(world.config(safety_net_minutes=60), ports(FakeAgents(_acts(propose=None, plan=None)), FakeHub("clean"), ci=ci))
    assert again.kind == "ok", again.detail
    assert _state(world)["items"][0]["status"] == "adopted"


def test_stalled_fix_is_judged_by_another_runtime_and_an_infeasible_item_is_reverted(world, monkeypatch) -> None:
    _declare(world, "lines", LINES)
    ci = fake_ci(Watched(None))

    def idle_fix(spec, submit):
        return {"commit": sh(spec.cwd, "rev-parse", "HEAD")}

    def infeasible(spec, submit):
        return {"items": [{"item": "I-001", "feasible": False, "reason": "行数の上限を守ると振る舞いが変わる", "evidence": "e"}]}

    acts = _acts(implement=commit_at(LONG, "長くする"), fix=idle_fix, judge=infeasible)
    fake = FakeAgents(acts)
    got = drive.drive(world.config(participants=("codex",)), ports(fake, FakeHub("clean"), ci=ci))
    assert got.kind == "ok", got.detail
    item = _state(world)["items"][0]
    assert item["status"] == "reverted" and "成立しない" in item["reason"]
    assert [(j["by"], j["feasible"]) for j in item["feasibility"]] == [("codex", False)]
    assert [p for a, p, *_ in fake.seen if p == "judge"] == ["judge"]
    assert got.metrics["judged"] == 1
    assert "c = a * b" not in sh(world.origin, "show", f"refs/heads/{BRANCH}:src/geo.py")


def test_flaky_failure_is_not_the_items_and_its_fix_is_asked_once(world, monkeypatch, tmp_path) -> None:
    _declare(world, "flaky", FLAKY)
    monkeypatch.setenv("FLAKY_COUNT", str(tmp_path / "count"))
    ci = fake_ci(Watched(None))
    fix = commit_at({"tests/test_geo_flaky.py": "def test_x():\n    pass\n"}, "フレーキーを直す\n\nFlaky-Fix: flaky:src/geo.py")
    fake = FakeAgents(_acts(**{"flaky-fix": fix}))
    got = drive.drive(world.config(), ports(fake, FakeHub("clean"), ci=ci))
    assert got.kind == "ok", got.detail
    state = _state(world)
    assert state["items"][0]["status"] == "adopted"
    fixes = state["flaky_fixes"]
    assert [(f["key"], f["result"]) for f in fixes] == [("flaky:src/geo.py", "kept")] and fixes[0]["commit"]
    assert [p for _, p, *_ in fake.seen].count("flaky-fix") == 1
    assert got.metrics["flaky_fixed"] == 1


def test_flaky_fix_that_changes_non_test_files_is_sent_back_once_then_undone(world, monkeypatch, tmp_path) -> None:
    _declare(world, "flaky", FLAKY)
    monkeypatch.setenv("FLAKY_COUNT", str(tmp_path / "count"))
    ci = fake_ci(Watched(None))
    bad = [0]

    def outside(spec, submit):
        bad[0] += 1
        return commit_at({"docs/a.md": f"# {bad[0]}\n"}, "範囲の外\n\nFlaky-Fix: flaky:src/geo.py")(spec, submit)

    fake = FakeAgents(_acts(**{"flaky-fix": outside}))
    got = drive.drive(world.config(), ports(fake, FakeHub("clean"), ci=ci))
    assert got.kind == "ok", got.detail
    assert [(f["key"], f["result"]) for f in _state(world)["flaky_fixes"]] == [("flaky:src/geo.py", "undone")]
    assert bad[0] == 2
    assert sh(world.origin, "show", f"refs/heads/{BRANCH}:docs/a.md") == "# a"


@pytest.mark.parametrize("over", [{"required": ("pytest", "lint")}, {"tests": ("pytest", "lint")}])
def test_required_checks_and_ci_tests_are_args_that_change_the_result(world, over) -> None:
    # 必須の検査や CI のテストを変えたのに、同じ head の前回の成功を返さない
    assert world.config(**over).args() != world.config().args()


class _Billed:
    """claude の起動の出力を `--output-format json` の形にし、費用を報告させる担当の代わり。ほかのランタイムは報告しない。"""

    def __init__(self, fake: FakeAgents, usd: float) -> None:
        self.fake, self.usd = fake, usd

    def __call__(self, spec: agents.Launch) -> agents.AgentStatus:
        status = self.fake(spec)
        if spec.agent == "claude":
            body = spec.paths.out.read_text(encoding="utf-8")
            spec.paths.out.write_text(json.dumps({"result": body, "total_cost_usd": self.usd}), encoding="utf-8")
        return status


def test_launch_costs_are_the_reported_usd_and_unreported_launches_are_unknown_not_zero(world) -> None:
    fake = FakeAgents(_acts())
    got = drive.drive(world.config(), replace(ports(fake, FakeHub("clean"), ci=fake_ci(Watched(None))), launcher=_Billed(fake, 0.25)))
    assert got.kind == "ok", got.detail
    claude_runs = sum(1 for agent, *_ in fake.seen if agent == "claude")
    others = len(fake.seen) - claude_runs
    assert claude_runs > 0 and others > 0
    assert got.metrics["cost"] == pytest.approx(0.25 * claude_runs)
    assert got.metrics["cost_unknown"] == others
    phase_cost = _state(world)["phase_cost"]
    assert phase_cost["implement"]["usd"] > 0 and phase_cost["implement"]["unknown"] == 0
    assert phase_cost["propose"]["unknown"] > 0


def test_cost_is_unknown_when_no_launch_reports_it(world) -> None:
    got = drive.drive(world.config(), ports(FakeAgents(_acts()), FakeHub("clean"), ci=fake_ci(Watched(None))))
    assert got.kind == "ok", got.detail
    assert got.metrics["cost"] is None and got.metrics["cost_unknown"] > 0
