"""本番のプランの形の判定（純粋な関数。#3572 の決定 2）。

| 判定 | 何を見るか |
| --- | --- |
| `forbidden` | `release: true` のプランが、承認の後にコードを変える worker のステップや、直しのステップ（`FIX_STEPS`）を持たず、最初のステップが本番の PR のマージ（`merge_run`）であるか（不変条件 I1・I2） |
| `guard_first` | `merge` のプランの最初のステップが `release-guard` で、その止まる原因に行き先が無いか（I8） |
| `is_production_pr` | PR を `merge` のプランで流してはいけない（base が本番ブランチ）か（I8） |

プランは `ndf.core.route.expanded` の形（`use` を開き、差し込みは埋めていない）で受ける。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ndf import identity
from ndf.contracts import causes

# worker の起動の差し込み（雛形の `{{worker}}`）
WORKER = "{{worker}}"
# 承認の後に置かない直しのステップ（コードを変えるか、リリースブランチへ push する）
FIX_STEPS = frozenset({"push", "fix-push", "ci-fix", "fix-base", "base-sync"})
# `merge` のプランの最初のステップの id と、本番の PR で止まる終了コード（止まった理由の表の refused の終了コード）
GUARD_ID = "release-guard"
GUARD_EXIT = causes.exit_code("refused")


def guard_cause() -> str:
    """`release-guard` が本番の PR で止まる原因の種類（止まった理由の表の既存の種類。行き先を持たない）。"""
    return "refused"


def merge_run() -> tuple[str, ...]:
    """本番のプランの最初のステップの起動の頭（`ndf release-steps merge`）。"""
    return (identity.FIXED_WORDS["entrypoint"], "release-steps", "merge")


def _steps(plan: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    steps = plan["steps"] if "steps" in plan else []
    return [s for s in steps if isinstance(s, Mapping)] if isinstance(steps, list) else []


def _run(step: Mapping[str, Any]) -> list[str]:
    got = step["run"] if "run" in step else []
    return [str(a) for a in got] if isinstance(got, list) else []


def forbidden(plan: Mapping[str, Any]) -> list[str]:
    """`release: true` のプランが I1・I2 を破るステップを 1 件 1 行で返す（`release` が偽なら空）。"""
    if plan.get("release") is not True:
        return []
    steps = _steps(plan)
    out = []
    for s in steps:
        sid = str(s["id"]) if "id" in s else "?"
        run = _run(s)
        if run and run[0] == WORKER:
            out.append(f"{sid}: worker を起動する（承認の後にコードを変える主体を置かない）")
        if sid in FIX_STEPS:
            out.append(f"{sid}: 直しのステップ（承認の後にリリースブランチへ push しない）")
    first = _run(steps[0]) if steps else []
    head = merge_run()
    if tuple(first[: len(head)]) != head:
        out.append(f"最初のステップが {' '.join(head)} でない（承認の直後は本番の PR のマージ）")
    return out


def guard_first(plan: Mapping[str, Any]) -> list[str]:
    """`merge` のプランの最初のステップが `release-guard` で、止まる原因 `refused` に行き先が無いかを確かめ、破れを返す。"""
    steps = _steps(plan)
    if not steps or steps[0].get("id") != GUARD_ID:
        return [f"最初のステップが {GUARD_ID} でない"]
    first = steps[0]
    causes = first["exit_cause"] if isinstance(first.get("exit_cause"), Mapping) else {}
    out = []
    cause = guard_cause()
    if causes.get(str(GUARD_EXIT)) != cause:
        out.append(f"{GUARD_ID} の終了コード {GUARD_EXIT} の原因が {cause} でない")
    on_cause = first["on_cause"] if isinstance(first.get("on_cause"), Mapping) else {}
    if cause in on_cause:
        out.append(f"{GUARD_ID} の原因 {cause} に行き先がある（本番の PR を直しのステップへ流す）")
    return out


def is_production_pr(base_ref: str, production: str) -> bool:
    """PR の宛先が本番ブランチか（`merge` のプランで流してはいけない PR か。そのまま比べる）。"""
    return base_ref == production
