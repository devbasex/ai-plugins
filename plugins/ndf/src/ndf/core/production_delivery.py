"""リリース記録に残す本番の届け先の 1 行を選び、工程の文脈の形に合うかを確かめる部品（#4513）。

届け先の行の選び方の条件は `production_merge.on_production_channel` だけに置き、ここはそれを使う。文脈の形の確かめは
`ndf.contracts`（jsonschema を読む）を使うので、標準ライブラリだけで動く hook（`domains.hooks`）が読む
`production_merge` から分けて置く。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional

from ndf import contracts
from ndf.contracts import internal
from ndf.contracts.worker_kinds import DELIVERY
from ndf.core.production_merge import DELIVERIES, on_production_channel


def fits_context(row: Mapping[str, Any]) -> bool:
    """届け先の行が工程の文脈の `delivery` の形に合うか。

    文脈のスキーマ全体へ `{"delivery": 行}` を渡して確かめる（`$defs/delivery` だけを引くと、`branch` の参照が
    ルートの `properties` を指すので有効な行が参照の誤りで落ちる）。
    """
    return not contracts.schema_errors({DELIVERY: dict(row)}, internal.CONTEXT)


def production_delivery(section: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    """ブランチ戦略の設定 `section` の `release.deliveries` のうち、本番に届く行が 1 つで工程の文脈の `delivery` の形に合えば
    その行。ほかは None。"""
    release = section.get("release")
    rows = release.get(DELIVERIES) if isinstance(release, Mapping) else None
    found = [r for r in rows if on_production_channel(r)] if isinstance(rows, list) else []
    if len(found) != 1:
        return None
    row = dict(found[0])
    return row if fits_context(row) else None
