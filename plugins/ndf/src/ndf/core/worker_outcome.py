"""worker が書いた結果ファイルから `ndf worker run` の結果を組むところ（`core/worker.py` の部品）。

- 結果ファイルの status をコピーする（ok・gate・stopped。#1682）。gate は承認ゲートの結果、stopped と欠け（gaps）の付いた ok は
  欠けを付けた incomplete にする
- `--require-output` の名前（作業の定義の outputs。#3908）は、結果ファイルの `outputs` にあり、値が結果 JSON の
  `outputs` の形（`contracts/schemas/result.schema.json` の `$defs/outputs`）に合うかを確かめる。無いか形が違えば
  欠け（`kind` が `output`）にし、そろえば ok の結果の `outputs` に載せる（プランのステップが標準出力から読み、後の工程へ渡す）
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Optional

from ndf import cli, contracts
from ndf.contracts import result
from ndf.contracts.exit_codes import GATE, OK, STOPPED

# 結果 JSON の tool（このモジュールは `ndf worker run` の部品なので、サブコマンド `worker` の名前を使う）
TOOL = cli.tool_of("ndf.core.worker")
# 工程どうしで渡す値の形（結果 JSON の outputs）
OUTPUTS_SCHEMA = result.SCHEMA + "#/$defs/outputs"


def _gap_of(raw: object) -> dict[str, str]:
    """gaps の要素 1 つ。形の違う要素（kind・name・reason が空でない文字列でない）も捨てず、中身を理由にコピーした欠けにする。"""
    keys = ("kind", "name", "reason")
    if isinstance(raw, dict) and all(isinstance(raw.get(k), str) and raw[k] for k in keys):
        return {k: raw[k] for k in keys}
    shown = json.dumps(raw, ensure_ascii=False, default=str)
    return {"kind": "work", "name": "gap", "reason": f"形の違う gap（kind・name・reason を空でない文字列で書く）: {shown}"}


def gaps_of(report: Mapping[str, Any]) -> list[dict[str, str]]:
    """結果ファイルの gaps。形の違う gaps を黙って捨てると、作業を残した ok が完了に化けるので、欠けとして残す。"""
    raw = report.get("gaps")
    if raw is None or raw == []:
        return []
    return [_gap_of(g) for g in (raw if isinstance(raw, list) else [raw])]


def _output_gap(report: Mapping[str, Any], name: str) -> Optional[dict[str, str]]:
    """必須の名前 `name` の欠け。結果ファイルの `outputs` に無いか、値が outputs の形に合わなければ欠け、合えば None。"""
    have = outputs if isinstance(outputs := report.get("outputs"), dict) else {}
    if name not in have:
        reason = "作業の結果ファイルの outputs に無い"
    elif errs := contracts.schema_errors({name: have[name]}, OUTPUTS_SCHEMA):
        reason = f"作業の結果ファイルの outputs の値の形が違う: {'; '.join(errs)}"
    else:
        return None
    return {"kind": "output", "name": name, "reason": reason}


def require_outputs(report: Any, required: Sequence[str]) -> tuple[Any, Optional[dict[str, Any]]]:
    """ok の結果ファイルの `outputs` を `required`（`--require-output`）の名前で確かめ、（結果ファイル, 確かめた outputs）を返す（#3908）。

    欠け（無い・形の違う値）があれば欠けを `gaps` に足したコピーと None、無ければ `report` と確かめた名前の値だけの outputs。
    ok でないか `required` が空なら `report` と None。欠けの付いた ok は `outcome_of` が incomplete にする。
    確かめた outputs を結果ファイルの中へ戻さないのは、worker が同じキーを結果ファイルに書いて確かめを迂回できないためである。
    """
    if not (isinstance(report, dict) and report.get("status") == OK and required):
        return report, None
    missing = [g for name in required if (g := _output_gap(report, name))]
    if missing:
        return {**report, "gaps": gaps_of(report) + missing}, None
    return report, {name: report["outputs"][name] for name in required}


def outcome_of(
    kind: str,
    result_file: Path,
    report: Any,
    items: list[dict[str, Any]],
    metrics: dict[str, Any],
    *,
    outputs: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """worker が書いた結果ファイルの status をコピーした `worker run` の結果（#1682）。

    結果ファイルの status を読まずに ok を返すと、作業を残した worker の「判断が要る」「できなかった」が完了に
    化けるためである。gate（判断が要る）は承認ゲートの結果にし、stopped（できなかった）と欠け（gaps）の付いた ok は
    欠けを付けた incomplete にする。`report` は委譲の後に 1 回だけ読んで解いた結果ファイルで、無いか形が違えば bad_report。
    ok の結果には `outputs`（`require_outputs` が確かめた値）だけを載せる（結果ファイルの確かめていない値は渡さない）。
    起動の回数は `metrics` の `attempts`（`ndf worker run` が結果へ載せる値）から読む。
    """
    status = report.get("status") if isinstance(report, dict) else None
    if not isinstance(report, dict) or status not in (OK, GATE, STOPPED):
        return result.stopped(
            TOOL, "bad_report", f"作業 {kind} の結果ファイルを読めないか status が無い: {result_file}", items=items, metrics=metrics
        )
    said = told if isinstance(told := report.get("summary"), str) and told else "（要約なし）"
    gaps = gaps_of(report)
    if status == OK and not gaps:
        summary = f"作業 {kind} の worker が完了した（起動 {metrics['attempts']} 回）: {said}"
        return result.result(TOOL, OK, summary, items, metrics, outputs=outputs)
    if status == GATE:
        routes = given if isinstance(given := report.get("routes"), dict) and given else {"retry": None, "cancel": None}
        return result.result(TOOL, GATE, f"作業 {kind} の worker が判断を求めた: {said}", items, metrics, routes=routes)
    gaps = gaps or [{"kind": "work", "name": kind, "reason": said}]
    return result.stopped(TOOL, "incomplete", f"作業 {kind} の worker が作業を残した: {said}", items=items, metrics=metrics, gaps=gaps)
