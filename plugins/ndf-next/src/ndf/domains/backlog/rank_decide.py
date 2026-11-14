"""順位の算出（純粋）。マイルストーンごとの並び・大きい課題の枠・品質の枠・切り出しの境界・前倒しと後ろ倒しの候補。

切り出しの境界は、直近のマイルストーンの先頭から容量に収まるまで取った最後の課題である。尺度を超える課題と
人の判断待ちの課題（と、それを前提とする課題）は境界へ渡さない（#2232）。

却下の記録は (番号・向き・移動の要約値) の組で照らす。この回に候補を組んだ向きの却下のうち、どの候補とも
合わないものだけを古い記録として返す。後ろ倒しを組まなかった回は、後ろ倒しの却下を古いと見なさない（#1595 の関連 2）。
"""

from __future__ import annotations

import heapq
from collections import Counter
import math
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Optional

from ndf.domains.backlog import model
from ndf.domains.backlog.model import APPROVAL, APPROVED, AUTO, BACKWARD, FORWARD, SLOT_FIT, SLOT_NONE, SLOT_SPLIT, Config, Row

Edge = tuple[int, int]  # (a, b): a は b に依存する


class CycleError(ValueError):
    """依存の辺が輪になっている。"""

    def __init__(self, numbers: list[int]) -> None:
        super().__init__("依存が輪になっている: " + " → ".join(f"#{n}" for n in numbers))
        self.numbers = numbers


@dataclass(frozen=True)
class Board:
    """1 回の算出の入力。`issues` は open の課題 {番号: {"milestone": 題名 | None, "labels": [...], "created_at": ...}}。

    `sized` は見積もりの記録から Size を決めた課題（表の行の Size の出どころ）。
    """

    issues: dict[int, dict[str, Any]]
    estimates: dict[int, model.Estimate]
    cfg: Config
    order: list[str]
    sub_issues: dict[int, list[int]] = field(default_factory=dict)
    edges: frozenset[Edge] = frozenset()
    rejected: tuple[dict[str, Any], ...] = ()
    approved: frozenset[int] = frozenset()
    sized: frozenset[int] = frozenset()


def _of(table: dict[int, Any], n: int) -> list[int]:
    """番号の列の表から 1 件の列を引く（無ければ空）。"""
    return sorted(table[n]) if n in table else []


def depends_on_map(edges: Iterable[Edge]) -> dict[int, set[int]]:
    out: dict[int, set[int]] = {}
    for a, b in edges:
        out.setdefault(a, set()).add(b)
    return out


def _reachable(starts: Iterable[int], adjacency: dict[int, set[int]]) -> set[int]:
    """`starts` から隣接の表をたどって届く番号（`starts` 自身は含めない）。"""
    seen = set(starts)
    stack, out = list(seen), set()
    while stack:
        for b in adjacency.get(stack.pop(), ()):
            if b not in seen:
                seen.add(b)
                stack.append(b)
                out.add(b)
    return out


def dependency_edges(raws: list[Any], issues: list[dict[str, Any]]) -> frozenset[Edge]:
    """open の課題どうしの依存の辺。見積の `depends_on`（番号の列）と、親が open の子を待つ辺（親 → 子）。

    見積が子 → 親の依存を書いていれば、輪にしないため親 → 子の辺を足さない。
    """
    open_numbers = {i["number"] for i in issues}
    out = set()
    for raw in raws:
        deps = raw["depends_on"] if isinstance(raw, dict) and isinstance(raw.get("depends_on"), list) else []
        out |= {(raw["number"], b) for b in deps if b in open_numbers and raw["number"] in open_numbers}
    subs = {(i["parent"], i["number"]) for i in issues if i["parent"] in open_numbers}
    return frozenset(out | {(p, k) for p, k in subs if (k, p) not in out})


def sub_issues_of(issues: list[dict[str, Any]]) -> dict[int, list[int]]:
    """open の親 → open の子の番号の列（大きい課題の枠が子を切り出しに当てる）。"""
    open_numbers = {i["number"] for i in issues}
    out: dict[int, list[int]] = {}
    for i in issues:
        if i["parent"] in open_numbers:
            out.setdefault(i["parent"], []).append(i["number"])
    return out


