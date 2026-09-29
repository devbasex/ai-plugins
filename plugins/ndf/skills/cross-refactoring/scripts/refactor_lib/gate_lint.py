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


def lint_gate(path: pathlib.Path, state: dict[str, Any], gate: dict[str, Any]) -> tuple[bool, str]:
    """静的解析の全体テストを、戦略に関わらず手元で走らせる（#1483 決定 7）。落ちた suite は
    `test_triage.lint_verdict` で着手前の成否から判定し、変更起因が無ければ通す（I12）。起動の失敗なら止める。

    戻りは（通ったか, 記録の 1 行）。静的解析の suite が無ければ `(True, "")`。
    """
    strategy = timeline.strategy_of(state)
    suites = [s for s in strategy.suites if s.kind == ts.LINT and s.command]
    if not suites:
        return True, ""
    work = work_dir(state)
    timeout = timeline.state_whole_timeout(state)
    tmp = pathlib.Path(state["tmp_dir"])
    baseline = state.get("baseline_test") or {}
    per_suite = baseline.get("suites") if isinstance(baseline.get("suites"), dict) else None
    changed: Optional[list[str]] = None
    started = time.monotonic()
    verdicts: list[dict[str, Any]] = []
    for i, suite in enumerate(suites):
        log = tmp / f"final-lint-{i}.log"
        code, timed_out = test_triage.run_within(
            timeout, started, lambda left, command=suite.command, log=log: run_with_timeout(command, work, left, output=log)
        )
        result = ts.outcome(code, timed_out)
        if result.launch_failed:
            launch.stop(path, state, "gate", suite.command, result, log)
        if result.status == ts.PASSED:
            continue
        if result.status == ts.TIMED_OUT:
            verdicts.append({"suite": suite.name, "verdict": "caused", "reason": result.reason, "command": suite.command})
            continue
        if changed is None:
            head = triage.baseline_head(state)
            changed = test_triage.changed_since(work, head) if head else []
        before = (per_suite or {}).get(suite.name) if per_suite is not None else baseline.get("status")
        verdict = test_triage.lint_verdict(
            work=work,
            suite=suite,
            baseline=before,
            changed=changed,
            timeout=timeline.state_test_timeout(state),
            log=tmp / f"final-lint-{i}-scope.log",
            run=triage.run_test,
        )
        narrowed = verdict.pop("outcome")
        if narrowed is not None and narrowed.launch_failed:
            launch.stop(path, state, "gate", verdict["command"], narrowed, tmp / f"final-lint-{i}-scope.log")
        verdicts.append({**verdict, "command": verdict["command"] or suite.command})
    caused = [v for v in verdicts if v["verdict"] == "caused"]
    gate["lint"] = verdicts
    passed = not caused
    seconds = round(time.monotonic() - started, 1)
    detail = f"静的解析 {len(suites)} 本" + (
        "（通った）" if not verdicts else "（" + "・".join(f"{v['suite']}: {v['reason']}" for v in verdicts) + "）"
    )
    for v in verdicts:
        if v["verdict"] == "preexisting":
            info(f"ℹ 静的解析の suite {v['suite']} は{v['reason']}")
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
    statefile.save(path, state)
    return passed, detail
