"""提案の取り込み（`merge-proposals`、#933 の F2）。

参加者の全員が 1 度だけ出した提案を読み、鍵が同じ提案を統合して、改修計画へ渡す候補を
切り出す。**意味の上で同じ提案かはここで決めない**（改修計画の中で Jev か実装担当が問う）。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any, Optional

import statefile

from .. import info
from ..items import defer, item_label
from ..paths import load_state, result_path, stem_for
from ..phases import finish_phase, phase_record
from ..proposals import build_candidates


def _read_runtime_proposal(result: pathlib.Path) -> Optional[list[dict[str, Any]]]:
    """1 者の提案の結果を読む。**1 者が欠けても全体は止めない。**"""
    runtime = result.name.split("-", 1)[0]
    if not result.exists():
        info(f"⚠ {runtime} の提案結果がありません: {result}")
        return None
    try:
        payload = json.loads(result.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        info(f"⚠ {runtime} の提案結果が JSON として読めません: {e}")
        return None
    if not isinstance(payload, dict):
        info(
            f"⚠ {runtime} の提案結果が JSON オブジェクトではありません"
            f"（{type(payload).__name__}）。提案なしとして扱います"
        )
        return []
    items = payload.get("items")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def load_proposals(state: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """参加者ごとの提案。結果の無い者は除く（件数は `proposed` に残す）。"""
    proposals: dict[str, list[dict[str, Any]]] = {}
    counts: dict[str, Optional[int]] = {}
    for runtime in state["runtimes"]:
        result = result_path(state, runtime, stem_for(runtime, "propose", state["id"]))
        found = _read_runtime_proposal(result)
        counts[runtime] = None if found is None else len(found)
        if found is not None:
            proposals[runtime] = found
    state["proposed"] = counts
    return proposals


def cmd_merge_proposals(args: argparse.Namespace) -> None:
    """提案を統合して候補を作る。

    終了コード: 0 = 候補あり / 2 = 候補 0 件（最終ゲートへ）。

    **叩き直しても二重に候補を作らない。** 取り込み済み（`phases.propose.ended_at`）
    なら前回の結果をそのまま返す。
    """
    path, state = load_state(args.id)
    if phase_record(state, "propose").get("ended_at"):
        info(f"↻ 提案は取り込み済みです（候補 {len(state.get('candidates') or [])} 件）")
        if not state.get("candidates"):
            sys.exit(2)
        return

    proposals = load_proposals(state)
    candidates, deferred = build_candidates(
        proposals, threshold=str(state.get("severity_threshold") or "minor"))
    state["candidates"] = candidates
    for item, reason in deferred:
        defer(state, item, reason)
    finish_phase(state, "propose")
    if not candidates:
        state["phase"] = "final"
    statefile.save(path, state)

    total = sum(n for n in (state.get("proposed") or {}).values() if n)
    info(f"提案 {total} 件 → 候補 {len(candidates)} 件 / 見送り {len(deferred)} 件")
    for item in candidates:
        info(f"  {item['id']} [{item['severity']}] {item_label(item)} "
             f"{item['smell']} → {item['technique']}（賛同 {len(item['proposed_by'])}）")
    if not candidates:
        info("候補が 0 件のため、最終ゲートへ進みます")
        sys.exit(2)
