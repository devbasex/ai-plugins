"""閉じる条件 3（最終工程の記録。#2228）の判定（`sprint-close.py` から分けた）。

配布とリリース後テストの記録は条件 1・2 が見るため、ここは振り返りの記録だけを判定する。出す（emit）のは呼び手である。
"""

from __future__ import annotations

import re

UNVERIFIED_FINAL = "閉じているが閉じる条件 3 を満たさない: "
STAGE_DIST, STAGE_VERIFY, STAGE_RETRO = "配布", "リリース後テスト", "振り返り"
FINAL_STAGES = (STAGE_DIST, STAGE_VERIFY, STAGE_RETRO)
NO_BASE = 0  # --record-pr 0 の比べる基準（配布の記録が無いため、どの識別子も数える）
_RETRO_LINE = re.compile(r"^振り返り:\s*https://github\.com/[\w.-]+/[\w.-]+/(?:issues|pull)/\d+#issuecomment-(\d+)\s*$", re.M)
FINAL_GAP = "最終工程（{stage}）の記録が無い: {what}"
FINAL_GAP_LEAD = "最終工程（"


def final_stage_gap(stage, body, since):
    """最終工程の記録が無ければ、何が無いかを含む理由を返す。あれば None。

    配布とリリース後テストの記録は条件 1・2 が見るため、ここでは振り返りだけを判定する。振り返りは本文の
    `振り返り: <コメントの URL>` の行のうち、識別子が since（記録の PR の最後の配布の記録のコメントの識別子）以上の
    ものを数える。since が None なら比べる基準が無いため満たさない。"""
    if stage != STAGE_RETRO:
        return None
    if since is None:
        return FINAL_GAP.format(stage=stage, what="比べる配布の記録のコメントが無い")
    ids = [int(x) for x in _RETRO_LINE.findall(body or "")]
    if not ids:
        return FINAL_GAP.format(stage=stage, what="課題の本文に振り返りの記録の行が無い")
    if max(ids) < since:
        return FINAL_GAP.format(stage=stage, what="課題の本文の振り返りの行が今回の配布の記録より前のもの")
    return None


def gap_item(base, state, gap):
    """条件 3 を満たさない課題の項目。閉じていれば開き直さない。"""
    if state == "CLOSED":
        return {**base, "result": "closed_unverified", "reason": UNVERIFIED_FINAL + gap}
    return {**base, "result": "kept_open", "reason": gap}
