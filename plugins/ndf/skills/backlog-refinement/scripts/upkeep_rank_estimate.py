"""upkeep_rank_estimate.py: 棚卸しの順位へ見積もりの記録を重ねる（#1856）。GitHub を呼ばない。

課題ごとに最新の見積もりの記録を読み、A の見込みを Size 1 の基準との比で尺度へ丸めて Size にする（I7）。
丸められない課題（記録が無い・A が実績でない・基準が無い）は、手順 2B で付けた段階のままにする。
比が尺度の最大を超えた課題は「尺度を超える」とし、順位の値は丸める前の比で計算する。集計と丸めは
`scripts/lib/estimate_predict.py` が持ち、ここは記録を読んで順位の入力へ写すだけである。
"""

from __future__ import annotations

from dataclasses import replace

OVER_SCALE = "尺度を超える"
VALUE_KEYS = ("A", "H", "L", "C")


def plain_row() -> dict:
    """見積もりの記録が無い課題の行の追加の列。"""
    return {"size_source": "付けた段階", **{k.lower(): None for k in VALUE_KEYS}, "size_basis": []}


def _latest(estimates: list[dict], numbers, ts) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for e in estimates:
        n = e.get("issue")
        if isinstance(n, int) and n in numbers and (n not in out or (ts(out[n].get("at")) or 0) <= (ts(e.get("at")) or 0)):
            out[n] = e
    return out


def estimate_overlay(root, numbers, scale) -> dict[int, dict]:
    """{課題: {"size": 段階 | "尺度を超える" | None, "ratio": 比 | None, "row": 追加の列}}。記録が無ければ {}。"""
    import estimate_predict as ep
    import estimate_records as er

    rec = er.estimate_dir(root)
    latest = _latest(er.read_estimates(rec), set(numbers), er.ts)
    if not latest:
        return {}
    rows, decl = er.read_actuals(rec), er.load_estimate_decl(root)
    units: dict[str, float | None] = {}
    out = {}
    for n, e in latest.items():
        vals = e.get("values") or {}
        a = vals.get("A") or {}
        done = e.get("done") or "実装"
        if done not in units:
            units[done] = ep.size_unit([x.a for x in er.issue_actuals(rows, done).values() if x.completed], decl)
        unit = units[done]
        size = ratio = None
        if a.get("mark") == "実績" and isinstance(a.get("typical"), (int, float)) and unit:
            size, ratio = ep.to_size(a["typical"], unit, scale), round(a["typical"] / unit, 3)
        row = {
            "size_source": "見積もり" if size is not None else "付けた段階",
            **{k.lower(): {"typical": (vals.get(k) or {}).get("typical"), "mark": (vals.get(k) or {}).get("mark")} for k in VALUE_KEYS},
            "size_basis": sorted({b["issue"] for b in a.get("basis") or [] if isinstance(b, dict) and isinstance(b.get("issue"), int)}),
        }
        out[n] = {"size": size, "ratio": ratio, "row": row}
    return out


def apply_overlay(ests: dict, overlay: dict) -> dict:
    """見積もりの Size を記録から決めた段階へ置き換える。尺度を超える課題は丸める前の比を Size の値にする。"""
    out = {}
    for n, e in ests.items():
        o = overlay.get(n) or {}
        if o.get("size") == OVER_SCALE:
            out[n] = replace(e, steps={**e.steps, "size": o["ratio"]})
        elif isinstance(o.get("size"), int):
            out[n] = replace(e, steps={**e.steps, "size": o["size"]})
        else:
            out[n] = e
    return out


def is_over(board, number: int) -> bool:
    return ((board.sizes or {}).get(number) or {}).get("size") == OVER_SCALE


def cells(row: dict) -> list[str]:
    out = []
    for k in VALUE_KEYS:
        v = row.get(k.lower()) or {}
        if v.get("mark") == "実績" and isinstance(v.get("typical"), (int, float)):
            out.append(f"${v['typical']:.2f}" if k == "C" else f"{v['typical'] / 60:.0f} 分")
        else:
            out.append("未校正" if v.get("mark") == "未校正" else "—")
    return out