def topo_order(numbers: Iterable[int], edges: Iterable[Edge], key: Callable[[int], Any]) -> list[int]:
    """依存先を先に置き、同じ順にできる課題は `key` の順に並べる。輪があれば `CycleError`。"""
    nums = set(numbers)
    inner = [(a, b) for a, b in edges if a in nums and b in nums]
    waiting = {n: 0 for n in nums}
    after: dict[int, list[int]] = {}
    for a, b in inner:
        waiting[a] += 1
        after.setdefault(b, []).append(a)
    ready = [(key(n), n) for n, c in waiting.items() if c == 0]
    heapq.heapify(ready)
    out: list[int] = []
    while ready:
        _, n = heapq.heappop(ready)
        out.append(n)
        for a in after.pop(n, []):
            waiting[a] -= 1
            if waiting[a] == 0:
                heapq.heappush(ready, (key(a), a))
    if len(out) != len(nums):
        raise CycleError(sorted(nums - set(out)))
    return out


def milestone_order(titles: Iterable[str], given: Optional[list[str]] = None) -> list[str]:
    """マイルストーンの着手の順。与えられればその順（無いものは後ろへ）、無ければ名前の先頭の連番の昇順。"""
    named = list(given) if given else []

    def num(t: str) -> tuple[int, int, str]:
        m = re.match(r"\s*(\d+)", t)
        return (0, int(m.group(1)), t) if m else (1, 0, t)

    pool = set(titles)
    return [t for t in named if t in pool] + sorted((t for t in pool if t not in named), key=num)


def large_slot(rows: list[Row], cfg: Config, board: Board) -> Optional[dict[str, Any]]:
    """大きい課題の枠。容量が無い・枠が 1 未満なら None。子の課題があれば依存の順で枠に収まる最初の子を当てる。"""
    if cfg.capacity is None:
        return None
    budget = math.floor(cfg.capacity * cfg.large_slot_ratio)
    if budget < 1:
        return None
    by_num = {r.number: r for r in rows}
    large = [r for r in rows if r.size >= cfg.large_min_size]
    if not large:
        return _slot_dict(None, None, None, budget, SLOT_NONE)
    big = min(large, key=lambda r: (-r.cod, r.number))
    piece = _slice_of(big, board, by_num, budget)
    if piece is None:
        return _slot_dict(big.number, None, big.size, budget, SLOT_SPLIT)
    return _slot_dict(big.number, piece, by_num[piece].size, budget, SLOT_FIT)


def _slice_of(big: Row, board: Board, by_num: dict[int, Row], budget: int) -> Optional[int]:
    """枠に当てる 1 切れ。子があれば依存の順で枠に収まる最初の子、無ければ本人が収まれば本人、どちらも無ければ None。"""
    kids = [k for k in _of(board.sub_issues, big.number) if k in by_num]
    if not kids:
        return big.number if big.size <= budget else None
    fit = [k for k in topo_order(kids, board.edges, lambda n: model.row_key(by_num[n])) if by_num[k].size <= budget]
    return fit[0] if fit else None


def _slot_dict(issue: Optional[int], piece: Optional[int], size: Optional[int], budget: int, status: str) -> dict[str, Any]:
    return {"issue": issue, "slice": piece, "size": size, "budget": budget, "status": status}


def _slot_piece(slot: Optional[dict[str, Any]]) -> Optional[int]:
    return slot["slice"] if slot and slot["status"] == SLOT_FIT else None


def _slot_used(slot: Optional[dict[str, Any]]) -> int:
    """枠の 1 切れが使う Size（当てた切れが無ければ 0）。"""
    return slot["size"] if _slot_piece(slot) is not None else 0


def _fits(total: int, size: int, budget: Optional[int], room: Optional[int], below_min: bool) -> bool:
    """品質の枠に入るか。ratio の枠に収まるか、min 件に満たないうちは容量の残りに収まるか。"""
    return budget is None or total + size <= budget or (below_min and total + size <= room)


