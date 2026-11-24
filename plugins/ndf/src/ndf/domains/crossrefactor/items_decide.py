"""提案からリファクタリング計画の項目を組む（#622）。

- 提案は `{"path", "symbol", "title", "reason"}` の辞書で、提案した担当（`by`）を持つ
- 課題が触る関数（`symbols_decide.Touched`）を指さない提案は採らずに見送る。計画の担当が返した項目も同じ検証を通す
- 同じ関数への同じ趣旨の提案（本文の先頭が同じ）は 1 つにまとめ、提案した担当を並べる
- 項目の番号は `I-001` から振る。項目の状態は `open`（未着手）・`adopted`（採った）・`reverted`（戻した）・`deferred`（見送った）
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Any

from ndf.core.converge import oscillation
from ndf.domains.crossrefactor import symbols_decide

FIELDS = ("path", "symbol", "title")
# 計画の項目が持つ欄（起動指示で求めた欄と、提案をそのまま項目にするときの担当）。計画の担当が足したほかの欄は持たない
# （`ndf refactor state` が項目を結果の items へそのまま出すため、結果のコントラクトの欄と名前が重なりうる）
PLANNED = (*FIELDS, "reason", "by", "tier", "technique", "tests")


class Status(StrEnum):
    OPEN = "open"
    ADOPTED = "adopted"
    REVERTED = "reverted"
    DEFERRED = "deferred"


def _complete(p: Mapping[str, Any]) -> bool:
    return all(isinstance(p.get(f), str) and p[f] for f in FIELDS)


def _reason(p: Mapping[str, Any]) -> str:
    return p["reason"] if isinstance(p.get("reason"), str) else ""


def _key(p: Mapping[str, Any]) -> tuple[str, str, str]:
    return (str(p["path"]), str(p["symbol"]), oscillation.finding_key(str(p["title"])))


def merge_proposals(by_runtime: Mapping[str, Iterable[Mapping[str, Any]]]) -> tuple[list[dict[str, Any]], int]:
    """担当ごとの提案を 1 つの並びへまとめる。返すのは (まとめた提案, 形の違う提案の数)。"""
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    broken = 0
    for runtime, proposals in by_runtime.items():
        for p in proposals:
            if not _complete(p):
                broken += 1
                continue
            got = merged.setdefault(_key(p), {**{f: str(p[f]) for f in FIELDS}, "reason": _reason(p), "by": []})
            got["by"].append(runtime)
    return list(merged.values()), broken


def plan_items(planned: Iterable[Mapping[str, Any]], touched: symbols_decide.Touched) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """計画の項目を、触る関数を指すもの（番号と状態を振る）と範囲外で見送るものへ分ける。"""
    complete = [{k: p[k] for k in PLANNED if k in p} for p in planned if _complete(p)]
    kept, skipped = symbols_decide.split_by_touched(complete, touched)
    items = [{**p, "id": f"I-{n:03d}", "status": Status.OPEN.value} for n, p in enumerate(kept, start=1)]
    return items, [{**p, "reason": why} for p, why in skipped]


def against_mvv(answer: Mapping[str, Any] | None, ids: Iterable[str]) -> dict[str, str] | None:
    """今の MVV での確かめの回答（`prompt_decide.mvv_check`）から、MVV に反する項目の番号 → 理由。

    回答が無いか、`ids` のどれかの判定が欠けていれば None。
    """
    rows = answer["items"] if answer is not None and isinstance(answer.get("items"), list) else []
    said = {r["item"]: r for r in rows if isinstance(r, Mapping) and isinstance(r.get("item"), str) and isinstance(r.get("keep"), bool)}
    wanted = list(ids)
    if any(i not in said for i in wanted):
        return None
    return {i: _reason(said[i]) for i in wanted if not said[i]["keep"]}


def next_open(items: Iterable[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """未着手の最初の項目。無ければ None。"""
    return next((i for i in items if i["status"] == Status.OPEN.value), None)


def counts(items: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """状態ごとの項目の数（metrics に出す）。"""
    out = {s.value: 0 for s in Status}
    for i in items:
        out[str(i["status"])] += 1
    return out
