"""プランの回をまたぐ記録（状態の置き場の `prs.json`・`outputs.json`・`progress.jsonl`）の読み書き。

書くのは耐久ステップ（`runner.run_step`・`runner.record_choices`）で、読むのはプランの結果を組む `runner` である。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from ndf import contracts
from ndf.contracts import chosen, result
from ndf.core.plan.files import read_json, write_json

# ステップの結果が載せた PR の番号を、回をまたいで集める記録（プランの結果の items へ載せる）
PRS_FILE = "prs.json"
# ステップの結果の outputs を回をまたいで重ねる記録（プランの結果の outputs へ載せる）
OUTPUTS_FILE = "outputs.json"
OUTPUTS_SCHEMA = result.SCHEMA + "#/$defs/outputs"
# 選択の記録を追記する進捗記録（追記だけ。書き換えない）
PROGRESS_FILE = "progress.jsonl"


def _add_outputs(state_dir: Path, outputs: dict[str, Any]) -> None:
    found = read_json(state_dir / OUTPUTS_FILE)
    write_json(state_dir / OUTPUTS_FILE, {**(found if found is not None else {}), **outputs})


def recorded_outputs(state_dir: Path) -> tuple[Optional[dict[str, Any]], list[str]]:
    """プランのステップが渡した outputs と、結果 JSON の形に合わないときの注意。形が違えば outputs を載せない。"""
    found = read_json(state_dir / OUTPUTS_FILE)
    if not found:
        return None, []
    errs = contracts.schema_errors(found, OUTPUTS_SCHEMA)
    if errs:
        return None, [f"ステップの outputs が結果 JSON の形に合わず載せなかった: {'; '.join(errs)}"]
    return found, []


def recorded_choices(state_dir: Path) -> list[dict[str, Any]]:
    """進捗記録に残った選択の記録（書いた順）。"""
    path = state_dir / PROGRESS_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        # まだ 1 件も選んでいない回は記録が無い
        return []
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    return [r for r in rows if isinstance(r, dict) and r.get("event") == chosen.EVENT]


def _add_prs(state_dir: Path, prs: list[int]) -> None:
    found = read_json(state_dir / PRS_FILE)
    known = list(found["prs"]) if found is not None else []
    write_json(state_dir / PRS_FILE, {"prs": known + [n for n in prs if n not in known]})


def recorded_prs(state_dir: Path) -> list[int]:
    """プランのステップが載せた PR の番号（載った順）。"""
    found = read_json(state_dir / PRS_FILE)
    return list(found["prs"]) if found is not None else []
