"""プランの回をまたぐ記録（状態の保存先の `prs.json`・`outputs.json`・`step-gaps.json`・`models.json`・`units.json`・
`progress.jsonl`）の読み書き。`units.json` だけは回をまたがず、書いた回の id を持つ（#2944）。

書くのは耐久ステップ（`runner.run_step`・`runner.record_choices`）で、読むのはプランの結果を組む `runner` である。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Optional

from ndf import contracts
from ndf.contracts import chosen, internal, result
from ndf.core.plan.files import read_json, write_json

# ステップの結果が載せた PR の番号を、回をまたいで集める記録（プランの結果の items へ載せる）
PRS_FILE = "prs.json"
# ステップの結果の outputs を回をまたいで重ねる記録（プランの結果の outputs へ載せる）
OUTPUTS_FILE = "outputs.json"
OUTPUTS_SCHEMA = result.SCHEMA + "#/$defs/outputs"
# 止まったステップが結果 JSON に残した欠け（ステップの id → gaps）。プランの停止結果（incomplete）の詳細に載せる
STEP_GAPS_FILE = "step-gaps.json"
GAP_SCHEMA = result.SCHEMA + "#/$defs/gap"
# 選択の記録を追記する進捗記録（追記だけ。書き換えない）
PROGRESS_FILE = "progress.jsonl"
# ステップごとの起動の指定した値と実際のモデル（ステップの id → 値。プランの結果の metrics.models に載せる。#2944）
MODELS_FILE = "models.json"
# 回の工程の割り付け ID（assigns）と、検査の結果を待つ修正（pending）。回の id（run）が違う記録は使わない（#2944）
UNITS_FILE = "units.json"

log = logging.getLogger(__name__)


def _add_models(state_dir: Path, step_id: str, launch: Mapping[str, Any]) -> None:
    """ステップ `step_id` の最後の起動の、指定した値と実際のモデルを残す。"""
    kept = {k: launch[k] for k in ("name", "requested_model", "effort", "model") if k in launch}
    write_json(state_dir / MODELS_FILE, {**recorded_models(state_dir), step_id: kept})


def recorded_models(state_dir: Path) -> dict[str, Any]:
    """ステップごとの起動の値。無ければ空。"""
    found = read_json(state_dir / MODELS_FILE)
    return found if found is not None else {}


def read_units(state_dir: Path) -> dict[str, Any]:
    """割り付け ID と結果待ちの修正の記録。無ければ空。読めないか壊れていれば警告を出して空。"""
    path = state_dir / UNITS_FILE
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        # 割り付けの無い回は記録が無い
        return {}
    except (OSError, ValueError) as exc:  # ファイル: 壊れた記録の回は効果指標を書かない
        log.warning("割り付けの記録 %s を読めないので、空として続ける: %s", path, exc)
        return {}
    shaped = isinstance(found, dict) and all(isinstance(found[k], t) for k, t in (("assigns", dict), ("pending", list)) if k in found)
    if not shaped:
        log.warning("割り付けの記録 %s の形が違うので、空として続ける", path)
        return {}
    return found


def write_units(state_dir: Path, value: dict[str, Any]) -> None:
    write_json(state_dir / UNITS_FILE, value)


def _add_outputs(state_dir: Path, outputs: dict[str, Any]) -> None:
    found = _stored_outputs(state_dir)
    write_json(state_dir / OUTPUTS_FILE, {**(found if found is not None else {}), **outputs})


def _stored_outputs(state_dir: Path) -> Optional[dict[str, Any]]:
    """`outputs.json` の outputs。旧い形は今の形へ移す。"""
    found = read_json(state_dir / OUTPUTS_FILE)
    return None if found is None else internal.stage_values(found)


def read_result(path: Path) -> Optional[dict[str, Any]]:
    """結果のバックアップ `path`。無ければ None。旧い形の outputs は今の形へ移す。新しいバージョンなら `InternalError`（precondition）。"""
    found = read_json(path)
    if result.newer(found):
        raise internal.InternalError("precondition", f"新しいバージョンの保存済みの結果 {path} を古い NDF が読んだ。プラグインを更新する")
    if found is None or not isinstance(found.get("outputs"), dict):
        return found
    return {**found, "outputs": internal.stage_values(found["outputs"])}


def recorded_outputs(state_dir: Path) -> tuple[Optional[dict[str, Any]], list[str]]:
    """プランのステップが渡した outputs と、結果 JSON の形に合わないときの注意。形が違えば outputs を載せない。"""
    found = _stored_outputs(state_dir)
    if not found:
        return None, []
    errs = contracts.schema_errors(found, OUTPUTS_SCHEMA)
    if errs:
        return None, [f"ステップの outputs が結果 JSON の形に合わず載せなかった: {'; '.join(errs)}"]
    return found, []


def _keep_step_gaps(state_dir: Path, step_id: str, gaps: Optional[list[Any]]) -> None:
    """ステップ `step_id` の最後の回が残した欠けを記録する。欠けが無ければ前の回の記録を外す（打ち直しで通ったステップの欠けを残さない）。"""
    found = read_json(state_dir / STEP_GAPS_FILE)
    kept = {k: v for k, v in (found if isinstance(found, dict) else {}).items() if k != step_id}
    if gaps:
        kept[step_id] = gaps
    if kept or found is not None:
        write_json(state_dir / STEP_GAPS_FILE, kept)


def recorded_step_gaps(state_dir: Path) -> dict[str, list[dict[str, Any]]]:
    """止まったステップごとの欠け。欠けの形（結果 JSON の gap）に合わない要素は載せない（停止結果の形を壊さない）。"""
    found = read_json(state_dir / STEP_GAPS_FILE)
    if not isinstance(found, dict):
        return {}
    return {
        k: gaps for k, v in found.items() if isinstance(v, list) and (gaps := [g for g in v if not contracts.schema_errors(g, GAP_SCHEMA)])
    }


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
