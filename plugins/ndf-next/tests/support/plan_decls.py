"""計画とレーンの設定を組み立てるテストの共通部品（`ndf.progress.lane`・`ndf.progress.plan` のテストが使う）。"""

from __future__ import annotations

from typing import Any

REPO = "https://example.invalid/o/r"


def decl(name: str, rows: Any, touches: Any = ("src/a",), after: Any = (), plans: int = 1) -> dict[str, Any]:
    """レーンの設定。`rows` は行ごとの課題の番号の並び。"""
    return {
        "name": name,
        "plans": plans,
        "touches": list(touches),
        "after": list(after),
        "rows": [{"kind": "sprint", "issues": list(i)} for i in rows],
    }


def doc(*lanes: dict[str, Any], rules: Any = (), **extra: Any) -> dict[str, Any]:
    """計画の設定（名前は p6）。`extra` は max_lanes・max_plans など。"""
    return {"version": 1, "name": "p6", "rules": list(rules), "lanes": list(lanes), **extra}
