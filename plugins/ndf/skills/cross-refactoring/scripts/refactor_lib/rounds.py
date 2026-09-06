"""ラウンドの種類と、種類ごとに変わる項目の扱い。

**階層はすべて「ラウンド」で表す**（#436）。テスト整備ラウンドと提案ラウンドは
同じ形（提案 → 採否 → 適用ラウンド → 検証 → 修正ラウンド）を持ち、**適用ラウンドと
修正ラウンドを共有する**。違うのは集める提案の中身だけである。

種類で変わるのは 3 つ（重複排除と対象外の鍵・外へ出す文章での指し方・見送りの
記録の形）だけなので、ここへ集める。工程の側は種類で分岐しない。
"""
from __future__ import annotations

from typing import Any

# ラウンドの種類。**宣言の無い状態ファイルは構造改善として読む**（この版より前で
# 始めた実行を、再開の時点でテスト整備へ戻さないため）。
TEST = "test"
STRUCTURE = "structure"


def round_kind(state: dict[str, Any]) -> str:
    """次に開くラウンドの種類。"""
    return TEST if state.get("round_kind") == TEST else STRUCTURE


def entry_kind(entry: dict[str, Any]) -> str:
    """記録済みのラウンド 1 件の種類。"""
    return TEST if entry.get("kind") == TEST else STRUCTURE


def item_kind(item: dict[str, Any]) -> str:
    """項目 1 件の種類。改善項目とテスト項目は同じ一覧に並ぶ。"""
    return TEST if item.get("kind") == TEST else STRUCTURE


def item_key(item: dict[str, Any]) -> tuple[str, ...]:
    """重複排除と対象外の判定に使う鍵（決定 9 / 決定 10）。

    改善項目は `path` + `symbol` + `smell`、**テスト項目は `target` + `case`**
    である。`level` は鍵に入れない（同じ経路を別の階層で 2 度固定させないため）。
    """
    if item_kind(item) == TEST:
        return (str(item.get("target") or ""), str(item.get("case") or ""))
    return (
        str(item.get("path") or ""),
        str(item.get("symbol") or ""),
        str(item.get("smell") or ""),
    )


def item_label(item: dict[str, Any]) -> str:
    """外へ出す文章で項目を指す名前。**内部の識別子だけで書かない。**

    どちらの種類も `<ファイル>#<シンボル>` になる。テスト項目の `path` は
    テストを足す先なので、指す対象は `target`（固定する入口）である。
    """
    if item_kind(item) == TEST:
        return str(item.get("target") or item.get("path") or "?")
    return f"{item.get('path')}#{item.get('symbol')}"


def deferred_record(
    item: dict[str, Any], item_id: str, reason: str
) -> dict[str, Any]:
    """見送り（対象外）の記録。**鍵に要る項目を種類ごとに残す。**

    残さないと、同じ提案が次のラウンドで再び採用され、同じ理由で失敗する。
    """
    record: dict[str, Any] = {
        "item_id": item_id,
        "kind": item_kind(item),
        "path": item.get("path"),
        "round": item.get("round"),
        "defer_reason": reason,
    }
    if item_kind(item) == TEST:
        record.update({"target": item.get("target"), "case": item.get("case")})
    else:
        record.update({"symbol": item.get("symbol"), "smell": item.get("smell")})
    return record
