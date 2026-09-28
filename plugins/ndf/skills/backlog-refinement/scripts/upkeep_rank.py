"""upkeep_rank.py: 課題の順位付けの算出（#1429）。GitHub を呼ばず、終了コードを持たない。

式・同点の扱い・依存の順序・大きい課題の枠・切り出しの境界・前倒しと後ろ倒しの判定・`### 順位` の節の描画と解析を
ここだけに置く。`SKILL.md` と `references/ranking.md` はこのファイルの関数を指し、式を写さない。

- 遅延コスト（CoD）= UBV + TC + RR/OE（どれも実効の段階）
- 順位の値 = CoD ÷ Size^α（α は大きさの指数）を小数 9 桁へ丸めた値
- マイルストーンの中の並び: 同じマイルストーンの中の依存を制約にした Kahn の算法。並べられる課題の中は
  順位の値の降順 → CoD の降順 → 番号の昇順
- 前倒しは CoD で比べる。比べる相手は直近のマイルストーンの切り出しの境界の課題

誤りは例外で返す（`RankError` は入力・宣言の誤り、`CycleError` は依存の循環）。`upkeep.py` が終了コードへ変換する。
"""

from __future__ import annotations

import hashlib
import heapq
import json
import math
import re
from dataclasses import dataclass, field

from upkeep_rank_config import (  # noqa: F401  呼び出し側は upkeep_rank から引く
    APPROVAL,
    APPROVED,
    AUTO,
    BACKWARD,
    COLUMN_NAMES,
    COLUMNS,
    DIRECTIONS,
    FORWARD,
    HEADING,
    MITIGATIONS,
    RESTORED_WHY,
    SLOT_FIT,
    SLOT_NONE,
    SLOT_SPLIT,
    Config,
    CycleError,
    Estimate,
    RankError,
    build_config,
    restore_from_row,
    validate_estimate,
)
from upkeep_rank_table import (  # noqa: F401
    outside_unchanged,
    parallel_group_edges,
    parse_rank_table,
    rank_changes,
    render_rank_table,
    replace_rank_section,
    slot_text,
)

# ---------------- 依存 ----------------


def merge_edges(sources, open_numbers) -> set:
    """依存の辺（a は b に依存する）を取り元の優先の順に合わせる（決定 2）。

    sources は優先の高い順の辺の列。上位の取り元に逆向きの辺があれば下位の辺を捨てる。open でない課題への辺は捨てる。
    """
    got: dict = {}
    for prio, edges in enumerate(sources):
        for a, b in edges:
            if a == b or a not in open_numbers or b not in open_numbers:
                continue
            rev = got.get((b, a))
            if rev is not None and rev < prio:
                continue
            got.setdefault((a, b), prio)
    return set(got)


def sub_issue_edges(sub_issues: dict) -> list:
    """サブイシュー: 子は親に依存する。"""
    return [(c, p) for p, kids in sub_issues.items() for c in kids]


def find_cycle(nodes, edges) -> list:
    """循環する課題の番号（循環の上にあるもの）。無ければ空。"""
    nodes = set(nodes)
    out = {n: set() for n in nodes}
    inc = {n: set() for n in nodes}
    for a, b in edges:
        if a in nodes and b in nodes:
            out[a].add(b)
            inc[b].add(a)
    alive = set(nodes)
    changed = True
    while changed:  # 入る辺か出る辺が無い課題を外していくと、循環の上の課題だけが残る
        changed = False
        for n in sorted(alive):
            if not (out[n] & alive) or not (inc[n] & alive):
                alive.discard(n)
                changed = True
    return sorted(alive)


def topo_order(numbers, edges, key) -> list:
    """numbers を依存の順に並べる（Kahn の算法）。並べられる課題の中は key の昇順。"""
    numbers = set(numbers)
    deps = {n: set() for n in numbers}
    users = {n: [] for n in numbers}
    for a, b in edges:
        if a in numbers and b in numbers:
            deps[a].add(b)
            users[b].append(a)
    heap = [(key(n), n) for n in numbers if not deps[n]]
    heapq.heapify(heap)
    order = []
    while heap:
        _, n = heapq.heappop(heap)
        order.append(n)
        for u in users[n]:
            deps[u].discard(n)
            if not deps[u]:
                heapq.heappush(heap, (key(u), u))
    if len(order) != len(numbers):
        raise CycleError(find_cycle(numbers - set(order), edges))
    return order


