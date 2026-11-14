"""選択の記録（行き先を選ぶたびに進行表へ残す事象 `route_chosen`）の組み立てと確かめと、選んだ主体の決め方。

形は `contracts/schemas/route-chosen.schema.json` にある。書き手（プランの実行・進行表）はここで組み、
測る側は記録をこの形でだけ読む（ndf-next のモジュールを import しない）。
選んだ主体は、承認ゲートの答えなら人、結果の `next` のとおりに打った選択ならスクリプト、ほかは conductor である。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any, Optional

from ndf import contracts
from ndf.contracts import causes

SCHEMA = contracts.schema_path("route-chosen")
EVENT = "route_chosen"
# 選んだ主体の語彙（スキーマの chooser の enum と同じ。テストが照らす）
SCRIPT, CONDUCTOR, HUMAN = "script", "conductor", "human"
CHOOSERS = (SCRIPT, CONDUCTOR, HUMAN)


def _follows(suggested: dict[str, Any], taken: Sequence[str]) -> bool:
    """打った引数が `next` の argv で始まり、残りが ask の鍵の `--<鍵> <値>` の組だけか。"""
    argv = suggested["argv"]
    if list(taken[: len(argv)]) != list(argv):
        return False
    rest = list(taken[len(argv) :])
    asked = {f"--{key}" for key in (suggested["ask"] if "ask" in suggested else ())}
    flags = rest[0::2]
    return len(rest) % 2 == 0 and len(flags) == len(set(flags)) and set(flags) <= asked


def chooser(suggested: Optional[dict[str, Any]], taken: Sequence[str], *, answered_by_human: bool = False) -> str:
    """選んだ主体。`suggested` は結果の `next`、`taken` は実際に打った引数、`answered_by_human` は承認ゲートの人の答えか。"""
    if answered_by_human:
        return HUMAN
    if suggested is not None and _follows(suggested, taken):
        return SCRIPT
    return CONDUCTOR


def errors(obj: Any) -> list[str]:
    """選択の記録の誤りを並べる。空なら正しい。形はスキーマ、cause の有無は止まった理由の表と照らす。"""
    errs = contracts.schema_errors(obj, SCHEMA)
    if isinstance(obj, dict) and isinstance(obj.get("cause"), str):
        errs += causes.unknown_kind_errors(obj["cause"], "cause")
    return errs


def now() -> str:
    """記録の時刻（UTC の ISO 8601。秒まで）。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record(
    run_id: str,
    rule: str,
    chooser: str,
    frm: str,
    to: str,
    cause: Optional[str] = None,
) -> dict[str, Any]:
    """選択の記録を今の時刻で 1 件組む。形に合わなければ `ValueError`。"""
    out: dict[str, Any] = {"event": EVENT, "at": now(), "run_id": run_id, "rule": rule, "chooser": chooser, "from": frm, "to": to}
    if cause is not None:
        out["cause"] = cause
    bad = errors(out)
    if bad:
        raise ValueError("; ".join(bad))
    return out