def quality_slot(rows: list[Row], board: Board, slot: Optional[dict[str, Any]], blocked: set[int]) -> Optional[dict[str, Any]]:
    """品質の枠。宣言が無ければ None。容量の ratio 分（最低 min 件）を品質のラベルの課題から先に取る。

    並びは 利用者へ届いた不具合 → 起票の古い順 → 今の順位。依存先が先に枠へ入っていない課題は取らない。
    """
    cfg, q = board.cfg, board.cfg.quality
    if q is None:
        return None
    piece = _slot_piece(slot)
    pool = [r for r in rows if q["label"] in board.issues[r.number]["labels"] and r.number != piece and r.number not in blocked]
    pool.sort(key=lambda r: (not r.observed_harm, board.issues[r.number]["created_at"], model.row_key(r)))
    budget = None if cfg.capacity is None else math.floor(cfg.capacity * q["ratio"])
    room = None if cfg.capacity is None else cfg.capacity - _slot_used(slot)
    deps = depends_on_map(board.edges)
    taken: list[int] = []
    total = 0
    for r in pool:
        if not set(_of(deps, r.number)) <= set(taken):
            continue
        fits = _fits(total, r.size, budget, room, len(taken) < q["min"])
        if len(taken) >= q["min"] and (budget is None or not fits):
            break
        if fits:
            taken.append(r.number)
            total += r.size
    return {"label": q["label"], "ratio": q["ratio"], "min": q["min"], "budget": budget, "issues": taken, "size_sum": total}


def boundary(
    rows: list[Row], capacity: Optional[int], slot: Optional[dict[str, Any]], quality: Optional[dict[str, Any]]
) -> Optional[dict[str, Any]]:
    """切り出しの境界。枠の 1 切れと品質の枠の課題を除いて先頭から、容量の残りに収まるまで取った最後の課題。

    1 件も収まらなければ、枠で容量を使い切ったときは `empty`、そうでなければ先頭の 1 件を `over` で返す。
    """
    if capacity is None or not rows:
        return None
    piece = _slot_piece(slot)
    reserved = set(quality["issues"]) if quality else set()
    rest = capacity - _slot_used(slot) - (quality["size_sum"] if quality else 0)
    pool = [r for r in rows if r.number not in reserved and r.number != piece]
    if not pool:
        return None
    total, last = 0, None
    for r in pool:
        if total + r.size > rest:
            break
        total, last = total + r.size, r
    if last is not None:
        return {"number": last.number, "size_sum": total, "capacity": capacity, "over": False}
    if piece is not None or reserved:
        return {"number": pool[0].number, "size_sum": 0, "capacity": capacity, "over": False, "empty": True}
    return {"number": pool[0].number, "size_sum": pool[0].size, "capacity": capacity, "over": True}


def held_back(rows: list[Row], edges: Iterable[Edge]) -> set[int]:
    """境界に入れない課題: 尺度を超える課題・人の判断待ちの課題と、それを（辺を辿って）前提とする課題。"""
    blocked = {r.number for r in rows if r.over or r.waiting_decision}
    return blocked | _reachable(blocked, depends_on_map((b, a) for a, b in edges))


def slots(board: Board, ranked: list[Row]) -> tuple[Optional[dict], Optional[dict], Optional[dict]]:
    """大きい課題の枠・品質の枠・切り出しの境界。候補と表で同じ決め方を使う。"""
    slot = large_slot(ranked, board.cfg, board)
    blocked = held_back(ranked, board.edges)
    quality = quality_slot(ranked, board, slot, blocked)
    return slot, quality, boundary([r for r in ranked if r.number not in blocked], board.cfg.capacity, slot, quality)


def milestone_rows(board: Board, rows: dict[int, Row], place: dict[int, Optional[str]], title: str) -> list[Row]:
    nums = [n for n, t in place.items() if t == title and n in rows]
    return [rows[n] for n in topo_order(nums, board.edges, lambda n: model.row_key(rows[n]))]


