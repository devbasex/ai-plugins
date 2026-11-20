"""担当を起動できないときに、原因を利用者へ見せる（#3907）。

- 始める前に担当すべてを起動できるかを確かめ、できなければ提案と計画を流さず precondition で止める
- コードを書く担当を起動しなかった（refused・no_slot）ときは、その状態と理由を見送りの理由に書く
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ndf.adapters import agents, training
from ndf.domains.crossrefactor import drive, store

from ._drive_world import FakeAgents, FakeHub, Watched, World, base_acts, fake_ci, make_world, ports


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    return make_world(tmp_path, monkeypatch)


def test_runtime_that_cannot_launch_stops_before_proposing(world, monkeypatch) -> None:
    refused = "学習に使わない設定と確かめられていないので claude を起動しない（確かめられなかった: トークンが無い）"
    monkeypatch.setattr(training, "ensure", lambda runtime, env=None, now=None, read=None: refused if runtime == "claude" else None)
    fake = FakeAgents(base_acts())

    got = drive.drive(world.config(), ports(fake, FakeHub("clean"), ci=fake_ci(Watched(None))))

    assert got.kind == "precondition" and refused in got.detail
    assert fake.seen == []


@pytest.mark.parametrize(
    ("state", "said"),
    [(agents.AgentState.REFUSED, "学習の設定を確かめられていない"), (agents.AgentState.NO_SLOT, "スロットが空かなかった")],
)
def test_not_launched_coder_names_the_state_and_detail_in_the_skip_reason(world, state, said) -> None:
    fake = FakeAgents(base_acts())

    def launcher(spec: agents.Launch) -> agents.AgentStatus:
        if spec.agent == "claude" and "-add-tests-" in spec.paths.out.name:
            return agents.AgentStatus(spec.agent, state, None, "起動の前置きの理由", ())
        return fake(spec)

    got = drive.drive(world.config(), ports(launcher, FakeHub("clean"), ci=fake_ci(Watched(None))))  # type: ignore[arg-type]

    assert got.kind == "ok", got.detail
    item = store.load(store.path_of(world.root()))["items"][0]
    assert item["status"] == "deferred"
    assert said in item["reason"] and "起動の前置きの理由" in item["reason"] and "結果を出さずに" not in item["reason"]
    shown = next(i for i in got.items if i["kind"] == "item")
    assert shown["reason"] == item["reason"]
