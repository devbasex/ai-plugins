"""リファクタリング計画のコメントの書き直し（`plan-comment`。#1692）。

駆動（`drive.py`）が結果の出口（done・stopped・pause）で 1 度だけ打ち、conductor はプランの外の取り消しを
push した後に `--scan-reverts` を付けて打つ。**コメントを書き直すのはこの子コマンドだけである**（決定 1）。

| 終了コード | 意味 |
| --- | --- |
| 0 | 書き直した・書く対象が無い（`PLAN_COMMENT=skipped`。計画の前・置き場所がコメントでない） |
| 1 | 投稿・編集に失敗した（`PLAN_COMMENT=failed`。状態ファイルの変更は保存する） |
| 4 | 状態ファイルが無い・`--scan-reverts` で origin を取り込めない（コメントも状態も変えない） |

出力: `PLAN_COMMENT`・`UNPUBLISHED`（0 / 1。判定できなければ空）・`PLAN_URL`。`--scan-reverts` では
`REVERTED_OUTSIDE`（今回取り消しにした項目）と `RESTORED_OUTSIDE`（今回元の状態へ戻した項目）も出す。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from typing import Any, Optional

import statefile

from .. import die, ledger, outside_reverts
from ..items import implement_shas
from ..paths import full_commit, load_state
from ..plan import PLAN_COMMENT, plan_mode, write_plan_comment


def unpublished(state: dict[str, Any], point: Optional[str] = None) -> Optional[bool]:
    """手元の HEAD が、公開した地点から到達できないコミットを持つか。判定できなければ `None`（決定 6）。

    公開した地点は `point`（`--scan-reverts` の `FETCH_HEAD`）か、無ければ `ledger.published_point`。
    """
    point = point or ledger.published_point(state)
    work = (state.get("worktrees") or {}).get("work")
    if not point or not work:
        return None
    try:
        p = subprocess.run(["git", "merge-base", "--is-ancestor", "HEAD", point], cwd=work, capture_output=True)
    except OSError:
        return None
    return {0: False, 1: True}.get(p.returncode)


def apply_outside_reverts(state: dict[str, Any], reverted: dict[str, str]) -> tuple[list[str], list[str]]:
    """取り消されたままの実装コミットの項目を取り消しにし、取り消しが取り消された項目を元へ戻す（I7）。"""
    work = state["worktrees"]["work"]
    marked: list[str] = []
    restored: list[str] = []
    for item in state.get("items") or []:
        reverter = next(
            (r for r in (outside_reverts.reverter_of(reverted, full_commit(work, sha)) for sha in implement_shas(item)) if r), None
        )
        if reverter and ledger.is_live(item):
            ledger.mark_outside_revert(item, reverter)
            marked.append(str(item.get("id")))
        elif not reverter and ledger.restore_outside_revert(item):
            restored.append(str(item.get("id")))
    return marked, restored


def cmd_plan_comment(args: argparse.Namespace) -> None:
    path, state = load_state(args.id)
    if not state.get("plan"):
        # 計画の前（提案で止まった・提案が 0 件）は書く改善項目が無い（I3）
        statefile.emit(PLAN_COMMENT="skipped", UNPUBLISHED=None)
        return
    point = None
    extra: dict[str, Any] = {}
    if args.scan_reverts:
        scanned = outside_reverts.read_origin_reverts(state["worktrees"]["work"], state["plan"].get("base_sha") or "", state["head_branch"])
        if scanned is None:
            die(f"origin の {state['head_branch']} を取り込めないため、コメントも状態も変えません")
        point, reverted = scanned
        marked, restored = apply_outside_reverts(state, reverted)
        ledger.note_publication(state, "observed", sha=point)
        extra = {"REVERTED_OUTSIDE": " ".join(marked), "RESTORED_OUTSIDE": " ".join(restored)}
    flag = unpublished(state, point)
    outcome, url = write_plan_comment(state, flag) if plan_mode(state) == PLAN_COMMENT else ("skipped", None)
    statefile.save(path, state)
    statefile.emit(PLAN_COMMENT=outcome, UNPUBLISHED=flag, PLAN_URL=url, **extra)
    if outcome == "failed":
        sys.exit(1)
