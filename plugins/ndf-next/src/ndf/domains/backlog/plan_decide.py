"""plan の却下と移動を、順位の算出（rank.json の metrics）の候補と照らす（純粋）。

plan の形:

```json
{"rank": "<rank.json の digest>",
 "rejected": [{"number": 21, "direction": "前倒し"}],
 "reschedule": [{"number": 23, "direction": "前倒し", "milestone": "11.0.0"}],
 "reparent": [{"number": 40, "parent": 12}]}
```

却下と移動は本人の候補（候補の `number` が plan の番号と同じもの）だけに照らす。ほかの前倒しの候補の依存先
（`group`）にしか無い番号の却下は誤りにし、反映は 1 件も書かない（#1595）。依存先の移動は、依存する候補の移動と
同じ移す先で plan に並べたときだけ受ける（先頭だけが動いて依存先を置いていかない）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, NamedTuple, Optional

from ndf.domains.backlog.model import APPROVAL, AUTO, APPROVED, BACKWARD, DIRECTIONS, FORWARD


class Checked(NamedTuple):
    errors: list[str]
    # 承認を待つ移動の番号（反映しない）
    holds: set[int]


def _list(metrics: Mapping[str, Any], direction: str) -> list[dict[str, Any]]:
    key = "forward" if direction == FORWARD else "backward"
    got = metrics[key] if key in metrics else []
    return got if isinstance(got, list) else []


def own_candidate(metrics: Mapping[str, Any], n: Any, direction: str) -> Optional[dict[str, Any]]:
    """plan の番号と向きに当たる本人の候補。依存先にしか無い番号は None。"""
    return next((c for c in _list(metrics, direction) if c["number"] == n), None)


def lead_of(metrics: Mapping[str, Any], n: Any) -> Optional[dict[str, Any]]:
    """`n` を依存先（group）に持つ前倒しの候補。"""
    return next((c for c in _list(metrics, FORWARD) if n in c["group"]), None)


def _entries(plan: Mapping[str, Any], key: str, errors: list[str]) -> list[dict[str, Any]]:
    got = plan[key] if key in plan else []
    if not isinstance(got, list) or not all(isinstance(e, dict) for e in got):
        errors.append(f"{key} はオブジェクトの列で書く")
        return []
    return got


def _direction_error(n: Any, d: Any) -> Optional[str]:
    return None if d in DIRECTIONS else f"#{n} の向きは {' / '.join(DIRECTIONS)} のどれか: {d!r}"


def _check_rejected(metrics: Mapping[str, Any], e: dict[str, Any]) -> Optional[str]:
    n, d = e.get("number"), e.get("direction")
    wrong = _direction_error(n, d)
    if wrong is not None:
        return wrong
    if own_candidate(metrics, n, d) is not None:
        return None
    lead = lead_of(metrics, n) if d == FORWARD else None
    if lead is not None:
        return f"#{n} は #{lead['number']} の依存先で、本人の候補が無い（退けるなら #{lead['number']} を退ける）"
    return f"#{n} の{d}は rank.json の候補に無い"


def _check_move(metrics: Mapping[str, Any], e: dict[str, Any], moved: set[tuple[Any, Any]]) -> Optional[str]:
    """1 件の移動を照らす。`moved` は plan の前倒しの (番号, 移す先) の集まり。"""
    n, d, to = e.get("number"), e.get("direction"), e.get("milestone")
    wrong = _direction_error(n, d)
    if wrong is not None:
        return wrong
    c = own_candidate(metrics, n, d)
    if c is None:
        return _check_dependency_move(metrics, n, d, to, moved)
    if to != c["to"]:
        return f"#{n} の milestone は候補の移す先 {c['to']!r} と合わせる"
    missing = [g for g in c["group"] if (g, c["to"]) not in moved] if d == FORWARD else []
    if missing:
        return f"#{n} の前倒しは依存先 {', '.join(f'#{g}' for g in missing)} も同じ移す先で前倒しする"
    return None


def _check_dependency_move(metrics: Mapping[str, Any], n: Any, d: Any, to: Any, moved: set[tuple[Any, Any]]) -> Optional[str]:
    """本人の候補が無い移動を照らす。前倒しの候補の依存先で、その候補と同じ移す先で前倒しするときだけ受ける。"""
    lead = lead_of(metrics, n) if d == FORWARD else None
    if lead is None:
        return f"#{n} の{d}は rank.json の候補に無い"
    if (lead["number"], lead["to"]) not in moved or to != lead["to"]:
        return f"#{n} は #{lead['number']} の依存先で、#{lead['number']} と同じ移す先 {lead['to']!r} で前倒しするときだけ移す"
    return None


def _holds(metrics: Mapping[str, Any], moves: list[dict[str, Any]]) -> set[int]:
    """承認を待つ移動。依存先が保留に入った前倒しの先頭も、先頭が保留の依存先も保留にする。"""
    holds = set()
    for e in moves:
        c = own_candidate(metrics, e["number"], e["direction"])
        if c is not None and c["decision"] == APPROVAL:
            holds.add(e["number"])
    for c in _list(metrics, FORWARD):
        members = {c["number"], *c["group"]}
        if members & holds:
            holds |= members & {e["number"] for e in moves}
    return holds


def check(plan: Mapping[str, Any], metrics: Mapping[str, Any]) -> Checked:
    """plan の却下と移動の誤りと、承認を待つ移動。"""
    errors: list[str] = []
    rejected = _entries(plan, "rejected", errors)
    moves = _entries(plan, "reschedule", errors)
    errors += [err for e in rejected if (err := _check_rejected(metrics, e)) is not None]
    moved = {(e.get("number"), e.get("milestone")) for e in moves if e.get("direction") == FORWARD}
    errors += [err for e in moves if (err := _check_move(metrics, e, moved)) is not None]
    return Checked(errors, set() if errors else _holds(metrics, moves))


def placed_moves(metrics: Mapping[str, Any]) -> list[tuple[int, str]]:
    """順位の表が移したものとして置く移動（前倒しの自動・承認済みと依存先、後ろ倒しの承認済み）。"""
    placed = [(n, FORWARD) for c in _list(metrics, FORWARD) if c["decision"] in (AUTO, APPROVED) for n in [c["number"], *c["group"]]]
    return placed + [(c["number"], BACKWARD) for c in _list(metrics, BACKWARD) if c["decision"] == APPROVED]


def rejected_after(current: list[dict[str, Any]], plan: Mapping[str, Any], metrics: Mapping[str, Any]) -> list[dict[str, Any]]:
    """却下の記録へ plan の却下を本人の候補の要約値で足し、rank.json の古い却下（rejected_stale）を消す。

    `check` が誤りを返さない plan だけを渡す（本人の候補が必ずある）。
    """
    stale_list = metrics["rejected_stale"] if "rejected_stale" in metrics else []
    stale = {r["move_digest"] for r in stale_list}
    out = [r for r in current if r.get("move_digest") not in stale]
    have = {(r.get("number"), r.get("direction"), r.get("move_digest")) for r in out}
    for e in plan["rejected"] if "rejected" in plan else []:
        c = own_candidate(metrics, e["number"], e["direction"])
        key = (e["number"], e["direction"], c["move_digest"])
        if key not in have:
            out.append({"number": e["number"], "direction": e["direction"], "move_digest": c["move_digest"]})
            have.add(key)
    return out
