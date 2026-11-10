"""閉じる条件 3（最終工程の記録。#2228）の判定（`sprint-close.py` から分けた）。

デプロイとリリース後テストの記録は条件 1・2 が見るため、ここは振り返りの記録だけを判定する。出す（emit）のは呼び手である。
"""

from __future__ import annotations

import argparse
import re

import jsonio
import legacy_values

UNVERIFIED_FINAL = "閉じているが閉じる条件 3 を満たさない: "
STAGE_DIST, STAGE_VERIFY, STAGE_RETRO = "デプロイ", "リリース後テスト", "振り返り"
FINAL_STAGES = (STAGE_DIST, STAGE_VERIFY, STAGE_RETRO)
NO_BASE = 0  # --record-pr 0 の比べる基準（リリース記録が無いため、どの識別子も数える）
_RETRO_LINE = re.compile(r"^振り返り:\s*https://github\.com/[\w.-]+/[\w.-]+/(?:issues|pull)/\d+#issuecomment-(\d+)\s*$", re.M)
FINAL_GAP = "最終工程（{stage}）の記録が無い: {what}"
FINAL_GAP_LEAD = "最終工程（"


def final_stage_arg(value):
    """--final-stage の値。前の版のプランが渡す旧い工程名も受ける。"""
    stage = legacy_values.to_current("stage", value)
    if stage not in FINAL_STAGES:
        raise argparse.ArgumentTypeError(f"{' / '.join(FINAL_STAGES)} のどれでもない: {value}")
    return stage


def stage_from_progress(path, given: str) -> tuple[str, str]:
    """(最終工程, 根拠)。`path`（まとめのプランの進捗記録 progress.jsonl）が無ければ `given`（--final-stage）を使う。

    同じプランで retro が通っていれば振り返り、通っていなければリリース後テスト（#2914 の I5）。見る範囲は `--from` の無い
    最後の実行の始まりより後（続きから流した実行を含む）。読めなければリリース後テスト（条件 2 を必ず見る側）。"""
    if not path:
        return given, f"--final-stage {given}（省略時は{STAGE_RETRO}）"
    rows = jsonio.read_jsonl(path, missing=[], broken=[], skip_broken=True)
    cut = max((i for i, r in enumerate(rows) if r.get("kind") == "run" and r.get("from") is None), default=-1)
    ran = any(r.get("kind") == "step" and r.get("step") == "retro" and r.get("exit") == 0 for r in rows[cut + 1 :])
    return (STAGE_RETRO if ran else STAGE_VERIFY), f"--final-stage-from {path}（retro が通っていれば{STAGE_RETRO}、無ければ{STAGE_VERIFY}）"


def final_stage_gap(stage, body, since):
    """最終工程の記録が無ければ、何が無いかを含む理由を返す。あれば None。

    デプロイとリリース後テストの記録は条件 1・2 が見るため、ここでは振り返りだけを判定する。振り返りは本文の
    `振り返り: <コメントの URL>` の行のうち、識別子が since（記録の PR の最後のリリース記録のコメントの識別子）以上の
    ものを数える。since が None なら比べる基準が無いため満たさない。"""
    if stage != STAGE_RETRO:
        return None
    if since is None:
        return FINAL_GAP.format(stage=stage, what="比べるリリース記録のコメントが無い")
    ids = [int(x) for x in _RETRO_LINE.findall(body or "")]
    if not ids:
        return FINAL_GAP.format(stage=stage, what="課題の本文に振り返りの記録の行が無い")
    if max(ids) < since:
        return FINAL_GAP.format(stage=stage, what="課題の本文の振り返りの行が最後のリリース記録より前のもの")
    return None


def gap_item(base, state, gap):
    """条件 3 を満たさない課題の項目。閉じていれば開き直さない。"""
    if state == "CLOSED":
        return {**base, "result": "closed_unverified", "reason": UNVERIFIED_FINAL + gap}
    return {**base, "result": "kept_open", "reason": gap}
