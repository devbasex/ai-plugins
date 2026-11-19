"""棚卸しのテストが共有する、手順書（ranking.md）の形の見積の入力（`ndf issues rank --scores` の 1 件）。"""

from __future__ import annotations

from typing import Any


def estimate_row(n: int, ubv: int = 1, tc: int = 1, rr: int = 1, size: int = 1, digest: str | None = None, **extra: Any) -> dict[str, Any]:
    """見積の入力の 1 件。4 列の段階は既定で 1、digest は既定で `d<番号>`。"""

    def col(step: int) -> dict[str, Any]:
        return {"step": step, "why": f"#{n} の根拠"}

    return {"number": n, "digest": digest or f"d{n}", "ubv": col(ubv), "tc": col(tc), "rr_oe": col(rr), "size": col(size), **extra}
