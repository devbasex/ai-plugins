"""プランの回をまたぐ記録（状態の保存先の `prs.json`・`outputs.json`・`step-gaps.json`・`step-missing.json`・`progress.jsonl`）の読み書き。

書くのは耐久ステップ（`runner.run_step`・`runner.record_choices`）で、読むのはプランの結果を組む `runner` である。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from ndf import contracts
from ndf.contracts import chosen, internal, result
from ndf.contracts.exit_codes import GATE
from ndf.core.plan.files import read_json, write_json

# ステップの結果が載せた PR の番号を、回をまたいで集める記録（プランの結果の items へ載せる）
PRS_FILE = "prs.json"
# ステップの結果の outputs を回をまたいで重ねる記録（プランの結果の outputs へ載せる）。ステップごとの要素の並び
# `{"entries": [{"step": <id か null>, "outputs": {...}}, ...]}` で持ち、先頭から重ねる（#4513）
OUTPUTS_FILE = "outputs.json"
ENTRIES = "entries"
OUTPUTS_SCHEMA = result.SCHEMA + "#/$defs/outputs"
# 止まったステップが結果 JSON に残した欠け（ステップの id → gaps）。プランの停止結果（incomplete）の詳細に載せる
STEP_GAPS_FILE = "step-gaps.json"
GAP_SCHEMA = result.SCHEMA + "#/$defs/gap"
# 判断を求めたステップの最後の回が確かめを通さなかった必須の出力の名前（ステップの id → 名前の並び。#4513）
STEP_MISSING_FILE = "step-missing.json"
# 選択の記録を追記する進捗記録（追記だけ。書き換えない）
PROGRESS_FILE = "progress.jsonl"


def _add_outputs(state_dir: Path, step_id: str, status: str, outputs: Optional[dict[str, Any]]) -> None:
    """ステップ `step_id` の結果の outputs を要素の並びに足す（#4513）。

    ok・stopped なら、同じステップの前の要素から今回のキーを外し、今回の要素を末尾に足す（同じステップを打ち直しても要素は
    増えない）。gate（判断を求めた）なら、同じステップの前の要素をすべて外し、今回の outputs があれば末尾に足す（却下して
    打ち直した前の回の値を混ぜない）。ステップの分からない要素（直す前の形）は外さない。
    """
    kept: list[dict[str, Any]] = []
    for entry in _stored_entries(state_dir):
        if entry["step"] != step_id:
            kept.append(entry)
        elif status != GATE and outputs:
            rest = {k: v for k, v in entry["outputs"].items() if k not in outputs}
            if rest:
                kept.append({"step": step_id, "outputs": rest})
    if outputs:
        kept.append({"step": step_id, "outputs": dict(outputs)})
    write_json(state_dir / OUTPUTS_FILE, {ENTRIES: kept})


def _stored_entries(state_dir: Path) -> list[dict[str, Any]]:
    """`outputs.json` の要素の並び。直す前の形（キーと値だけのオブジェクト）はステップの分からない要素 1 つにする。

    旧い形のキー名は要素ごとに今の形へ移す。
    """
    found = read_json(state_dir / OUTPUTS_FILE)
    if found is None:
        return []
    raw = found[ENTRIES] if isinstance(found.get(ENTRIES), list) else [{"step": None, "outputs": found}]
    return [
        {"step": e.get("step"), "outputs": internal.stage_values(e["outputs"])}
        for e in raw
        if isinstance(e, dict) and isinstance(e.get("outputs"), dict)
    ]


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
    found: dict[str, Any] = {}
    for entry in _stored_entries(state_dir):
        found.update(entry["outputs"])
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


def _keep_step_missing(state_dir: Path, step_id: str, status: str, names: Optional[list[str]]) -> None:
    """判断を求めた（`status` が gate の）ステップ `step_id` が確かめを通さなかった必須の出力の名前を残す。無ければ前の回の記録を外す（#4513）。

    記録を読むのは、承認ゲートで止まった最後のステップのぶんだけである（`runner.final_result`）。そのステップは止まった回に
    ここを通って記録を書き直しているので、ほかの結果の回は読みも書きもしない。内容が変わらなければ書かない。
    """
    if status != GATE:
        return
    found = read_json(state_dir / STEP_MISSING_FILE)
    before = found if isinstance(found, dict) else {}
    kept = {k: v for k, v in before.items() if k != step_id}
    if names:
        kept[step_id] = list(names)
    if kept != before:
        write_json(state_dir / STEP_MISSING_FILE, kept)


def recorded_step_missing(state_dir: Path) -> dict[str, list[str]]:
    """判断を求めたステップごとの、確かめを通さなかった必須の出力の名前。名前の並びでない値は載せない。"""
    found = read_json(state_dir / STEP_MISSING_FILE)
    if not isinstance(found, dict):
        return {}
    return {k: list(v) for k, v in found.items() if isinstance(v, list) and v and all(isinstance(n, str) for n in v)}


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
