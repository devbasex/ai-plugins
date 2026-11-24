"""drive の後半の工程（見積りの配分・結果を変える引数・起動の費用）を、担当と
GitHub・CI の代わりで通す。旧い drive の意図した振る舞いをそろえる（既知の不具合は引き継がない）。

- #3747: 起動の費用が常に 0 で記録される（費用を報告する CLI の値を拾わず、報告しない CLI と区別しない）
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from ndf.adapters import agents
from ndf.domains.crossrefactor import drive, store

from ._drive_world import (
    Comments,
    FakeAgents,
    FakeHub,
    Watched,
    World,
    base_acts,
    fake_ci,
    make_world,
    no_plan,
    ports,
)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    return make_world(tmp_path, monkeypatch)


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


def _proposing(*proposals):
    return lambda spec, submit: {"proposals": list(proposals)}


def _planning(*items):
    return lambda spec, submit: {"items": list(items)}


_AREA = {"path": "src/geo.py", "symbol": "area", "title": "計画が選んだ題", "reason": "r", "tier": "high", "technique": "extract_variable"}
_UNTOUCHED = dict(_AREA, symbol="untouched")
_PROPOSED = {"path": "src/geo.py", "symbol": "area", "title": "提案の題", "reason": "r"}
_PROPOSED_UNTOUCHED = dict(_PROPOSED, symbol="untouched")


@pytest.mark.parametrize(
    ("plan", "titles", "skipped", "fallback"),
    [
        pytest.param(no_plan, ["提案の題"], ["提案の題"], True, id="結果が無ければ提案を項目にする"),
        pytest.param(_planning(_AREA), ["計画が選んだ題"], [], False, id="結果があれば計画だけを項目にする"),
        pytest.param(_planning(), [], [], False, id="空の計画は提案に戻さない"),
        pytest.param(_planning(_UNTOUCHED), [], ["計画が選んだ題"], False, id="範囲外だけの計画は提案に戻さない"),
        pytest.param(_planning({"path": "src/geo.py"}), [], [], False, id="形の違う項目だけの計画は提案に戻さない"),
    ],
)
def test_only_a_planner_without_a_result_falls_back_to_the_proposals(world, plan, titles, skipped, fallback) -> None:
    fake = FakeAgents(_acts(propose=_proposing(_PROPOSED, _PROPOSED_UNTOUCHED), plan=plan))
    got = drive.drive(world.config(), ports(fake, FakeHub("clean"), Comments(), ci=fake_ci(Watched(None))))
    assert got.kind == "ok", got.detail
    state = _state(world)
    assert [i["title"] for i in state["items"]] == titles
    assert [s["title"] for s in state["skipped"]] == skipped
    assert state["plan_fallback"] is fallback
    assert [agent for agent, phase, _, _ in fake.seen if phase == "plan"] == ["claude"]


@pytest.mark.parametrize("propose", [_proposing(), _proposing({"path": "src/geo.py"}), no_plan], ids=["空", "形が違う", "結果が無い"])
def test_without_proposals_the_planner_is_not_launched_and_it_is_not_a_fallback(world, propose) -> None:
    # 計画の担当が結果を返す状態でも起動しない。工程の起点は起動の有無に依らず記録する
    fake = FakeAgents(_acts(propose=propose, plan=_planning(_AREA)))
    got = drive.drive(world.config(), ports(fake, FakeHub("clean"), Comments(), ci=fake_ci(Watched(None))))
    assert got.kind == "ok", got.detail
    assert {phase for _, phase, _, _ in fake.seen} == {"propose"}
    state = _state(world)
    assert (state["items"], state["skipped"], state["plan_fallback"]) == ([], [], False)
    assert "plan" in state["phases"] and "plan" not in state["phase_seconds"]


def test_a_broken_allocation_declaration_stops_the_plan(world) -> None:
    (world.main / ".ndf").mkdir(exist_ok=True)
    (world.main / ".ndf" / "estimate.json").write_text(json.dumps({"allocation_minutes": {"nope": 1}}), encoding="utf-8")
    got = drive.drive(world.config(), ports(FakeAgents(_acts(plan=_planned)), FakeHub("clean"), Comments(), ci=fake_ci(Watched(None))))
    assert got.kind == "invalid" and "配分の表を作れない" in got.detail


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