# ---------------- 順位 ----------------


@dataclass(frozen=True)
class Row:
    number: int
    ubv: int
    tc: int
    rr_oe: int
    size: int
    cod: int
    value: float
    sources: dict
    released: int
    waiting_decision: bool
    observed_harm: bool

    def steps(self) -> dict:
        return {"ubv": self.ubv, "tc": self.tc, "rr_oe": self.rr_oe, "size": self.size}


def score(est: Estimate, labels, released: int, cfg: Config) -> Row:
    """実効の段階・CoD・順位の値（I2）。ラベルの下限と被依存の対応表は上げる向きにだけ効く。"""
    ubv, ubv_src = est.steps["ubv"], "llm"
    floor = cfg.label_step(labels)
    if floor is not None and floor > ubv:
        ubv, ubv_src = floor, "label"
    rr, rr_src = est.steps["rr_oe"], "llm"
    by_deps = cfg.dependents_step(released)
    if by_deps > rr:
        rr, rr_src = by_deps, "dependents"
    cod = ubv + est.steps["tc"] + rr
    value = round(cod / (est.steps["size"] ** cfg.size_exponent), 9)
    return Row(
        est.number,
        ubv,
        est.steps["tc"],
        rr,
        est.steps["size"],
        cod,
        value,
        {"ubv": ubv_src, "rr_oe": rr_src},
        released,
        est.waiting_decision,
        est.observed_harm,
    )


def row_key(r: Row):
    """同じ順に並べられる課題の中の順序: 順位の値の降順 → CoD の降順 → 番号の昇順（I4）。"""
    return (-r.value, -r.cod, r.number)


def milestone_order(titles, given=None) -> list:
    """マイルストーンの着手の順。与えられればその順（無いものは後ろへ）、無ければ名前の先頭の連番の昇順。"""

    def num(t):
        m = re.match(r"\s*(\d+)", t)
        return (0, int(m.group(1)), t) if m else (1, 0, t)

    rest = sorted((t for t in titles if not given or t not in given), key=num)
    head = [t for t in (given or []) if t in titles]
    return head + rest


def large_slot(rows_in, capacity, cfg: Config, sub_issues, edges):
    """大きい課題の枠（決定 21 の B・I9）。容量が無い・枠が 1 未満なら None。"""
    if capacity is None:
        return None
    budget = math.floor(capacity * cfg.large_slot_ratio)
    if budget < 1:
        return None
    by_num = {r.number: r for r in rows_in}
    large = [r for r in rows_in if r.size >= cfg.large_min_size]
    if not large:
        return {"issue": None, "slice": None, "size": None, "budget": budget, "status": SLOT_NONE}
    big = min(large, key=lambda r: (-r.cod, r.number))
    kids = [k for k in sub_issues.get(big.number, []) if k in by_num]
    if kids:
        order = topo_order(kids, edges, lambda n: row_key(by_num[n]))
        fit = [k for k in order if by_num[k].size <= budget]
        piece = fit[0] if fit else None
    else:
        piece = big.number if big.size <= budget else None
    if piece is None:
        return {"issue": big.number, "slice": None, "size": big.size, "budget": budget, "status": SLOT_SPLIT}
    return {"issue": big.number, "slice": piece, "size": by_num[piece].size, "budget": budget, "status": SLOT_FIT}


def boundary(rows_in, capacity, slot):
    """切り出しの境界（決定 21 の B）。枠の 1 切れを除いて先頭から、容量の残りに収まるまで取った最後の課題。"""
    if capacity is None or not rows_in:
        return None
    piece = slot["slice"] if slot and slot["status"] == SLOT_FIT else None
    rest = capacity - (slot["size"] if piece is not None else 0)
    total, last = 0, None
    for r in rows_in:
        if r.number == piece:
            continue
        if total + r.size > rest:
            break
        total, last = total + r.size, r
    if last is None:
        first = next((r for r in rows_in if r.number != piece), None)
        if first is None:
            return None
        return {"number": first.number, "size_sum": first.size, "capacity": capacity, "over": True}
    return {"number": last.number, "size_sum": total, "capacity": capacity, "over": False}


def stable_digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


