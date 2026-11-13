"""cross-review が上限到達（終了コード 6）で終わったときの、解決できないの判定（#2388）。

判定は `supervise.py review-settled` に実行の記録（state.json）を渡して確かめる。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from supervise_lib import commands, limits  # noqa: E402
from test_supervise_contract import cli  # noqa: E402

pytestmark = pytest.mark.usefixtures("issue_bodies")

LIMITS = dict(max_attempts=2, max_cost=1.5, max_seconds=3600)


# --- 経路 ---------------------------------------------------------------------


def codes(**seqs):
    """ステップごとの終了コードの並び（使い切ったら 0）。judge は gate を返す。"""
    left = {k.replace("_", "-"): list(v) for k, v in seqs.items()}

    def decide(sid, step):
        if step["type"] == "judge":
            return "gate"
        return left[sid].pop(0) if left.get(sid) else 0

    return decide


def after(ran: list[str], start: str = "review") -> list[str]:
    return ran[ran.index(start) :]


SETTLED_TO_PUSH = ["review-settled", "sync-review", "requirements-recheck", "survey-check", "glossary-check", "push-glossary"]
RESOLVE = ["review-settled", "resolve-sync", "resolve", "push-resolve", "review"]


# --- 上限の数え方 ---------------------------------------------------------------


def test_a_loop_through_on_exit_is_counted_once():
    steps = [
        {"id": "c", "type": "run", "cmd": "true", "on_exit": {"6": "s"}, "next": "end"},
        {"id": "s", "type": "run", "cmd": "true", "on_exit": {"1": "f"}, "next": "end"},
        {"id": "f", "type": "work", "kind": "修正", "prompt": "直す", "next": "c"},
    ]
    plan = {"steps": steps}
    assert limits.loops(plan) == ({"c→s": 3}, {})
    assert limits.step_limit(plan) == 3 + limits.FIX_ROUNDS * 3


# --- 解決できないの判定 -----------------------------------------------------------


def rec(sid: str, code: int, unresolved=None, seconds: float = 0.0, cost: float | None = None, unknown: bool = False) -> dict:
    e: dict = {"id": sid, "type": "drive" if sid == "review" else "work", "exit": code, "seconds": seconds}
    if sid == "review":
        e["counts"] = {"unresolved": unresolved}
    if cost is not None or unknown:
        e["llm"] = {"cost": cost or 0.0, "cost_unknown": unknown}
    return e


def settle(tmp_path: Path, *log, state: dict | None = None) -> tuple[int, dict]:
    path = tmp_path / "state.json"
    path.write_text(json.dumps(state if state is not None else {"log": list(log)}))
    res, code = commands.cmd_review_settled(str(path), "review", "resolve", **LIMITS)
    return code, res["metrics"]


@pytest.mark.parametrize("final", ["max_rounds", "oscillation"])
def test_nothing_left_is_0_whatever_the_final_state(tmp_path, final):
    e = rec("review", 6, 0)
    e["counts"]["final"] = final
    assert settle(tmp_path, e) == (0, {"unresolved": 0, "attempts": 0, "cost": 0.0, "seconds": 0.0, "reason": None})


@pytest.mark.parametrize(
    "log",
    [
        [],
        [rec("sync-body", 0)],
        [rec("review", 6)],
        [{"id": "review", "exit": 6, "counts": {}}],
        [rec("review", 1, 0)],
        [rec("review", 6, 0), rec("review", 1, 0)],
    ],
)
def test_an_unreadable_record_is_u1(tmp_path, log):
    code, m = settle(tmp_path, *log)
    assert (code, m["reason"]) == (2, "U1")


def test_a_missing_or_broken_file_is_u1(tmp_path):
    res, code = commands.cmd_review_settled(str(tmp_path / "none.json"), "review", "resolve", **LIMITS)
    assert (code, res["metrics"]["reason"]) == (2, "U1")
    assert settle(tmp_path, state={"log": "x"})[1]["reason"] == "U1"


def test_left_findings_continue_until_the_attempts_run_out(tmp_path):
    assert settle(tmp_path, rec("review", 6, 3))[0] == 1
    one = [rec("review", 6, 3), rec("resolve", 1), rec("resolve", 0), rec("review", 6, 2)]
    assert settle(tmp_path, *one) == (1, {"unresolved": 2, "attempts": 1, "cost": 0.0, "seconds": 0.0, "reason": None})
    two = [*one, rec("resolve", 0), rec("review", 6, 1)]
    code, m = settle(tmp_path, *two)
    assert (code, m["reason"], m["attempts"]) == (2, "U2", 2)


def test_a_count_that_does_not_drop_is_u3(tmp_path):
    code, m = settle(tmp_path, rec("review", 6, 2), rec("resolve", 0), rec("review", 6, 2))
    assert (code, m["reason"]) == (2, "U3")
    assert settle(tmp_path, rec("review", 6, 2), rec("resolve", 0), rec("review", 6, 1))[0] == 1


def test_the_fix_cost_over_the_limit_or_unknown_is_u4(tmp_path):
    code, m = settle(tmp_path, rec("review", 6, 3), rec("resolve", 0, cost=1.6), rec("review", 6, 2))
    assert (code, m["reason"]) == (2, "U4")
    code, m = settle(tmp_path, rec("review", 6, 3), rec("resolve", 0, unknown=True), rec("review", 6, 2))
    assert (code, m["reason"]) == (2, "U4")
    # review の費用は足さない
    assert settle(tmp_path, rec("review", 6, 3, cost=1.0), rec("resolve", 0, cost=1.4), rec("review", 6, 2, cost=1.0))[0] == 1


def test_the_seconds_after_the_first_fix_over_the_limit_is_u5(tmp_path):
    def log(last: float):
        return rec("review", 6, 3, seconds=9999), rec("resolve", 0, seconds=1000), rec("review", 6, 2, seconds=last)

    code, m = settle(tmp_path, *log(2601))
    assert (code, m["reason"], m["seconds"]) == (2, "U5", 3601.0)
    assert settle(tmp_path, *log(2600))[0] == 1


def test_the_cli_prints_the_result_and_returns_the_code(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"log": [rec("review", 6, 2), rec("resolve", 0), rec("review", 6, 2)]}))
    p = cli(
        "review-settled", "--state", str(path), "--step", "review", "--fix-step", "resolve",
        "--max-attempts", "2", "--max-cost", "1.5", "--max-seconds", "3600", cwd=tmp_path,
    )  # fmt: skip
    out = json.loads(p.stdout.strip().splitlines()[-1])
    assert p.returncode == 2 and out["tool"] == "review-settled" and out["metrics"]["reason"] == "U3"


def test_the_first_limit_in_order_wins_when_several_are_hit(tmp_path):
    """現状固定: 複数の上限へ同時に当たると、未解決 0 件 → U2 → U3 → U4 → U5 の順で先のものを返す。"""

    def fix(**kw):
        return rec("resolve", 0, seconds=4000, cost=2.0, **kw)

    def reason(*log):
        return settle(tmp_path, *log)[1]["reason"]

    assert settle(tmp_path, rec("review", 6, 3), fix(), fix(), rec("review", 6, 0))[0] == 0
    assert reason(rec("review", 6, 2), fix(), fix(), rec("review", 6, 2)) == "U2"
    assert reason(rec("review", 6, 2), fix(), rec("review", 6, 2)) == "U3"
    assert reason(rec("review", 6, 3), fix(), rec("review", 6, 2)) == "U4"
    assert reason(rec("review", 6, 3), rec("resolve", 0, seconds=4000), rec("review", 6, 2)) == "U5"


def test_the_result_summary_and_metrics_for_each_reason(tmp_path):
    """現状固定: 入口が返す status・summary・metrics の形。"""
    path = tmp_path / "state.json"

    def run(*log):
        path.write_text(json.dumps({"log": list(log)}))
        return commands.cmd_review_settled(str(path), "review", "resolve", **LIMITS)

    res, code = run(rec("review", 6, 0))
    assert (code, res["status"], res["summary"]) == (0, "ok", "未解決 0 件。解決を続けるか通常の経路: 通常の経路へ進む")
    res, code = run(rec("review", 6, 3), rec("resolve", 0, seconds=12.34, cost=0.12345), rec("review", 6, 2, seconds=1.0))
    assert (code, res["status"], res["summary"]) == (1, "stopped", "未解決 2 件。解決を続けるか通常の経路: 解決のフローを続ける")
    assert res["metrics"] == {"unresolved": 2, "attempts": 1, "cost": 0.1235, "seconds": 13.3, "reason": None}
    res, code = run(rec("review", 1, 0))
    assert (code, res["summary"]) == (2, "U1: review の最後の終了コードが 6 でない（1）")
    assert res["metrics"] == {"unresolved": None, "attempts": 0, "cost": 0.0, "seconds": 0.0, "reason": "U1"}
    res, code = run(rec("review", 6, 3), rec("resolve", 0, cost=1.6), rec("review", 6, 2))
    assert res["summary"] == "未解決 2 件。U4: 直しの費用が上限 $1.5 を超えたか不明"
