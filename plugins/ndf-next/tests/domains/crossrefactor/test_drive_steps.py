"""drive の後半の工程（予算・安全網・成立の判定・フレーキーの直し・計画のコメントと完了報告）を、担当と GitHub・CI の
代わりで通す。旧い drive の意図した振る舞いをそろえる（既知の不具合は引き継がない）。
"""

from __future__ import annotations

import itertools
import json
from dataclasses import replace
from pathlib import Path

import pytest

from ndf.domains.crossrefactor import budget_decide, drive, report_decide, store

from ._drive_world import (
    BRANCH,
    Comments,
    FakeAgents,
    FakeHub,
    Watched,
    World,
    commit_at,
    commit_in,
    fake_ci,
    make_world,
    no_plan,
    ports,
    propose_area,
    sh,
)

TESTS = {"tests/test_geo.py": "from src.geo import area\n\n\ndef test_area():\n    assert area(2, 3) == 6\n"}
NEW = {"src/geo.py": "def area(w, h):\n    product = w * h\n    return product\n\n\ndef untouched():\n    return 0\n"}
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
    """検査の宣言と検査の本体を PR のブランチへ載せる（項目の起点にも在る）。"""
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
    commit_in(world.main, {".ndf/project.json": json.dumps(decl), f"{name}.py": script}, "検査の宣言")
    sh(world.main, "push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}")
    sh(world.main, "fetch", "-q", "origin")


def _acts(**over):
    acts = {
        "propose": propose_area,
        "plan": no_plan,
        "add-tests": commit_at(TESTS, "テストを足す", {"tests": ["tests/test_geo.py"]}),
        "implement": commit_at(NEW, "式に名前を付ける"),
        "fix": no_plan,
        "final-fix": no_plan,
        "judge": no_plan,
        "flaky-fix": no_plan,
    }
    acts.update(over)
    return acts


def _state(world: World) -> dict:
    return store.load(store.path_of(world.root()))


def _planned(spec, submit):
    return {"items": [
        {"path": "src/geo.py", "symbol": "area", "title": "式に名前を付ける", "reason": "r", "tier": "high", "estimate": {"test": 5, "implement": 10}},
        {"path": "src/geo.py", "symbol": "area", "title": "大きな書き換え", "reason": "r", "tier": "low", "estimate": {"test": 50, "implement": 100}},
        {"path": "src/geo.py", "symbol": "area", "title": "見積りの無い項目", "reason": "r"},
    ]}  # fmt: skip


def test_plan_items_are_chosen_by_the_budget_and_the_plan_comment_and_report_are_written(world, monkeypatch) -> None:
    fake_ci(monkeypatch, Watched(None))
    comments = Comments()
    got = drive.drive(world.config(budget_minutes=30), ports(FakeAgents(_acts(plan=_planned)), FakeHub("clean"), comments))
    assert got.kind == "ok", got.detail
    state = _state(world)
    assert [(i["title"], i["status"]) for i in state["items"]] == [("式に名前を付ける", "adopted")]
    reasons = {s["title"]: s["reason"] for s in state["skipped"] if "title" in s and s["symbol"] == "area"}
    assert reasons["大きな書き換え"].startswith(budget_decide.OVER_BUDGET)
    assert reasons["見積りの無い項目"] == budget_decide.NO_ESTIMATE
    assert state["budget"] == {"minutes": 30, "extended_to": None, "reason": ""}
    assert got.metrics["budget_minutes"] == 30 and got.metrics["plan_comment"] == "written"
    assert len(comments.bodies) == 1 and comments.bodies[0].startswith(report_decide.MARKER)
    report = Path(got.metrics["report"])
    assert report.parent == world.root() / "logs" and "大きな書き換え" not in report.read_text(encoding="utf-8").split("## 見送り")[0]


def test_safety_net_stops_without_undoing_items_and_a_larger_limit_resumes(world, monkeypatch) -> None:
    fake_ci(monkeypatch, Watched(None))
    comments = Comments()
    tick = itertools.count(step=600)
    slow = replace(ports(FakeAgents(_acts()), FakeHub("clean"), comments), clock=lambda: float(next(tick)))
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


def test_stalled_fix_is_judged_by_another_runtime_and_an_infeasible_item_is_reverted(world, monkeypatch) -> None:
    _declare(world, "lines", LINES)
    fake_ci(monkeypatch, Watched(None))

    def idle_fix(spec, submit):
        return {"commit": sh(spec.cwd, "rev-parse", "HEAD")}

    def infeasible(spec, submit):
        return {"items": [{"item": "I-001", "feasible": False, "reason": "行数の上限を守ると振る舞いが変わる", "evidence": "e"}]}

    acts = _acts(implement=commit_at(LONG, "長くする"), fix=idle_fix, judge=infeasible)
    fake = FakeAgents(acts)
    got = drive.drive(world.config(participants=("codex",)), ports(fake, FakeHub("clean")))
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
    fake_ci(monkeypatch, Watched(None))
    fix = commit_at({"tests/test_geo_flaky.py": "def test_x():\n    pass\n"}, "フレーキーを直す\n\nFlaky-Fix: flaky:src/geo.py")
    fake = FakeAgents(_acts(**{"flaky-fix": fix}))
    got = drive.drive(world.config(), ports(fake, FakeHub("clean")))
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
    fake_ci(monkeypatch, Watched(None))
    bad = [0]

    def outside(spec, submit):
        bad[0] += 1
        return commit_at({"docs/a.md": f"# {bad[0]}\n"}, "範囲の外\n\nFlaky-Fix: flaky:src/geo.py")(spec, submit)

    fake = FakeAgents(_acts(**{"flaky-fix": outside}))
    got = drive.drive(world.config(), ports(fake, FakeHub("clean")))
    assert got.kind == "ok", got.detail
    assert [(f["key"], f["result"]) for f in _state(world)["flaky_fixes"]] == [("flaky:src/geo.py", "undone")]
    assert bad[0] == 2
    assert sh(world.origin, "show", f"refs/heads/{BRANCH}:docs/a.md") == "# a"
