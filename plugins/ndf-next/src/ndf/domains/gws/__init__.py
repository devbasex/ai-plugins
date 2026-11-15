"""`ndf gws`: gws（Google Workspace の CLI）の確認と Google Slides のサムネイルの保存（#3547）。

2 つの流れ（`check`・`thumbnails`）は結果の辞書と終了コードの組（`Outcome`）を返すか、止まる理由（`GwsStop`）を投げる。
結果 JSON を出すのは受付（`cli`）だけである。認証済みかの判定は records の gws の部品（`ndf.domains.records.gws`）が持つ。
"""

from __future__ import annotations

from typing import Any, Optional

from ndf.contracts import exit_codes

# 結果の辞書と終了コードの組
Outcome = tuple[dict[str, Any], int]


class GwsStop(Exception):
    """流れが止まる理由（止まった理由の種類・説明・利用者への案内・項目・計測値）。受付が `stopped` の結果 JSON にする。"""

    def __init__(
        self,
        kind: str,
        detail: str,
        *,
        summary: Optional[str] = None,
        items: Optional[list[dict[str, str]]] = None,
        metrics: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(detail)
        self.kind = kind
        self.detail = detail
        self.summary = summary
        self.items = items
        self.metrics = metrics


def unauthenticated_code() -> int:
    """gws が未認証の承認ゲートの終了コード（承認ゲートの最初の値の次。導入の同意の承認ゲートと分ける）。"""
    return exit_codes.first_code(exit_codes.GATE) + 1
