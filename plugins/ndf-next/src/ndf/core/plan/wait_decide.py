"""待ちの結果の判断（純粋な関数。SF7）。

`ndf plan wait` は流れているプランを見回り、人の手が要るもの（止まった・承認ゲート）を種類つきで返す。
結果の各行は止まりなら `cause`、承認ゲートなら `gate: true` と `routes` を持ち、`next` には続きの待ちを打つコマンドを
載せる。読み手は文でなく `status`・`cause.kind`・`next` で次の手を決める。
`routes` と `next` の値はそのまま打てるコマンド（`result.command` の形）である。結果の `routes` の鍵は答えの語彙だけを持つため、
承認ゲートが 2 本以上あれば結果の `routes` は先頭の 1 本の分にし、各行の `routes` にそれぞれの分を残す。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple, Optional

from ndf.contracts import causes, result
from ndf.contracts.exit_codes import GATE, OK, STOPPED


class Watched(NamedTuple):
    """見回った 1 本。`status` は終わっていなければ None。`routes` は承認ゲートの答えごとのコマンド。"""

    name: str
    status: Optional[str]
    kind: Optional[str] = None
    detail: Optional[str] = None
    routes: Optional[Mapping[str, Optional[dict[str, Any]]]] = None


def _item(w: Watched) -> dict[str, Any]:
    item: dict[str, Any] = {"kind": "plan", "name": w.name, "status": w.status}
    if w.status == STOPPED:
        kind = w.kind if w.kind is not None else "unknown"
        item["cause"] = causes.cause(kind, w.detail if w.detail else f"{w.name} が止まった", instance=w.name)
    if w.status == GATE:
        item["gate"] = True
        item["routes"] = dict(w.routes) if w.routes else {}
    return item


def settled(found: Sequence[Watched]) -> bool:
    """待ちを終えてよいか（すべてが終わったか、人の手が要るものがあるか）。"""
    return all(w.status is not None for w in found) or any(w.status in (GATE, STOPPED) for w in found)


def gate_routes(head_routes: Optional[Mapping[str, Any]], again: dict[str, Any]) -> dict[str, Any]:
    """承認ゲートの結果の routes。routes を持つ先頭の 1 本の分にする。

    答えを持たない待ち（承認ゲート・流していないプラン）は、答えの語彙の「同じ作業を打ち直す」で打ち直す。
    """
    return dict(head_routes) if head_routes else {"retry": again}


def attention(tool: str, found: Sequence[Watched], again: dict[str, Any]) -> dict[str, Any]:
    """見回りの結果。止まりがあれば `stopped`（先頭の止まりの種類）、承認ゲートがあれば `gate`、すべて終われば `ok`。"""
    items = [_item(w) for w in found]
    stops = [w for w in found if w.status == STOPPED]
    gates = [w for w in found if w.status == GATE]
    metrics = {"plans": len(found), "stopped": len(stops), "gates": len(gates)}
    if stops:
        first = items[found.index(stops[0])]["cause"]
        summary = f"{len(stops)} 本のプランが止まった（先頭: {stops[0].name}）"
        return result.result(tool, STOPPED, summary, items, metrics, cause=first, next=again)
    if gates:
        head = next((w for w in gates if w.routes), gates[0])
        routes = gate_routes(head.routes, again)
        summary = f"{len(gates)} 本のプランが承認ゲートで待っている（routes は {head.name} の分）"
        return result.result(tool, GATE, summary, items, metrics, routes=routes, next=again)
    return result.result(tool, OK, f"{len(found)} 本のプランが終わった", items, metrics)


def timed_out(tool: str, found: Sequence[Watched], waited: float, again: dict[str, Any]) -> dict[str, Any]:
    """待ちの上限に達した結果。種類は wait_timeout、`next` は同じ待ち。"""
    running = [w.name for w in found if w.status is None]
    detail = f"{waited:.0f} 秒待ったが {len(running)} 本のプランが流れている"
    items = [_item(w) for w in found]
    return result.stopped(tool, "wait_timeout", detail, items=items, metrics={"waited_seconds": round(waited, 1)}, next=again)
