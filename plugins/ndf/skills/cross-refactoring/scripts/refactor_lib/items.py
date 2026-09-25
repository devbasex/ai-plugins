"""改善項目と候補の鍵・表示・見送りの記録（#933 で `rounds.py` から移した）。

ラウンドと群が無くなり、残ったのは項目そのものの扱いだけである。項目は改修計画が採った
改善項目（`state["items"]`）、候補は提案を統合したもの（`state["candidates"]`）で、
どちらも `path` + `symbol` + `smell` を鍵に持つ。
"""
from __future__ import annotations

from typing import Any, Optional

from . import die

# 項目の状態（設計の「項目の状態遷移」）。
PLANNED = "planned"
TESTED = "tested"
IMPLEMENTED = "implemented"
FAILING = "failing"
VERIFIED = "verified"
REVERTED = "reverted"
DEFERRED = "deferred"     # 締め切り・足したテストの失敗で見送った（取り消しと別に数える）

# 取り消しの対象になりうる（コミットを持ちうる）状態。
LIVE = (PLANNED, TESTED, IMPLEMENTED, FAILING, VERIFIED)


def item_key(item: dict[str, Any]) -> tuple[str, str, str]:
    """重複排除と見送りの判定に使う鍵。`path` + `symbol` + `smell`。"""
    return (
        str(item.get("path") or ""),
        str(item.get("symbol") or ""),
        str(item.get("smell") or ""),
    )


def key_text(item: dict[str, Any]) -> str:
    """鍵を 1 語で表す。実装担当の改修計画の結果が項目を指すときに使う（`path#symbol#smell`）。"""
    return "#".join(item_key(item))


def group_key(item: dict[str, Any]) -> tuple[str, str]:
    """候補の切り出しの単位（決定 17）。`path` + `symbol` の組。"""
    return str(item.get("path") or ""), str(item.get("symbol") or "")


def item_label(item: Optional[dict[str, Any]]) -> str:
    """外へ出す文章で項目を指す名前。**内部の識別子だけで書かない。**"""
    if not item:
        return "?"
    return f"{item.get('path')}#{item.get('symbol')}"


def item_kind(item: dict[str, Any]) -> str:
    """配分テーブルの種類（決定 6）。改善項目は `structure/<technique>`。"""
    return f"structure/{item.get('technique') or 'unknown'}"


def deferred_record(item: dict[str, Any], reason: str, detail: str = "") -> dict[str, Any]:
    """見送りの記録。**理由は `vocabulary.DEFER_REASONS` の 8 つに限る**（AC9）。

    鍵に要る項目を残すのは、報告と改修計画が `<ファイル>#<シンボル>` で指すためである。
    """
    record: dict[str, Any] = {
        "path": item.get("path"),
        "symbol": item.get("symbol"),
        "smell": item.get("smell"),
        "technique": item.get("technique"),
        "severity": item.get("severity"),
        "proposed_by": list(item.get("proposed_by") or []),
        "defer_reason": reason,
    }
    if item.get("id"):
        record["item_id"] = item["id"]
    if detail:
        record["detail"] = detail
    return record


def defer(state: dict[str, Any], item: dict[str, Any], reason: str, detail: str = "") -> None:
    """見送りへ 1 件足す。**同じ項目 ID を二重に書かない**（取り込みの叩き直しに備える）。"""
    deferred = state.setdefault("deferred_items", [])
    item_id = item.get("id")
    if item_id and any(d.get("item_id") == item_id for d in deferred):
        return
    deferred.append(deferred_record(item, reason, detail))


def find_item(
    state: dict[str, Any], item_id: Optional[str], required: bool = True
) -> Any:
    """改修計画が採った項目を ID で引く。"""
    for item in state.get("items") or []:
        if item.get("id") == item_id:
            return item
    if required:
        die(f"改善項目 {item_id} がありません")
    return None


def item_shas(item: dict[str, Any]) -> list[str]:
    """項目が持つコミット（テスト・実装・修正）を古い順に返す。"""
    commits = item.get("commits") or {}
    shas = [commits.get("test"), commits.get("implement"), *(commits.get("fix") or [])]
    return [s for s in shas if isinstance(s, str) and s]


def live_items(state: dict[str, Any]) -> list[dict[str, Any]]:
    """取り消されていない項目。"""
    return [i for i in state.get("items") or [] if i.get("status") in LIVE]
