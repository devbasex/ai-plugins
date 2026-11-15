"""本番の PR のマージの承認を決める（純粋な関数。#3572 の決定 3・4・8）。

承認の経路は 2 つで、どちらもリリース承認かつマージ承認である。

| 経路 | 承認の与え方 |
| --- | --- |
| `gate` | リリース承認ゲートの承認の答え（承認の証跡） |
| `github-merge` | 利用者が GitHub で本番の PR をマージした（マージした先端への承認） |

`decide` の判定:

| マージした先端への最新の承認の証跡 | 経路 | 承認した人 | 承認ゲートの承認の先端（`gate_head`） |
| --- | --- | --- | --- |
| 経路 `gate` | `gate` | 証跡の書き手 | マージした先端 |
| 経路 `github-merge` | `github-merge` | 証跡の「承認した人」 | 経路 `gate` の最新の承認の先端（無ければ None） |
| 無い（承認の後に先端が動き、人がその先端をマージした） | `github-merge` | マージした人 | 経路 `gate` の最新の承認の先端（無ければ None）。別の先端への承認を引き継がない |

止まったマージの次の手は `NEXT_MOVES` の 2 つで、既定の手（`suggested`）は止まった理由の種類で決まる（決定 3）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple

from ndf.contracts import causes
from ndf.domains.release.shapes import text_at

GATE_ROUTE, MERGE_ROUTE = "gate", "github-merge"
ROUTES = (GATE_ROUTE, MERGE_ROUTE)
# 止まったマージの次の手（打ち直し・差し戻し）
RERUN, REJECT = "rerun", "reject"
NEXT_MOVES = (RERUN, REJECT)


class MergeFacts(NamedTuple):
    """「Get a pull request」の応答から読んだマージの事実。`by` はマージした人のログイン（読めなければ空）。"""

    merged: bool
    by: str
    head: str
    merge_sha: str
    base_ref: str
    head_ref: str


class Approved(NamedTuple):
    """信用する書き手の承認の証跡 1 件。`order` はコメントの並び、`by` は証跡の「承認した人」（無ければ書き手）。"""

    order: int
    author: str
    sha: str
    route: str
    by: str


class Approval(NamedTuple):
    """マージした本番の PR の承認。"""

    route: str
    by: str
    approved_head: str
    merged_head: str
    merge_sha: str
    gate_head: str | None

    def as_json(self) -> dict[str, Any]:
        return dict(self._asdict())


def _ref(raw: Mapping[str, Any], side: str, key: str) -> str:
    part = raw[side] if side in raw else None
    return text_at(part, key) if isinstance(part, Mapping) else ""


def facts_of(raw: Mapping[str, Any]) -> MergeFacts:
    """PR の REST の応答（STD2）からマージの事実を読む。"""
    merged = raw["merged"] is True if "merged" in raw else False
    user = raw["merged_by"] if "merged_by" in raw else None
    by = text_at(user, "login") if isinstance(user, Mapping) else ""
    head, base_ref, head_ref = _ref(raw, "head", "sha"), _ref(raw, "base", "ref"), _ref(raw, "head", "ref")
    if not merged:
        return MergeFacts(False, "", head, "", base_ref, head_ref)
    return MergeFacts(True, by, head, text_at(raw, "merge_commit_sha"), base_ref, head_ref)


def _latest(found: Sequence[Approved]) -> Approved | None:
    return max(found, key=lambda a: a.order) if found else None


def decide(facts: MergeFacts, found: Sequence[Approved]) -> Approval | None:
    """マージ済みの PR の承認を決める（マージされていなければ None）。`found` は信用する承認の証跡（差し戻しを含まない）。"""
    if not facts.merged:
        return None
    gate = _latest([a for a in found if a.route == GATE_ROUTE])
    gate_head = gate.sha if gate is not None else None
    on_head = _latest([a for a in found if a.sha == facts.head])
    if on_head is not None and on_head.route == GATE_ROUTE:
        return Approval(GATE_ROUTE, on_head.author, facts.head, facts.head, facts.merge_sha, facts.head)
    by = on_head.by if on_head is not None else facts.by
    return Approval(MERGE_ROUTE, by, facts.head, facts.head, facts.merge_sha, gate_head)


def suggested(kind: str) -> str:
    """止まった理由の種類から既定の次の手（表の行き先が打ち直しの止まり方は打ち直し、ほかは差し戻し。決定 3）。"""
    found = causes.table()
    # 同じ承認・同じ先端のまま打ち直せば通りうる止まり方（ci_pending・infra_wait など）は、表の行き先が retry
    return RERUN if kind in found and found[kind].route == "retry" else REJECT