@dataclass
class Board:
    """1 回の算出の入力。issues は open の課題 {番号: {"milestone": 題名 | None, "labels": [...]}}。"""

    issues: dict
    estimates: dict
    cfg: Config
    order: list
    sub_issues: dict = field(default_factory=dict)
    edges: set = field(default_factory=set)
    previous: dict = field(default_factory=dict)
    rejected: list = field(default_factory=list)
    approved: tuple = ()
    excluded: list = field(default_factory=list)


def _milestone_rows(board, rows, place, title):
    nums = [n for n, t in place.items() if t == title and n in rows]
    return [rows[n] for n in topo_order(nums, board.edges, lambda n: row_key(rows[n]))]


def _group(board, n, place, nearest):
    """候補が依存する課題のうち、直近より後ろか未設定にあるもの（推移的に集める）。"""
    deps = {}
    for a, b in board.edges:
        deps.setdefault(a, set()).add(b)
    seen, stack, out = {n}, [n], set()
    while stack:
        for b in deps.get(stack.pop(), ()):
            if b in seen:
                continue
            seen.add(b)
            stack.append(b)
            if place.get(b) != nearest:
                out.add(b)
    return sorted(out)


def _forward_candidate(board, n, r, b, place, nearest, base, threshold, rejected):
    """前倒しの候補 1 件。戻り値は (move_digest, 却下で外したもの, 候補) で、どちらか一方だけが入る。"""
    cfg = board.cfg
    group = _group(board, n, place, nearest)
    md = stable_digest({**base, "number": n, "direction": FORWARD, "from": place.get(n), "to": nearest,
                  "digest": board.estimates[n].digest, "group": group})  # fmt: skip
    if (n, FORWARD, md) in rejected:
        return md, {"number": n, "direction": FORWARD, "move_digest": md}, None
    auto = r.observed_harm and (r.ubv >= cfg.auto_threshold or r.rr_oe >= cfg.auto_threshold)
    decision = AUTO if auto else (APPROVED if n in board.approved else APPROVAL)
    higher = [COLUMN_NAMES[c] for c in ("ubv", "tc", "rr_oe") if getattr(r, c) > getattr(b, c)]
    return md, None, {"number": n, "from": place.get(n), "to": nearest, "cod": r.cod, "boundary_cod": b.cod,
                      "threshold": threshold, "diff": r.cod - b.cod, "higher_columns": higher, "decision": decision,
                      "group": group, "move_digest": md}  # fmt: skip


def _backward_candidate(board, forward, near_rows, place, nearest, base, rejected):
    """後ろ倒しの候補。戻り値は (backward, dropped に足すもの, seen に足すもの)。"""
    order = board.order
    backward, dropped, seen = [], [], []
    depended = {bb for a, bb in board.edges if place.get(a) == nearest and place.get(bb) == nearest}
    free = [r for r in near_rows if r.number not in depended]
    low = min(free, key=lambda r: (r.cod, -r.value, r.number), default=None)
    if low is not None and low.cod < min(f["cod"] for f in forward):
        pair = sorted(f["number"] for f in forward)
        md = stable_digest({**base, "number": low.number, "direction": BACKWARD, "from": nearest, "to": order[1],
                      "digest": board.estimates[low.number].digest, "pair": pair})  # fmt: skip
        seen.append(md)
        if (low.number, BACKWARD, md) in rejected:
            dropped.append({"number": low.number, "direction": BACKWARD, "move_digest": md})
        else:
            decision = APPROVED if low.number in board.approved else APPROVAL
            backward.append({"number": low.number, "from": nearest, "to": order[1], "cod": low.cod,
                             "decision": decision, "move_digest": md})  # fmt: skip
    return backward, dropped, seen


def _stale_rejections(rejected, seen):
    return [
        {"number": r.get("number"), "direction": r.get("direction"), "move_digest": r.get("move_digest")}
        for r in rejected
        if r.get("move_digest") not in seen
    ]


