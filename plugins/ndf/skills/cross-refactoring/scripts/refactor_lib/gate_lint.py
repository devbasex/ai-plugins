"""最終ゲートの静的解析（#1483 決定 7・I12）。`commands/gate.py` が呼ぶ。

静的解析の全体テストは、全体テストを CI に任せる戦略でも手元で走らせる。落ちた suite は着手前の suite ごとの成否
（`baseline_test.suites`）と `test_triage.lint_verdict` で既存失敗か変更起因に分け、変更起因が無ければ通す。
起動の失敗なら `launch.stop` で止める。
"""

from __future__ import annotations

import pathlib
import time
from typing import Any, Optional

import statefile
import test_strategy as ts
import test_triage

from . import info, launch, timeline, triage
from .gitfacts import run_with_timeout
from .paths import work_dir


def _judge_suite(path: pathlib.Path, state: dict[str, Any], suite: Any, i: int, env: dict[str, Any]) -> Optional[dict[str, Any]]:
    """静的解析の suite を 1 本走らせ、落ちたときの判定を返す。通れば None。

    `env` は suite をまたいで共有する材料（work・timeout・tmp・baseline・per_suite・started）と、
    初めて要ったときに 1 度だけ求める変更ファイルの並び（changed）を持つ。
    """
    work, tmp, baseline, per_suite = env["work"], env["tmp"], env["baseline"], env["per_suite"]
    log = tmp / f"final-lint-{i}.log"
    code, timed_out = test_triage.run_within(
        env["timeout"], env["started"], lambda left, command=suite.command, log=log: run_with_timeout(command, work, left, output=log)
    )
    result = ts.outcome(code, timed_out)
    if result.launch_failed:
        launch.stop(path, state, "gate", suite.command, result, log)
    if result.status == ts.PASSED:
        return None
    if result.status == ts.TIMED_OUT:
        return {"suite": suite.name, "verdict": "caused", "reason": result.reason, "command": suite.command}
    if env["changed"] is None:
        head = triage.baseline_head(state)
        env["changed"] = test_triage.changed_since(work, head) if head else []
    before = (per_suite or {}).get(suite.name) if per_suite is not None else baseline.get("status")
    verdict = test_triage.lint_verdict(
        work=work,
        suite=suite,
        baseline=before,
        changed=env["changed"],
        timeout=timeline.state_test_timeout(state),
        log=tmp / f"final-lint-{i}-scope.log",
        run=triage.run_test,
    )
    narrowed = verdict.pop("outcome")
    if narrowed is not None and narrowed.launch_failed:
        launch.stop(path, state, "gate", verdict["command"], narrowed, tmp / f"final-lint-{i}-scope.log")
    return {**verdict, "command": verdict["command"] or suite.command}


def _lint_detail(suites: list[Any], verdicts: list[dict[str, Any]]) -> str:
    return f"静的解析 {len(suites)} 本" + (
        "（通った）" if not verdicts else "（" + "・".join(f"{v['suite']}: {v['reason']}" for v in verdicts) + "）"
    )


def _record_lint_check(gate: dict[str, Any], suites: list[Any], passed: bool, detail: str, seconds: float) -> None:
    gate.setdefault("checks", []).append(
        {
            "at": statefile.now(),
            "mode": "lint",
            "command": " && ".join(s.command for s in suites),
            "status": "pass" if passed else "fail",
            "detail": detail,
            "seconds": seconds,
        }
    )


def lint_gate(path: pathlib.Path, state: dict[str, Any], gate: dict[str, Any]) -> tuple[bool, str]:
    """静的解析の全体テストを、戦略に関わらず手元で走らせる（#1483 決定 7）。落ちた suite は
    `test_triage.lint_verdict` で着手前の成否から判定し、変更起因が無ければ通す（I12）。起動の失敗なら止める。

    戻りは（通ったか, 記録の 1 行）。静的解析の suite が無ければ `(True, "")`。
    """
    strategy = timeline.strategy_of(state)
    suites = [s for s in strategy.suites if s.kind == ts.LINT and s.command]
    if not suites:
        return True, ""
    baseline = state.get("baseline_test") or {}
    env: dict[str, Any] = {
        "work": work_dir(state),
        "timeout": timeline.state_whole_timeout(state),
        "tmp": pathlib.Path(state["tmp_dir"]),
        "baseline": baseline,
        "per_suite": baseline.get("suites") if isinstance(baseline.get("suites"), dict) else None,
        "changed": None,
    }
    started = env["started"] = time.monotonic()
    verdicts: list[dict[str, Any]] = []
    for i, suite in enumerate(suites):
        verdict = _judge_suite(path, state, suite, i, env)
        if verdict is not None:
            verdicts.append(verdict)
    caused = [v for v in verdicts if v["verdict"] == "caused"]
    gate["lint"] = verdicts
    passed = not caused
    seconds = round(time.monotonic() - started, 1)
    detail = _lint_detail(suites, verdicts)
    for v in verdicts:
        if v["verdict"] == "preexisting":
            info(f"ℹ 静的解析の suite {v['suite']} は{v['reason']}")
    _record_lint_check(gate, suites, passed, detail, seconds)
    statefile.save(path, state)
    return passed, detail
