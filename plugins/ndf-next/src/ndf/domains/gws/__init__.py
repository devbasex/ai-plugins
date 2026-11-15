"""`ndf gws`: gws（Google Workspace の CLI）の確認と Google Slides のサムネイルの保存（#3547）。

2 つの流れ（`check`・`thumbnails`）は結果の辞書と終了コードの組（`Outcome`）を返すか、止まる理由（`GwsStop`）を投げる。
結果 JSON を出すのは受付（`cli`）だけである。認証済みかの判定は records の gws の部品（`ndf.domains.records.gws`）が持つ。
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from typing import Any, Optional

from ndf.contracts import exit_codes, limits, result
from ndf.domains.gws import report_decide as rd

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


def login_request(retry: str) -> str:
    """利用者に `gws auth login` を頼む案内の文（Claude Code とほかのランタイムでのログインの打ち方）。`retry` は終えた後に打ち直すもの。"""
    return f"利用者に `gws auth login` を頼み（Claude Code では `! gws auth login`、ほかのランタイムでは別の端末で打つ）、終えたら {retry} を打ち直す"


def unauthenticated_gate(
    tool: str,
    summary: str,
    retry_argv: Sequence[str],
    *,
    metrics: Optional[dict[str, Any]] = None,
    items: Optional[list[dict[str, str]]] = None,
) -> Outcome:
    """gws が未認証の承認ゲートの結果（state・routes の retry と cancel）と終了コードを組む。`retry_argv` は `ndf` の後ろの引数。"""
    routes = {"retry": result.command(*retry_argv), "cancel": None}
    shown: dict[str, Any] = {"state": rd.UNAUTHENTICATED}
    if metrics is not None:
        shown.update(metrics)
    out = result.result(tool, result.GATE, summary, items=items, metrics=shown, routes=routes)
    return out, unauthenticated_code()


def call(cmd: Sequence[str], limit_name: str, what: str, *, summary: Optional[str] = None) -> subprocess.CompletedProcess[str]:
    """外部コマンドを上限 `limit_name` の秒数で 1 回打つ。上限を超えたら interrupted、起動できなければ unknown の `GwsStop`。"""
    try:
        return subprocess.run(list(cmd), capture_output=True, text=True, timeout=limits.seconds(limit_name), check=False)
    except subprocess.TimeoutExpired as exc:
        raise GwsStop("interrupted", f"{what} が上限の {exc.timeout} 秒を超えた", summary=summary) from exc
    except OSError as exc:
        raise GwsStop("unknown", f"{what} を起動できない: {exc}", summary=summary) from exc
