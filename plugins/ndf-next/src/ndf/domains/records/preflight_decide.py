"""手順 0 の 3 つの確かめ（worktree の設定・宣言・MVV）を 1 回の結果にまとめる判断（純粋な関数。SF10 #3208）。

3 つを順に見て、どれも通れば `ok`。止まった確かめがあれば、先頭のその結果の `cause` と、設定を用意する
コマンドの `next` を返す。後の確かめも打ち、`items` に 1 件ずつ並べる（1 回で欠けをすべて見せる）。
確かめを工程として呼ぶ口は `ndf.progress.invoke` が持ち、ここはまとめるだけである。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Optional

from ndf.contracts.result import STOPPED

# 確かめの名前（結果の items の name）
WORKTREE, DECLARATION, MVV = "worktree-setup", "project-declaration", "project-mvv"
ORDER = (WORKTREE, DECLARATION, MVV)


@dataclass(frozen=True)
class Checked:
    """1 つの確かめの結果。`result` は結果 JSON、`next` は止まったときに設定を用意するコマンド（無ければ None）。"""

    name: str
    result: Mapping[str, Any]
    next: Optional[dict[str, Any]] = None

    @property
    def passed(self) -> bool:
        return self.result.get("status") != STOPPED


@dataclass(frozen=True)
class Summary:
    """まとめた結果。`cause` と `next` は先頭の止まった確かめのもの（全部通れば None）。"""

    passed: bool
    items: list[dict[str, Any]]
    cause: Optional[Mapping[str, Any]]
    next: Optional[dict[str, Any]]
    summary: str


def _item(c: Checked) -> dict[str, Any]:
    item: dict[str, Any] = {"name": c.name, "status": c.result.get("status"), "summary": c.result.get("summary")}
    if c.result.get("warnings"):
        item["warnings"] = list(c.result["warnings"])
    if not c.passed and "cause" in c.result:
        item["cause"] = c.result["cause"]
    return item


def combine(checks: Sequence[Checked]) -> Summary:
    """確かめの結果をまとめる。名前が `ORDER` の順でなければ `ValueError`（3 つを順に打つ約束）。"""
    if tuple(c.name for c in checks) != ORDER:
        raise ValueError(f"確かめの順が違う: {[c.name for c in checks]}（{list(ORDER)} の順）")
    items = [_item(c) for c in checks]
    failed = [c for c in checks if not c.passed]
    if not failed:
        return Summary(True, items, None, None, "手順 0 の確かめ（worktree の設定・宣言・MVV）がすべて通った")
    first = failed[0]
    cause = first.result["cause"] if "cause" in first.result else None
    names = "、".join(c.name for c in failed)
    return Summary(False, items, cause, first.next, f"手順 0 の確かめが止まった: {names}")


def worktree_checked(result: Mapping[str, Any], init: dict[str, Any], absent_cause: dict[str, Any]) -> Checked:
    """`worktree check` の結果を確かめにする。宣言が無い（`metrics.present` が偽）なら止まりとして `init` を next にする。"""
    metrics = result.get("metrics")
    present = isinstance(metrics, Mapping) and metrics.get("present") is True
    if result.get("status") == STOPPED:
        return Checked(WORKTREE, result, init)
    if not present:
        stopped = {**result, "status": STOPPED, "cause": absent_cause}
        return Checked(WORKTREE, stopped, init)
    return Checked(WORKTREE, result)
