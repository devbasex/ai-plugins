"""upkeep_depth_diff.py: 振り返りと差分の棚卸しの起動の判定と、振り返りの範囲の組み立て（#2914 の決定 2・3）。

`upkeep.py depth due diff` と `depth scope` が使う。記録（`depths.jsonl`）の読み書きは `upkeep_depth.py` が持ち、ここは
行の列から判定と範囲を組むだけである。範囲に入ったかは、後の `due` の行の `values.scope.laps` にそのループの 1 回が
載っているかで決める（不変条件 I1）。
"""

from __future__ import annotations


def _lap_key(r: dict) -> tuple:
    return (r.get("loop"), r.get("lap"))


def _diff_rows(rows: list[dict], event: str) -> list[tuple[int, dict]]:
    return [(i, r) for i, r in enumerate(rows) if r.get("depth") == "diff" and r.get("event") == event]


def pending_diffs(rows: list[dict]) -> list[tuple[int, dict]]:
    """まだどの範囲にも入っていない開発ループの diff の `not_due` の行（古い順・同じループの 1 回は最後の行。I1）。"""
    taken = {_lap_key(x) for _, r in _diff_rows(rows, "due") for x in ((r.get("values") or {}).get("scope") or {}).get("laps") or []}
    out = {_lap_key(r): (i, r) for i, r in _diff_rows(rows, "not_due") if r.get("loop") is not None and _lap_key(r) not in taken}
    return list(out.values())


def diff_due(rows: list[dict], cfg: dict, cur: dict, manual=False) -> tuple[list, dict]:
    """diff の (起動した条件, 判定の値)。起動するときは値に振り返りの範囲（`scope`）を置く。`cur` は今の回の値。"""
    every, key = cfg["diff"]["every"], _lap_key(cur)
    if cur.get("loop") is not None:  # 同じループの 1 回の打ち直しは、前の判定の範囲をそのまま使う
        again = next((r for _, r in reversed(_diff_rows(rows, "due")) if _lap_key(r) == key), None)
        if again:
            return list(again.get("trigger") or []), dict(again.get("values") or {})
    pend = [(i, r) for i, r in pending_diffs(rows) if _lap_key(r) != key]
    n = len(pend) + 1
    if cur.get("loop") is None:  # ループの外は今のスプリントだけを扱い、見送った回を消費しない
        trig, pend = ["no_loop"], []
    else:
        trig = (["manual"] if manual else []) + (["count"] if n >= every else [])
    if not trig:
        return [], {"count": n, "every": every, "next_in": every - n}
    first = pend[0][0] if pend else len(rows)
    since = next((r.get("at") for i, r in reversed(_diff_rows(rows, "due")) if i < first), None)
    laps = [{k: r.get(k) for k in ("loop", "lap", "sprint", "issues")} for _, r in pend] + [cur]
    issues = sorted({x for lap in laps for x in lap.get("issues") or []})
    return trig, {"count": n, "every": every, "scope": {"laps": laps, "issues": issues, "since": since}}