def group_of(board: Board, n: int, place: dict[int, Optional[str]], nearest: str) -> list[int]:
    """候補が（推移的に）依存する課題のうち、直近のマイルストーンに無いもの。前倒しは一緒に動かす。"""
    return sorted(b for b in _reachable({n}, depends_on_map(board.edges)) if place.get(b) != nearest)


@dataclass
class _Moves:
    """候補を組む途中の値。`seen` は組んだ候補の要約値、`built` は候補を組んだ向き。"""

    forward: list[dict[str, Any]] = field(default_factory=list)
    backward: list[dict[str, Any]] = field(default_factory=list)
    dropped: list[dict[str, Any]] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)
    built: set[str] = field(default_factory=set)


def _rejected_keys(board: Board) -> set[tuple[Any, Any, Any]]:
    return {(r.get("number"), r.get("direction"), r.get("move_digest")) for r in board.rejected}


def _take(moves: _Moves, rejected: set, n: int, direction: str, digest: str, item: dict[str, Any]) -> None:
    """1 件の候補を、却下に当たれば dropped へ、当たらなければ候補へ置く。"""
    moves.seen.add(digest)
    moves.built.add(direction)
    if (n, direction, digest) in rejected:
        moves.dropped.append({"number": n, "direction": direction, "move_digest": digest})
    elif direction == FORWARD:
        moves.forward.append(item)
    else:
        moves.backward.append(item)


@dataclass(frozen=True)
class _MoveContext:
    """候補を組む回の固定の組。`base` は境界の要約値（move_digest の入力の共通部分）。"""

    place: dict[int, Optional[str]]
    nearest: str
    threshold: int
    base: dict[str, Any]


def _forward_item(board: Board, r: Row, b: Row, ctx: _MoveContext) -> tuple[str, dict[str, Any]]:
    cfg, place, nearest = board.cfg, ctx.place, ctx.nearest
    group = group_of(board, r.number, place, nearest)
    digest = model.stable_digest({**ctx.base, "number": r.number, "direction": FORWARD, "from": place.get(r.number), "to": nearest,
                                  "digest": board.estimates[r.number].digest, "group": group})  # fmt: skip
    auto = r.observed_harm and max(r.ubv, r.rr_oe) >= cfg.auto_threshold
    decision = AUTO if auto else (APPROVED if r.number in board.approved else APPROVAL)
    higher = [model.COLUMN_NAMES[c] for c in model.STEPS if getattr(r, c) > getattr(b, c)]
    return digest, {"number": r.number, "from": place.get(r.number), "to": nearest, "cod": r.cod, "boundary_cod": b.cod,
                    "threshold": ctx.threshold, "diff": r.cod - b.cod, "higher_columns": higher, "decision": decision,
                    "group": group, "move_digest": digest}  # fmt: skip


def _backward(board: Board, moves: _Moves, near_rows: list[Row], ctx: _MoveContext, rejected: set) -> None:
    """前倒しの候補より CoD の低い直近の課題（ほかの直近の課題の依存先でないもの）を 1 件、次のマイルストーンへ。"""
    place, nearest, order = ctx.place, ctx.nearest, board.order
    depended = {b for a, b in board.edges if place.get(a) == nearest and place.get(b) == nearest}
    free = [r for r in near_rows if r.number not in depended]
    low = min(free, key=lambda r: (r.cod, -r.value, r.number), default=None)
    if low is None or low.cod >= min(f["cod"] for f in moves.forward):
        return
    pair = sorted(f["number"] for f in moves.forward)
    digest = model.stable_digest({**ctx.base, "number": low.number, "direction": BACKWARD, "from": nearest, "to": order[1],
                                  "digest": board.estimates[low.number].digest, "pair": pair})  # fmt: skip
    decision = APPROVED if low.number in board.approved else APPROVAL
    item = {"number": low.number, "from": nearest, "to": order[1], "cod": low.cod, "decision": decision, "move_digest": digest}
    _take(moves, rejected, low.number, BACKWARD, digest, item)


