"""設計のプランで上限到達の cross-review を、未解決の件数で分けて流す（#2388 の受け入れ条件 1〜6・8）。

プランは全ペースの組み立て（`plan_sprint_design` / `plan_advise_design` / `plan_mvv_design`）で作り、ステップの結果は
ハンドラーをスタブに替えて決める。`review-settled` だけは、実行器が書いた `state.json` を本物の判定で読む。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
from supervise_lib import commands, engine, limits, sprint_waves  # noqa: E402
from supervise_lib.sprint_waves import RESOLVE_ATTEMPTS, RESOLVE_COST, RESOLVE_SECONDS  # noqa: E402

BUILD = {
    "normal": sprint_waves.plan_sprint_design,
    "advise": sprint_waves.plan_advise_design,
    "mvv": sprint_waves.plan_mvv_design,
}
DROP = ("記録", "branch", "起点", "リポジトリ", "スプリント状態")
BEFORE = ["glossary", "requirements-check", "design", "rebuild", "pr", "sync-body"]
AFTER = ["sync-review", "requirements-recheck", "glossary-check", "push-glossary"]
RESOLVE = ["resolve-sync", "resolve", "push-resolve"]
LLM = {"calls": 1, "turns": 1, "input": 0, "cache_read": 0, "cache_write": 0, "output": 0}
ADVISE = ["ready", "mvv", "mvv-note", "gate"]  # 助言の MVV 判定（判定を PR へ書く）の後に関門 1 の判断


def build(tmp: Path, pace: str) -> dict:
    a = SimpleNamespace(base="develop", mode="standard", name="m1", worktree=str(tmp), state=str(tmp / "sprint.json"))
    return limits.fill(BUILD[pace](a, 42, str(tmp)))


class _Fake:
    """run・work・drive・pr のスタブ。review は (終了コード, 未解決の件数) を順に返し、review-settled は本物の判定で決める。"""

    def __init__(self, reviews: list[tuple[int, int | None]], fails: dict[str, int]) -> None:
        self.reviews, self.fails = list(reviews), fails

    def execute(self, ctx, step: dict):
        sid = step["id"]
        cur = ctx.state.cur
        if sid == "review":
            code, unresolved = self.reviews.pop(0)
            cur.update(exit=code, text="review", seconds=60.0, counts={"unresolved": unresolved})
        elif sid == "review-settled":
            res, code = commands.cmd_review_settled(
                str(ctx.state.dir / "state.json"), "review", "resolve", RESOLVE_ATTEMPTS, RESOLVE_COST, RESOLVE_SECONDS
            )
            cur.update(exit=code, text=json.dumps(res, ensure_ascii=False), seconds=0.0)
        elif sid == "resolve":
            cur.update(exit=0, text="resolve", seconds=60.0, llm={**LLM, "cost": 0.2})
        else:
            cur.update(exit=self.fails.get(sid, 0), text=sid, seconds=0.0)
        return cur["exit"] == 0, ""


class _Judge:
    kind = "judge"

    def execute(self, ctx, step: dict) -> dict:
        ctx.state.cur.update(exit=0, text=json.dumps({"decision": "gate"}), seconds=0.0)
        return {"decision": "gate"}


def drive(tmp: Path, plan: dict, reviews: list[tuple[int, int | None]], fails: dict[str, int] | None = None) -> tuple[list[str], str]:
    plan = {k: v for k, v in plan.items() if k not in DROP}
    plan["作業場所"] = str(tmp)
    eng = engine.Engine(plan, tmp / "state")
    fake = _Fake(reviews, fails or {})
    for kind in ("run", "work", "drive", "pr"):
        eng.handlers[kind] = fake
    eng.handlers["judge"] = _Judge()
    text = eng.run(None)
    return [e["id"] for e in eng.state.log], text


def until_ready(ran: list[str]) -> list[str]:
    return ran[: ran.index("ready")] if "ready" in ran else ran


@pytest.mark.parametrize("final", ["oscillation", "max_rounds"])
def test_ac1_2_capped_review_with_no_unresolved_goes_to_mvv(tmp_path, final):
    plan = build(tmp_path, "mvv")
    ran, _ = drive(tmp_path, plan, [(6, 0)])
    assert ran[: len(BEFORE) + 2 + len(AFTER) + 1] == [*BEFORE, "review", "review-settled", *AFTER, "mvv"]
    assert "gate" not in ran and "ready" not in ran


@pytest.mark.parametrize("pace", ["normal", "advise", "mvv"])
def test_ac3_left_findings_are_resolved_before_the_normal_route(tmp_path, pace):
    ran, text = drive(tmp_path, build(tmp_path, pace), [(6, 2), (6, 0)])
    head = [*BEFORE, "review", "review-settled", *RESOLVE, "review", "review-settled", *AFTER]
    assert ran[: len(head)] == head
    tail = {"normal": ["ready", "gate"], "advise": ADVISE, "mvv": ["mvv"]}[pace]
    assert ran[len(head) : len(head) + len(tail)] == tail
    assert ("gate" in ran) == (pace != "mvv")


@pytest.mark.parametrize("pace", ["normal", "advise", "mvv"])
def test_ac3_unresolvable_findings_go_to_the_gate_without_mvv(tmp_path, pace):
    ran, text = drive(tmp_path, build(tmp_path, pace), [(6, 2), (6, 2)])  # 直しの後も減らない（U3）
    assert until_ready(ran) == [*BEFORE, "review", "review-settled", *RESOLVE, "review", "review-settled"]
    tail = ADVISE if pace == "advise" else ["ready", "gate"]
    assert ran[len(until_ready(ran)) :] == tail and "結果: 関門" in text
    assert "sync-review" not in ran


def test_ac3_a_failed_re_review_goes_to_the_gate(tmp_path):
    ran, _ = drive(tmp_path, build(tmp_path, "mvv"), [(6, 1), (1, None)])
    assert ran[len(BEFORE) :] == ["review", "review-settled", *RESOLVE, "review", "ready", "gate"]


def test_ac3_the_flow_stops_at_the_attempt_cap_within_the_step_limit(tmp_path):
    plan = build(tmp_path, "mvv")
    ran, text = drive(tmp_path, plan, [(6, 3), (6, 2), (6, 1)])  # 減り続けるが 2 回で止まる（U2）
    loop = ["review", "review-settled", *RESOLVE]
    assert ran[len(BEFORE) :] == [*loop, *loop, "review", "review-settled", "ready", "gate"]
    assert "結果: 関門" in text and len(ran) <= plan["上限"]


def test_ac3_the_step_limit_counts_the_resolve_loop_once(tmp_path):
    plan = build(tmp_path, "mvv")
    fixes, _ = limits.loops(plan)
    assert fixes.get("review→review-settled") == 5 and "review-settled→resolve-sync" not in fixes
    assert plan["上限"] == limits.longest_path(plan) + limits.FIX_ROUNDS * (sum(fixes.values()) + sum(limits.loops(plan)[1].values()))


@pytest.mark.parametrize("pace", ["mvv"])
def test_ac4_a_failed_drive_goes_to_the_gate(tmp_path, pace):
    ran, _ = drive(tmp_path, build(tmp_path, pace), [(1, None)])
    assert ran[len(BEFORE) :] == ["review", "ready", "gate"]


def test_ac5_normal_with_state_runs_the_advisory_mvv_then_stops_at_the_gate(tmp_path):
    ran, text = drive(tmp_path, build(tmp_path, "advise"), [(6, 0)])
    assert ran[len(BEFORE) :] == ["review", "review-settled", *AFTER, *ADVISE] and "結果: 関門" in text


@pytest.mark.parametrize("pace", ["normal", "mvv"])
def test_ac6_a_converged_review_skips_the_settled_check(tmp_path, pace):
    ran, _ = drive(tmp_path, build(tmp_path, pace), [(0, 0)])
    assert ran[len(BEFORE) : len(BEFORE) + 1 + len(AFTER)] == ["review", *AFTER]
    assert "review-settled" not in ran


# --- 受け入れ条件 8: 解決できないの判定（U1〜U5）---


def review(unresolved, code=6, seconds=60.0, **extra) -> dict:
    return {"id": "review", "exit": code, "seconds": seconds, "counts": {"unresolved": unresolved, **extra}}


def resolve(cost=0.2, seconds=60.0, code=0, unknown=False) -> dict:
    return {"id": "resolve", "exit": code, "seconds": seconds, "llm": {"cost": cost, "cost_unknown": unknown}}


def settle(tmp: Path, log: list[dict] | None) -> tuple[int, dict]:
    f = tmp / "state.json"
    if log is not None:
        f.write_text(json.dumps({"log": log, "llm": {}}))
    res, code = commands.cmd_review_settled(str(f), "review", "resolve", 2, 1.5, 3600)
    return code, res["metrics"]


@pytest.mark.parametrize("final", ["oscillation", "max_rounds"])
def test_zero_unresolved_is_ok_whatever_the_final_state(tmp_path, final):
    assert settle(tmp_path, [review(0, state=final)]) == (0, {"unresolved": 0, "attempts": 0, "cost": 0, "seconds": 0, "reason": None})


@pytest.mark.parametrize(
    "log",
    [None, [], [review(None)], [{"id": "review", "exit": 6, "counts": {}}], [review(0, code=0)], [review(True)]],
    ids=["no-file", "no-review", "null", "missing", "not-capped", "bool"],
)
def test_u1_an_unreadable_count_stops(tmp_path, log):
    code, m = settle(tmp_path, log)
    assert (code, m["reason"]) == (2, "U1")


def test_u2_the_attempt_cap_stops(tmp_path):
    assert settle(tmp_path, [review(3), resolve(), review(2), resolve(), review(1)])[1]["reason"] == "U2"
    assert settle(tmp_path, [review(3), resolve(), review(2)])[0] == 1
    assert settle(tmp_path, [review(3), resolve(code=1), review(2), resolve(), review(1)])[0] == 1  # 失敗した直しは数えない


def test_u3_a_count_that_does_not_drop_stops(tmp_path):
    code, m = settle(tmp_path, [review(2), resolve(), review(2)])
    assert (code, m["reason"]) == (2, "U3")
    assert settle(tmp_path, [review(2), resolve(), review(3)])[1]["reason"] == "U3"
    assert settle(tmp_path, [review(2), resolve(), review(1)]) == (
        1,
        {"unresolved": 1, "attempts": 1, "cost": 0.2, "seconds": 120.0, "reason": None},
    )


def test_u4_the_fix_cost_cap_stops(tmp_path):
    assert settle(tmp_path, [review(3), resolve(cost=1.6), review(2)])[1]["reason"] == "U4"
    assert settle(tmp_path, [review(3), resolve(cost=0.1, unknown=True), review(2)])[1]["reason"] == "U4"
    with_review_cost = [review(3), resolve(cost=1.4), {**review(2), "llm": {"cost": 1.0}}]
    assert settle(tmp_path, with_review_cost)[0] == 1  # review の費用は足さない


def test_u5_the_time_cap_stops(tmp_path):
    code, m = settle(tmp_path, [review(3, seconds=1000.0), resolve(seconds=1800.0), review(2, seconds=1801.0)])
    assert (code, m["reason"]) == (2, "U5")
    assert settle(tmp_path, [review(3, seconds=1000.0), resolve(seconds=1800.0), review(2, seconds=1800.0)])[0] == 1


def test_the_command_prints_the_result_and_exits_with_the_code(tmp_path):
    f = tmp_path / "state.json"
    f.write_text(json.dumps({"log": [review(2), resolve(), review(2)]}))
    args = ["--state", str(f), "--step", "review", "--fix-step", "resolve", "--max-attempts", "2", "--max-cost", "1.5"]
    p = subprocess.run(
        [sys.executable, str(SCRIPTS / "supervise.py"), "review-settled", *args, "--max-seconds", "3600"], capture_output=True, text=True
    )
    out = json.loads(p.stdout.strip().splitlines()[-1])
    assert p.returncode == 2 and out["tool"] == "review-settled" and out["status"] == "stopped" and out["metrics"]["reason"] == "U3"


# --- ステップの上限: on_exit を含むループを数え、同じステップの集まりは 1 回だけ数える ---


def test_a_loop_through_on_exit_is_counted_once():
    plan = {
        "steps": [
            {"id": "a", "type": "run", "cmd": "true", "on_exit": {"6": "b"}, "on_fail": "z", "next": "z"},
            {"id": "b", "type": "run", "cmd": "true", "on_exit": {"1": "c"}, "next": "z"},
            {"id": "c", "type": "work", "kind": "修正", "next": "a"},
            {"id": "z", "type": "run", "cmd": "true"},
        ]
    }
    fixes, returns = limits.loops(plan)
    assert fixes == {"a→b": 3} and returns == {}
