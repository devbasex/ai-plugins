"""queue の結果のプランごとの待ちの秒（#2998 の受け入れ条件 5〜7・I3〜I5）。`supervise.py queue` を子プロセスで打つ。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_supervise_queue import Queue, run_step  # noqa: E402
from test_supervise_resume import note, once_slow  # noqa: E402
from supervise_lib import queue_waits  # noqa: E402

pytestmark = pytest.mark.usefixtures("issue_bodies")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("NDF_SV_STATE_DIR", str(tmp_path / "sv"))
    return os.environ.copy()


def by_stem(res: dict) -> dict:
    return {Path(i["plan"]).stem: i for i in res["items"]}


def test_reasons_include_the_plan_limit_for_2999():
    """受け入れ条件 7: 計画の上限の名前が理由に定義されている。"""
    assert queue_waits.REASONS == ("overlap", "plans", "resource", "deps", "plan_limit")


def test_overlapping_plan_waits_for_the_earlier_one(tmp_path, env):
    """受け入れ条件 5・6: 同じファイルを触る 2 本を --max 2 で流すと、後の overlap は前の流れた秒、plans は 0。"""
    q = Queue(tmp_path, env)
    a = q.plan("a", [run_step(q.span("a", 3))], ["same.py"])
    b = q.plan("b", [run_step(q.span("b", 0.2))], ["same.py"])
    p, res = q.run(a, b, "--max", "2")
    assert p.returncode == 0, p.stderr
    items = by_stem(res)
    for i in items.values():
        assert set(i["waits"]) == set(queue_waits.REASONS) and all(v >= 0 for v in i["waits"].values())
    wb = items["b"]["waits"]
    assert abs(wb["overlap"] - items["a"]["seconds"]) <= 5 and wb["overlap"] >= 3
    assert wb["plans"] == 0 and wb["deps"] == 0 and wb["resource"] == 0 and wb["plan_limit"] == 0
    assert items["a"]["waits"]["overlap"] < 3


def test_plans_slot_wait_is_counted(tmp_path, env):
    """`--max 1` の後のプランは plans を待つ。待たずに出た先のプランは 0（見回りの遅れを数えない）。"""
    q = Queue(tmp_path, env)
    a = q.plan("a", [run_step(q.span("a", 4))], ["a.py"])
    b = q.plan("b", [run_step(q.span("b", 0.2))], ["b.py"])
    p, res = q.run(a, b, "--max", "1")
    assert p.returncode == 0, p.stderr
    items = by_stem(res)
    assert items["b"]["waits"]["plans"] >= 4 and items["b"]["waits"]["overlap"] == 0
    assert items["a"]["waits"]["plans"] == 0


def test_resumed_queue_returns_the_recorded_waits_of_an_earlier_stage(tmp_path, env):
    """I5: 落ちた後の打ち直しでは、前のステージの待ちの秒を耐久ステップの記録から返す。"""
    t, q = tmp_path, Queue(tmp_path, env)
    a = q.plan("a", [run_step(note(t, "a1"), "a1")], ["a.py"])
    c = q.plan("c", [run_step(once_slow(t, "c1"), "c1")], ["c.py"])
    q.kill_when_sleeping(c, "c1", a, "--then", c)
    p, res = q.run(a, "--then", c)
    assert p.returncode == 0, p.stderr
    items = by_stem(res)
    assert set(items["a"]["waits"]) == set(queue_waits.REASONS) and set(items["c"]["waits"]) == set(queue_waits.REASONS)
    assert q.visits().count("a1") == 1


def test_waiting_for_a_needed_plan_is_counted_as_deps(tmp_path, env):
    q = Queue(tmp_path, env)
    a = q.plan("a", [run_step(q.span("a", 2))], ["a.py"])
    b = q.plan("b", [run_step(q.span("b", 0.2))], ["b.py"], after=["a"])
    p, res = q.run(a, b, "--max", "2")
    assert p.returncode == 0, p.stderr
    wb = by_stem(res)["b"]["waits"]
    assert wb["deps"] >= 2 and wb["overlap"] < 2


def test_waiting_for_a_resource_slot_is_summed_over_the_steps(tmp_path, env):
    q = Queue(tmp_path, env, {"resources": {"graphql": {"limit": 1, "commands": ["slot-cmd"]}}})
    a = q.plan("a", [run_step(": slot-cmd; " + q.span("sa", 4), "slot")])
    b = q.plan("b", [run_step(q.meet("fb", "sa"), "free"), run_step(": slot-cmd; " + q.span("sb", 0.2), "slot")])
    p, res = q.run(a, b, "--max", "2")
    assert p.returncode == 0, p.stderr
    items = by_stem(res)
    assert items["b"]["waits"]["resource"] >= 2  # b は a の枠のステップが始まった後に枠を求め、その終わりまで待つ
    assert items["a"]["waits"]["resource"] == 0
    assert items["a"]["waits"]["plans"] == 0 and items["b"]["waits"]["plans"] == 0


def test_plans_not_run_by_this_queue_have_no_waits(tmp_path, env):
    """I4: 前提のプランで流さなかったプランと、前の実行で完了の記録があるプランは waits を持たない。"""
    q = Queue(tmp_path, env)
    a = q.plan("a", [run_step(note(tmp_path, "a", "exit 1"))], ["a.py"])
    b = q.plan("b", [run_step(note(tmp_path, "b"))], ["b.py"], after=["a"])
    c = q.plan("c", [run_step(note(tmp_path, "c"))], ["c.py"])
    _, res = q.run(a, b, c, "--max", "2")
    items = by_stem(res)
    assert items["b"]["result"] == "流さなかった" and "waits" not in items["b"]
    assert "waits" in items["a"] and "waits" in items["c"]
    d = q.plan("d", [run_step(note(tmp_path, "d"))], ["d.py"])
    _, res = q.run(c, d)
    items = by_stem(res)
    assert "waits" not in items["c"] and "waits" in items["d"]
