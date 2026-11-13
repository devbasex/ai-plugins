"""結果 JSON の組み立て・形の確かめ・出力。

副命令と作業は結果を標準出力へ 1 行の JSON で出し、終了コードで終える。読み手は `status` と `cause.kind`
（と終了コード）だけで次の手を決め、`summary` や Markdown の文から判定しない。
形は `contracts/schemas/result.schema.json`、`stopped` の終了コードは止まった理由の表の種類から引く。
"""

from __future__ import annotations

import json
import sys
from typing import Any, Optional, TextIO

from ndf import contracts
from ndf.contracts import causes, exit_codes

SCHEMA = contracts.schema_path("result")
# 結果の status の語彙（result.schema.json の enum と事実の表 exit-codes の status 列と同じ）
OK, GATE, STOPPED = "ok", "gate", "stopped"
STATUSES = (OK, GATE, STOPPED)


def result(
    tool: str,
    status: str,
    summary: str,
    items: Optional[list] = None,
    metrics: Optional[dict] = None,
    *,
    cause: Optional[dict] = None,
    routes: Optional[dict] = None,
    gaps: Optional[list] = None,
    process: Optional[list] = None,
    decisions: Optional[list] = None,
    warnings: Optional[list] = None,
    presentation_path: Optional[str] = None,
    next: Optional[str] = None,
) -> dict[str, Any]:
    """結果を組む。任意の項目は渡したときだけ置く（空の配列は置かない）。形は `errors` が確かめる。"""
    out: dict[str, Any] = {"tool": tool, "status": status, "summary": summary, "items": list(items or []), "metrics": dict(metrics or {})}
    for key, val in (("gaps", gaps), ("process", process), ("decisions", decisions), ("warnings", warnings)):
        if val:
            out[key] = list(val)
    for key, val in (("cause", cause), ("routes", routes), ("presentation_path", presentation_path), ("next", next)):
        if val:
            out[key] = val
    return out


def stopped(
    tool: str,
    kind: str,
    detail: str,
    *,
    summary: Optional[str] = None,
    instance: Optional[str] = None,
    retry_after: Optional[float] = None,
    **extra: Any,
) -> dict[str, Any]:
    """`stopped` の結果を種類と説明から組む。`summary` を省けば説明を使う。

    `instance` と `retry_after` は `cause` へ、残りのキーワード（`extra`）は `result` へ渡す。
    """
    c = causes.cause(kind, detail, instance=instance, retry_after=retry_after)
    return result(tool, STOPPED, summary if summary is not None else detail, cause=c, **extra)


def code_matches(status: str, code: int) -> bool:
    """status と終了コードが事実の表 exit-codes の区分で食い違わないか。"""
    return exit_codes.matches(status, code)


def code_for(obj: Any) -> Optional[int]:
    """既定の終了コード。ok は 0、gate は承認ゲートの最初の値、stopped は cause.kind の終了コード。決められなければ None。"""
    if not isinstance(obj, dict):
        return None
    status = obj.get("status")
    if status in (OK, GATE):
        return exit_codes.first_code(status)
    if status == STOPPED:
        c = obj.get("cause")
        kind = c.get("kind") if isinstance(c, dict) else None
        if isinstance(kind, str) and kind in causes.table():
            return causes.exit_code(kind)
    return None


def errors(obj: Any, code: Optional[int] = None) -> list[str]:
    """結果の形の誤りを並べる。空なら正しい。`code` を渡すと status と種類との対応も確かめる。"""
    errs = contracts.schema_errors(obj, SCHEMA)
    if not isinstance(obj, dict):
        return errs
    if "cause" in obj:
        # cause の形はスキーマ（$defs/cause）が上で確かめた。表と照らす種類の有無だけを足す
        errs += causes.kind_errors(obj["cause"])
    if code is not None and obj.get("status") in STATUSES:
        if not code_matches(obj["status"], code):
            errs.append(f"status {obj['status']} と終了コード {code} が合わない")
        expected = code_for(obj)
        if obj["status"] == STOPPED and expected is not None and code != expected:
            errs.append(f"cause.kind {obj['cause']['kind']} の終了コードは {expected}（渡された値 {code}）")
    return errs


def emit(obj: dict[str, Any], code: Optional[int] = None, stream: Optional[TextIO] = None) -> int:
    """結果を確かめて 1 行で出し、終了コードを返す。形が違えば出さずに誤りを標準エラーへ書き、invalid の終了コードを返す。"""
    if code is None:
        code = code_for(obj)
    errs = errors(obj, code)
    if errs or code is None:
        for line in errs or ["終了コードを決められない（stopped に cause が無い）"]:
            print(f"結果の形の誤り: {line}", file=sys.stderr)
        return causes.exit_code("invalid")
    print(json.dumps(obj, ensure_ascii=False), file=stream or sys.stdout)
    return code
