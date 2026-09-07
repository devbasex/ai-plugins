"""Pull Request へ出す文章の規約（#436 決定 6-b と「外へ出す文章」の節）。

**読み手は GitHub 上にいる。** 状態ファイルも内部の識別子も見えない。

| 規約 | 内容 |
| --- | --- |
| 項目の指し方 | **`<ファイル>#<シンボル>` を併記する**（`rounds.item_label`）。内部の識別子だけで書かない |
| 取り消した項目 | **内訳を書かない。** 件数だけ述べ、内訳は改修計画へ譲る |
| 改修計画の参照 | **生の URL を必ず書く。** Markdown のリンクにしない（利用者の画面で URL を取り出せない） |

対象は Pull Request へ出す**すべての**文章である。実装では検証の結果
（`verify-round`）・修正の要約（`merge-fix`）・進行の報告（`report`）の 3 つが
これにあたる。
"""
from __future__ import annotations

from typing import Any

from .plan import PLAN_COMMENT, PLAN_FILE, plan_mode
from .rounds import item_label


def plan_reference(state: dict[str, Any]) -> str:
    """改修計画の参照。**コメントなら生の URL をそのまま返す。**

    Markdown のリンク記法にしない。読み手が URL を取り出せなくなる。
    """
    mode = plan_mode(state)
    if mode == PLAN_COMMENT:
        url = str((state.get("plan_comment") or {}).get("url") or "")
        return url or "（改修計画のコメントをまだ作成できていません）"
    if mode == PLAN_FILE:
        return str(state.get("plan_file") or "")
    return "（この実行では改修計画を記録していません）"


def plan_line(state: dict[str, Any]) -> str:
    """外へ出す文章へ 1 行で添える改修計画の参照。"""
    return f"改修計画: {plan_reference(state)}"


def dropped_line(state: dict[str, Any], count: int) -> str:
    """取り消しの件数だけを述べる 1 行。**内訳は書かない。**

    内訳を持つのは改修計画だけである。2 か所に置くと片方だけが古くなる。
    """
    return f"取り消し {count} 件（内訳は改修計画にある）。{plan_line(state)}"


def item_lines(state: dict[str, Any], item_ids: list[str]) -> list[str]:
    """項目を外へ出す形で並べる。**`<ファイル>#<シンボル>` を併記する。**"""
    by_id = {i.get("item_id"): i for i in state.get("items") or []}
    lines = []
    for item_id in item_ids:
        item = by_id.get(item_id)
        lines.append(
            f"{item_id} `{item_label(item)}`" if item else str(item_id)
        )
    return lines