def _candidates(board, rows, place):
    """前倒し・後ろ倒しの候補（AC6〜AC8）と、却下で外したもの・却下の一覧の古い要素。"""
    cfg, order = board.cfg, board.order
    nearest = order[0]
    near_rows = _milestone_rows(board, rows, place, nearest)
    slot = large_slot(near_rows, cfg.capacity, cfg, board.sub_issues, board.edges)
    bnd = boundary(near_rows, cfg.capacity, slot)
    if bnd is None:
        return [], [], [], []
    b = rows[bnd["number"]]
    b_est = board.estimates[b.number]
    threshold = cfg.threshold(b.cod)
    rejected = {(r.get("number"), r.get("direction"), r.get("move_digest")) for r in board.rejected}
    base = {"boundary": b.number, "boundary_digest": b_est.digest, "boundary_cod": b.cod}
    forward, dropped, seen = [], [], []
    for n in sorted(rows, key=lambda n: (-rows[n].cod, n)):
        r = rows[n]
        if place.get(n) == nearest or r.cod < threshold:
            continue
        md, drop, item = _forward_candidate(board, n, r, b, place, nearest, base, threshold, rejected)
        seen.append(md)
        if drop is not None:
            dropped.append(drop)
        else:
            forward.append(item)
    backward = []
    if forward and len(order) > 1:
        backward, dropped_add, seen_add = _backward_candidate(board, forward, near_rows, place, nearest, base, rejected)
        dropped += dropped_add
        seen += seen_add
    return forward, backward, dropped, _stale_rejections(board.rejected, seen)


def _milestone_out(board, rows, place, title, idx):
    cfg = board.cfg
    ranked = _milestone_rows(board, rows, place, title)
    slot = large_slot(ranked, cfg.capacity, cfg, board.sub_issues, board.edges)
    bnd = boundary(ranked, cfg.capacity, slot)
    out_rows = [
        {
            "rank": k,
            "number": r.number,
            **r.steps(),
            "cod": r.cod,
            "value": r.value,
            "waiting_decision": r.waiting_decision,
            "released": r.released,
            "sources": r.sources,
        }  # fmt: skip
        for k, r in enumerate(ranked, 1)
    ]
    totals = {
        "cod_sum": sum(r.cod for r in ranked),
        "cod_max": max((r.cod for r in ranked), default=0),
        "high_count": sum(1 for r in ranked if r.ubv >= cfg.auto_threshold),
    }
    return {"title": title, "order": idx, "rows": out_rows, "large_slot": slot, "boundary": bnd, "totals": totals}


def compute_ranking(board: Board) -> dict:
    """順位・枠・境界・候補・変化を算出する。CycleError を投げうる。"""
    cfg = board.cfg
    open_nums = set(board.issues)
    ranked = {n for n in open_nums if n in board.estimates}
    cyc = find_cycle(ranked, board.edges)
    if cyc:
        raise CycleError(cyc)
    released = {n: 0 for n in open_nums}
    for a, b in board.edges:  # 被依存の数は open の課題からの辺だけを数える（excluded の課題からの辺も数える）
        released[b] += 1
    rows = {n: score(board.estimates[n], board.issues[n].get("labels", []), released[n], cfg) for n in ranked}
    place = {n: board.issues[n].get("milestone") for n in open_nums}
    place = {n: (t if t in board.order else None) for n, t in place.items()}
    forward, backward, dropped, stale = [], [], [], []
    if board.order:
        forward, backward, dropped, stale = _candidates(board, rows, place)
    moved = dict(place)
    for f in forward:
        if f["decision"] in (AUTO, APPROVED):
            for n in [f["number"], *f["group"]]:
                moved[n] = f["to"]
    for b in backward:
        if b["decision"] == APPROVED:
            moved[b["number"]] = b["to"]
    milestones = [_milestone_out(board, rows, moved, t, i) for i, t in enumerate(board.order, 1)]
    pos = {t: i for i, t in enumerate(board.order)}
    cross = [
        {"number": a, "milestone": moved[a], "depends_on": b, "depends_on_milestone": moved[b]}
        for a, b in sorted(board.edges)
        if moved.get(a) in pos and moved.get(b) in pos and pos[moved[a]] < pos[moved[b]]
    ]
    return {
        "milestones": milestones,
        "forward": forward,
        "backward": backward,
        "rejected": dropped,
        "rejected_stale": stale if cfg.capacity is not None else [],
        "cross_milestone_deps": cross,
        "placement": {n: moved[n] for n in sorted(ranked)},
        "changes": rank_changes(milestones, board.previous, moved, {e["number"]: e["reason"] for e in board.excluded}),
    }
