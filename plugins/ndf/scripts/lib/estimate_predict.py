"""見込みの算出（#1856）。類似の実績の選択・構成で分けること・値ごとの見込みと印・Size への丸め・
実装のステップの上限の見込み・予測と実績の誤差。

入力は記録の行と引数だけで、係数の定数を持たない（I6）。実績が 0 件なら、値は呼び出し側が渡した暫定の範囲か
空で、印は `未校正` にする。規約の正本は `skills/estimate/references/rules.md`。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

import estimate_records as er
import estimate_stats as stats

SCALE = (1, 2, 3, 5, 8, 13, 20)
OVER_SCALE = "尺度を超える"
MAX_CANDIDATES = stats.WINDOW
P_BREAK = 5  # この件数以上なら中央値と 80 分位、未満なら中央値と最大（決定 5）
IMPL_MIN_SAMPLES = 3  # 実装の上限を実績から出す最少の件数（決定 10。遅れの見張りの min_samples の既定と揃える）
VALUE_KEYS = ("A", "H", "L", "C")


@dataclass
class Request:
    issue: int | None
    task: str | None
    done: str
    items: dict = field(default_factory=dict)
    like: list[int] | None = None
    provisional: dict = field(default_factory=dict)  # {"A": (typical, high), ...}


# --- 実行の構成 ---


def current_config(rows: list[dict]) -> dict:
    """今の実行の構成: 最新の `run_end` の `config`。無ければ {}（すべて不明）。"""
    ends = [r for r in rows if r.get("kind") == "run_end" and isinstance(r.get("config"), dict)]
    ends.sort(key=lambda r: er.ts(r.get("at")) or 0)
    return dict(ends[-1]["config"]) if ends else {}


def _norm(key: str, v):
    if v in (None, "", []):
        return None
    return frozenset(v) if key == "models" and isinstance(v, list) else v


def config_diff(cur: dict, other: dict) -> tuple[list[str], bool]:
    """(違う鍵, どちらかが不明の鍵があったか)。不明の鍵は比べない。"""
    diff, unknown = [], False
    for k in er.CONFIG_KEYS:
        a, b = _norm(k, (cur or {}).get(k)), _norm(k, (other or {}).get(k))
        if a is None or b is None:
            unknown = True
            continue
        if a != b:
            diff.append(k)
    return diff, unknown


# --- 候補 ---


def _items_of(estimates: list[dict]) -> dict[int, dict]:
    """課題ごとの最新の見積もりの 6 項目。"""
    out: dict[int, tuple[float, dict]] = {}
    for e in estimates:
        if isinstance(e.get("issue"), int) and isinstance(e.get("items"), dict):
            t = er.ts(e.get("at")) or 0
            if e["issue"] not in out or out[e["issue"]][0] <= t:
                out[e["issue"]] = (t, e["items"])
    return {k: v for k, (_, v) in out.items()}


def items_difference(a: dict, b: dict) -> int:
    """`判定できない` を除き、両方で値の分かる項目のうち値の違う数。"""
    n = 0
    for k in er.ITEM_KEYS:
        x, y = (a or {}).get(k, "判定できない"), (b or {}).get(k, "判定できない")
        if "判定できない" in (x, y):
            continue
        n += x != y
    return n


def candidates(req: Request, actuals: dict[int, er.IssueActual], estimates: list[dict]) -> list[er.IssueActual]:
    """類似の実績の候補（完了条件のフェーズで成功した課題だけ）。`--like` を優先し、無ければ 6 項目の違いが 1 以下。"""
    done = {i: a for i, a in actuals.items() if a.completed and i != req.issue}
    if req.like:
        return [done[i] for i in req.like if i in done]
    items = _items_of(estimates)
    scored = [(items_difference(req.items, items[i]), -(er.ts(a.finished) or 0), i) for i, a in done.items() if i in items]
    scored = sorted(s for s in scored if s[0] <= 1)
    return [done[i] for _, _, i in scored[:MAX_CANDIDATES]]


# --- 値 ---


def _value(samples: list[tuple[int, float, bool]], provisional, missing_mark: str, notes: list[str]) -> dict:
    """samples: (課題, 実測, 等分したか)。件数で形を決める（決定 5）。0 件なら未校正か材料なし。"""
    note = "；".join(notes) or None
    if not samples:
        if provisional:
            lo, hi = provisional
            return {"mark": "未校正", "kind": None, "typical": lo, "high": hi, "basis": [], "note": note or "呼び出し側の暫定の範囲"}
        return {"mark": missing_mark, "kind": None, "typical": None, "high": None, "basis": [], "note": note}
    vals = [v for _, v, _ in samples]
    if len(vals) >= P_BREAK:
        kind, typical, high = "P50/P80", stats.median(vals), stats.quantile(vals, 0.8)
    else:
        kind, typical, high = "通常/追加の調査", stats.median(vals), stats.maximum(vals)
    basis = [{"issue": i, "value": v, **({"shared": True} if sh else {})} for i, v, sh in samples]
    return {"mark": "実績", "kind": kind, "typical": typical, "high": high, "basis": basis, "note": note}


def size_unit(a_values: list[float], decl: dict) -> float | None:
    """Size 1 の基準の秒。宣言 → 実績のある課題のうち A の小さい 4 分の 1 の中央値（4 課題以上）→ None。"""
    if decl.get("size_unit_seconds"):
        return float(decl["size_unit_seconds"])
    xs = sorted(v for v in a_values if v and v > 0)
    if len(xs) < 4:
        return None
    return stats.median(xs[: math.ceil(len(xs) / 4)])


def to_size(a_seconds: float | None, unit: float | None, scale=SCALE):
    """A を Size 1 の基準との比で尺度へ丸める。境界は隣り合う段階の中点、最大を超えたら「尺度を超える」（I7・決定 9）。"""
    if a_seconds is None or not unit:
        return None
    r = a_seconds / unit
    scale = sorted(scale)
    if r > scale[-1]:
        return OVER_SCALE
    for lo, hi in zip(scale, scale[1:]):
        if r <= (lo + hi) / 2:
            return lo
    return scale[-1]


def predict(
    req: Request,
    rows: list[dict],
    estimates: list[dict],
    decl: dict,
    materials: dict,
    alive: Callable | None = None,
) -> dict:
    """見積もり 1 件の結果（`predict` の出力の `items` の 1 要素）。"""
    actuals = er.issue_actuals(rows, req.done, alive=alive)
    cur = current_config(rows)
    same, other, unknown = [], [], False
    for a in candidates(req, actuals, estimates):
        diff, unk = config_diff(cur, a.config)
        if diff:
            other.append({"issue": a.issue, "keys": diff})
        else:
            same.append(a)
            unknown |= unk
    notes = ["構成の一部が不明"] if unknown else []
    values = {
        "A": _value([(a.issue, a.a, a.shared) for a in same], req.provisional.get("A"), "未校正", notes),
        "L": _value([(a.issue, a.l, False) for a in same if a.l is not None], req.provisional.get("L"), "未校正", notes),
        "C": _value(
            [(a.issue, a.c, a.shared) for a in same],
            req.provisional.get("C"),
            "未校正",
            notes + (["費用の取れない実行を含む"] if any(a.cost_unknown for a in same) else []),
        ),
        "H": _value(
            [(a.issue, a.h, False) for a in same if a.h is not None],
            req.provisional.get("H"),
            "材料なし",
            notes + ([] if any(a.h is not None for a in same) else ["人の作業時間の記録が無い"]),
        ),
    }
    unit = size_unit([a.a for a in actuals.values() if a.completed], decl)
    a_val = values["A"]
    size = to_size(a_val["typical"], unit, decl.get("scale") or SCALE) if a_val["mark"] == "実績" else None
    return {
        "kind": "estimate",
        "issue": req.issue,
        "task": req.task,
        "done": req.done,
        "items": {k: (req.items or {}).get(k, "判定できない") for k in er.ITEM_KEYS},
        "config": {k: cur.get(k) for k in er.CONFIG_KEYS},
        "values": values,
        "size": size,
        "size_unit_seconds": unit,
        "other_config": other,
        "materials": materials,
    }


# --- 実装の上限 ---


def impl_limit(rows: list[dict], window: int = stats.WINDOW, min_samples: int = IMPL_MIN_SAMPLES, alive: Callable | None = None) -> dict:
    """同じ構成でフェーズ `実装`・ステップ `impl` の `step` の行の直近 window 件（失敗を含む）の最大（決定 10）。

    返り値: {"source": "history"|"uncalibrated", "samples", "high", "basis": [実行の識別子...]}。余裕は呼び出し側が掛ける。"""
    cur = current_config(rows)
    table = er.run_table(rows, alive or (lambda run: False))
    picked: list[tuple[float, str, float]] = []
    for run in table.values():
        if run.phase != "実装" or config_diff(cur, run.config)[0]:
            continue
        for s in run.steps:
            if s.get("step") == "impl" and isinstance(s.get("seconds"), (int, float)):
                picked.append((er.ts(s.get("at")) or er.ts(run.started) or 0, run.run, float(s["seconds"])))
    picked = stats.recent(sorted(picked), window)
    if len(picked) < min_samples:
        return {"source": "uncalibrated", "samples": len(picked), "high": None, "basis": [r for _, r, _ in picked]}
    return {"source": "history", "samples": len(picked), "high": stats.maximum(v for _, _, v in picked), "basis": [r for _, r, _ in picked]}


# --- 誤差 ---


def _rank(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """順位相関（同順位は平均の順位）。2 組未満か分散が 0 なら None。"""
    if len(xs) < 2:
        return None
    rx, ry = _rank(xs), _rank(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    sy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return round(sxy / (sx * sy), 4) if sx and sy else None


def errors(estimates: list[dict], rows: list[dict], since: str | None = None, alive: Callable | None = None) -> tuple[list[dict], dict]:
    """予測と実績の組ごとの比（実績 ÷ 予測）と、比の中央値・A の順位相関。

    組は、課題を指す見積もりのうち、見積もりの後の実行で完了条件のフェーズが成功した課題の最新の見積もり。"""
    since_ts = er.ts(since) if since else None
    latest: dict[int, dict] = {}
    for e in estimates:
        if not isinstance(e.get("issue"), int):
            continue
        t = er.ts(e.get("at")) or 0
        if since_ts is not None and t < since_ts:
            continue
        if e["issue"] not in latest or (er.ts(latest[e["issue"]].get("at")) or 0) <= t:
            latest[e["issue"]] = e
    items = []
    for issue, e in sorted(latest.items()):
        got = er.issue_actuals(rows, e.get("done") or "実装", since=e.get("at"), alive=alive).get(issue)
        if not got or not got.completed:
            continue
        actual = {"A": got.a, "L": got.l, "C": got.c}
        predicted = {k: ((e.get("values") or {}).get(k) or {}).get("typical") for k in ("A", "L", "C")}
        ratio = {
            k: (round(actual[k] / predicted[k], 4) if isinstance(predicted[k], (int, float)) and predicted[k] and actual[k] is not None else None)
            for k in ("A", "L", "C")
        }
        items.append({"kind": "pair", "issue": issue, "estimate": e.get("id"), "predicted": predicted, "actual": actual, "ratio": ratio})
    metrics: dict = {"pairs": len(items)}
    for k in ("A", "L", "C"):
        metrics[f"ratio_median_{k}"] = stats.median([i["ratio"][k] for i in items if i["ratio"][k] is not None])
    both = [(i["predicted"]["A"], i["actual"]["A"]) for i in items if isinstance(i["predicted"]["A"], (int, float))]
    metrics["rank_correlation_A"] = spearman([p for p, _ in both], [a for _, a in both])
    return items, metrics