def stale_rejections(rejected: Iterable[dict[str, Any]], seen: set[str], built: set[str]) -> list[dict[str, Any]]:
    """この回に候補を組んだ向き（`built`）の却下のうち、どの候補とも合わないもの（古い記録）。"""
    return [
        {"number": r.get("number"), "direction": r.get("direction"), "move_digest": r.get("move_digest")}
        for r in rejected
        if r.get("direction") in built and r.get("move_digest") not in seen
    ]


def moves(board: Board, rows: dict[int, Row], place: dict[int, Optional[str]]) -> dict[str, list[dict[str, Any]]]:
    """前倒し・後ろ倒しの候補と、却下で外したもの・古い却下。境界が無ければどれも空。"""
    nearest = board.order[0]
    near_rows = milestone_rows(board, rows, place, nearest)
    bnd = slots(board, near_rows)[2]
    out = _Moves()
    if bnd is not None and bnd["number"] is not None:
        b = rows[bnd["number"]]
        threshold = board.cfg.threshold(b.cod)
        base = {"boundary": b.number, "boundary_digest": board.estimates[b.number].digest, "boundary_cod": b.cod}
        ctx = _MoveContext(place, nearest, threshold, base)
        rejected = _rejected_keys(board)
        for n in sorted(rows, key=lambda n: (-rows[n].cod, n)):
            if place.get(n) != nearest and rows[n].cod >= threshold:
                digest, item = _forward_item(board, rows[n], b, ctx)
                _take(out, rejected, n, FORWARD, digest, item)
        if out.forward and len(board.order) > 1:
            _backward(board, out, near_rows, ctx, rejected)
    return {"forward": out.forward, "backward": out.backward, "dropped": out.dropped,
            "rejected_stale": stale_rejections(board.rejected, out.seen, out.built)}  # fmt: skip


def _milestone_out(board: Board, rows: dict[int, Row], place: dict[int, Optional[str]], title: str, idx: int) -> dict[str, Any]:
    ranked = milestone_rows(board, rows, place, title)
    slot, quality, bnd = slots(board, ranked)
    marked = set(quality["issues"]) if quality else set()
    out_rows = [
        {
            "rank": k,
            "number": r.number,
            "ubv": r.ubv,
            "tc": r.tc,
            "rr_oe": r.rr_oe,
            "size": model.OVER_SCALE if r.over else r.size,
            "cod": r.cod,
            "value": r.value,
            "waiting_decision": r.waiting_decision,
            "sources": {**r.sources, "size": model.SOURCE_RECORDS if r.number in board.sized else model.SOURCE_LLM},
            **({"quality_slot": r.number in marked} if quality is not None else {}),
        }  # fmt: skip
        for k, r in enumerate(ranked, 1)
    ]
    out = {"title": title, "order": idx, "rows": out_rows, "large_slot": slot, "boundary": bnd,
           "over_scale": [r.number for r in ranked if r.over]}  # fmt: skip
    if quality is not None:
        out["quality_slot"] = quality
    return out


def released_counts(edges: Iterable[Edge]) -> dict[int, int]:
    """課題ごとの被依存の数（閉じると解放する課題の数）。"""
    return Counter(b for _, b in edges)


def compute(board: Board) -> dict[str, Any]:
    """マイルストーンごとの順位の表と、前倒し・後ろ倒しの候補。見積の無い課題は表に載せない。"""
    released = released_counts(board.edges)
    rows = {
        n: model.score(est, board.issues[n]["labels"], released[n] if n in released else 0, board.cfg)
        for n, est in board.estimates.items()
        if n in board.issues
    }
    place = {n: board.issues[n]["milestone"] for n in rows}
    tables = [_milestone_out(board, rows, place, t, i) for i, t in enumerate(board.order)]
    got = moves(board, rows, place) if board.order else {"forward": [], "backward": [], "dropped": [], "rejected_stale": []}
    return {"milestones": tables, **got}
