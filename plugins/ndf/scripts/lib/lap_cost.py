"""ループの 1 回の費用: 使用量の帳簿の和とスプリントの報告の和の大きい方をループの 1 回の費用にする（#2505 の決定 11）。

帳簿の和は Claude の行のうち、ループの 1 回のプランを指す行の和で、cross-review・MVV 判定を含む。報告の和を残すのは、Factory の実行ジョブの
再開で帳簿の行が欠けるためである（帳簿はホームの下にあり、報告は `.ndf/` の下で復元される）。Codex と Kiro は USD を持たないので
和に足さず、内訳に呼び出しの数とトークンを残す。USD は定価の見積もりで請求額ではない。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import usage_aggregate
import usage_ledger

SUPERVISE = "supervise"


def _lap_rows(prefix: str, runtime: str, directory: Optional[Path]) -> list[dict]:
    return [r for r in usage_ledger.read_usage_ledger(runtime, directory) if str(r.get("plan") or "").startswith(prefix)]


def _unpriced(prefix: str, directory: Optional[Path]) -> dict:
    """USD を持たない起動の呼び出しの数とトークン。"""
    out = {}
    for runtime in usage_ledger.RUNTIMES:
        if runtime == "claude":
            continue
        b = usage_aggregate.bucket()
        for r in _lap_rows(prefix, runtime, directory):
            usage_aggregate.count_row(b, r)
        if b["calls"]:
            tokens = b["usage"].get("total_tokens") or b["usage"].get("input_tokens", 0) + b["usage"].get("output_tokens", 0)
            out[runtime] = {"calls": b["calls"], **({"tokens": tokens} if tokens else {})}
    return out


def lap_cost(lap: dict, reports: float, directory: Optional[Path] = None) -> tuple[float, dict]:
    """(ループの 1 回の費用, ループの 1 回の費用の内訳)。`reports` はスプリントの報告の和。帳簿を読めなければ報告の和を使い、ループの 1 回の締めを止めない。"""
    parts: dict = {"source": "reports", "plans": 0.0, "others": 0.0, "unpriced": {}}
    # 帳簿の行はプランのパスで引く。工程の進行表で流したループの 1 回（#3092）はプランをループの 1 回のディレクトリの下に置く
    base = Path(lap["out"]).parent if lap.get("out") else (Path(lap["dir"]) if lap.get("dir") else None)
    if base is None:
        return reports, parts
    prefix = f"{base}/"
    try:
        rows = [r for r in _lap_rows(prefix, "claude", directory) if isinstance(r.get("cost_usd"), (int, float))]
        parts["unpriced"] = _unpriced(prefix, directory)
    except (OSError, ValueError) as e:
        parts["unreadable"] = str(e)[:200]
        return reports, parts
    parts["plans"] = round(sum(r["cost_usd"] for r in rows if r.get("source") == SUPERVISE), 6)
    parts["others"] = round(sum(r["cost_usd"] for r in rows if r.get("source") != SUPERVISE), 6)
    ledger = round(parts["plans"] + parts["others"], 6)
    if ledger >= reports:
        parts["source"] = "ledger"
    return max(ledger, reports), parts
