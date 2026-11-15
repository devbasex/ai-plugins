"""本番のプランの形の判定（純粋な関数。#3572 の決定 2）。

| 判定 | 何を見るか |
| --- | --- |
| `forbidden` | `release: true` のプランが、承認の後にコードを変える worker のステップや、直しのステップ（`FIX_STEPS`）・次の版の用意と同期の副命令（`AFTER_COMMANDS`）を持たず、最初のステップが本番の PR のマージ（`merge_run`）であるか（不変条件 I1・I2・I10） |
| `guard_before_merge` | `merge` の部品（`merge_part_ids` の id で、本番のプランのマージ `merge_run` でないもの）を持つプランで、最初の部品の直前が `release-guard` で、原因 `production_pr` の行き先が `end` か（I6・I13） |
| `is_production_pr` | PR を `merge` の部品でマージしてはいけない（base が本番ブランチ）か（I6） |

プランは `ndf.core.route.expanded` の形（`use` を開き、差し込みは埋めていない）で受ける。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ndf import identity
from ndf.contracts import causes
from ndf.domains.release.shapes import list_of

# worker の起動の差し込み（雛形の `{{worker}}`）
WORKER = "{{worker}}"
# 承認の後に置かない直しのステップ（コードを変えるか、リリースブランチへ push する）
FIX_STEPS = frozenset({"push", "fix-push", "ci-fix", "fix-base", "base-sync"})
# `merge` の部品の直前に置くステップの id
GUARD_ID = "release-guard"
# 本番のプランに置かない副命令（次の版のブランチの用意と同期。I10）
AFTER_COMMANDS = frozenset({"prepare-next", "defer"})
# `release-guard` が本番の PR を渡した後の行き先
GUARD_NEXT = "end"
# 本番のプランの最初のステップの起動の頭（`ndf release-steps merge`）
MERGE_RUN = (identity.FIXED_WORDS["entrypoint"], "release-steps", "merge")


def guard_cause() -> str:
    """`release-guard` が本番の PR をデプロイの工程へ渡す原因の種類。
    原因の文字列の持ち主はここだけで、終了コード `GUARD_EXIT` もここから求める。"""
    return "production_pr"


# 本番の PR を渡す終了コード（止まった理由の表の production_pr の終了コード）
GUARD_EXIT = causes.exit_code(guard_cause())


def merge_part_ids() -> tuple[str, ...]:
    """直前に `release-guard` を求める `merge` の部品の id（雛形の部品 `parts/steps.json` の id。並びの先にある方を見る）。"""
    return ("merge-gate", "merge")


def merge_run() -> tuple[str, ...]:
    """本番のプランの最初のステップの起動の頭（`MERGE_RUN`）。"""
    return MERGE_RUN


def _steps(plan: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [s for s in list_of(plan, "steps") if isinstance(s, Mapping)]


def _run(step: Mapping[str, Any]) -> list[str]:
    return [str(a) for a in list_of(step, "run")]


def forbidden(plan: Mapping[str, Any]) -> list[str]:
    """`release: true` のプランが I1・I2・I10 を破るステップを 1 件 1 行で返す（`release` が偽なら空）。"""
    if plan.get("release") is not True:
        return []
    steps = _steps(plan)
    out = []
    head = merge_run()
    for s in steps:
        sid = str(s["id"]) if "id" in s else "?"
        run = _run(s)
        if run and run[0] == WORKER:
            out.append(f"{sid}: worker を起動する（承認の後にコードを変える主体を置かない）")
        if sid in FIX_STEPS:
            out.append(f"{sid}: 直しのステップ（承認の後に本番の PR の head へ push しない）")
        if tuple(run[:2]) == head[:2] and len(run) > 2 and run[2] in AFTER_COMMANDS:
            out.append(f"{sid}: {run[2]} を打つ（本番のマージの後に次の版のブランチも同期の PR も作らない）")
    first = _run(steps[0]) if steps else []
    if tuple(first[: len(head)]) != head:
        out.append(f"最初のステップが {' '.join(head)} でない（承認の直後は本番の PR のマージ）")
    return out


def _mapping(step: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = step[key] if key in step else None
    return value if isinstance(value, Mapping) else {}


def guard_before_merge(plan: Mapping[str, Any]) -> list[str]:
    """`merge` の部品を持つプランで、最初の部品の直前が `release-guard` で、本番の PR を `end` へ渡すかを確かめ、破れを返す。"""
    steps = _steps(plan)
    parts = merge_part_ids()
    # 本番のプランのマージ（`merge_run` で始まるステップ）は `merge` の部品でなく、デプロイの工程そのもの
    head = merge_run()
    at = next((i for i, s in enumerate(steps) if s.get("id") in parts and tuple(_run(s)[: len(head)]) != head), None)
    if at is None:
        return []
    part = steps[at].get("id")
    if at == 0 or steps[at - 1].get("id") != GUARD_ID:
        return [f"{part} の直前が {GUARD_ID} でない"]
    guard = steps[at - 1]
    out = []
    cause = guard_cause()
    if _mapping(guard, "exit_cause").get(str(GUARD_EXIT)) != cause:
        out.append(f"{GUARD_ID} の終了コード {GUARD_EXIT} の原因が {cause} でない")
    if _mapping(guard, "on_cause").get(cause) != GUARD_NEXT:
        out.append(f"{GUARD_ID} の原因 {cause} の行き先が {GUARD_NEXT} でない（本番の PR をマージや直しのステップへ流す）")
    return out


def is_production_pr(base_ref: str, production: str) -> bool:
    """PR の宛先が本番ブランチか（`merge` の部品でマージしてはいけない PR か。そのまま比べる）。"""
    return base_ref == production
