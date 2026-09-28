"""upkeep_rank_table.py: マイルストーンの説明の `### 順位` の節の描画と解析、前回の表との差分、並列の組の依存（#1429）。

説明の節の外の文字は変えない（I6）。式と順序は `upkeep_rank.py` が持ち、ここは表の形だけを扱う。
"""

from __future__ import annotations

import re

import gh_sections
from upkeep_rank_config import COLUMN_NAMES, COLUMNS, HEADING, SLOT_NONE, SLOT_SPLIT

# ---------------- 前回との差分 ----------------


def rank_changes(milestones, previous, placement, excluded) -> list:
    """前回の `### 順位` の表と比べた変化（AC12）。前回の表が無いマイルストーンは全件を new にする。"""
    out = []
    for ms in milestones:
        prev = previous.get(ms["title"])
        prev_rows = {r["number"]: r for r in (prev or {}).get("rows", [])}
        now = {r["number"] for r in ms["rows"]}
        for r in ms["rows"]:
            p = prev_rows.get(r["number"])
            if p is None:
                kind = "unknown_previous" if prev and prev.get("truncated") else "new"
                out.append({"milestone": ms["title"], "number": r["number"], "kind": kind, "from_rank": None,
                            "to_rank": r["rank"], "columns": [], "reason": "前回の表に無い"})  # fmt: skip
                continue
            cols = [f"{COLUMN_NAMES[c]} {p[c]}→{r[c]}" for c in COLUMNS if p[c] != r[c]]
            if p["rank"] == r["rank"]:
                continue
            kind = "up" if r["rank"] < p["rank"] else "down"
            reason = "段階の変化: " + "・".join(cols) if cols else "ほかの課題の変化（入った・外れた・段階の変化・依存）"
            out.append({"milestone": ms["title"], "number": r["number"], "kind": kind, "from_rank": p["rank"],
                        "to_rank": r["rank"], "columns": cols, "reason": reason})  # fmt: skip
        for n, p in prev_rows.items():
            if n in now:
                continue
            if n in excluded:
                reason = f"順位から外れた（{excluded[n]}）"
            elif n not in placement:
                reason = "open でない"
            elif placement.get(n) is None:
                reason = "マイルストーンから外れた"
            else:
                reason = f"{placement[n]} へ移った"
            out.append({"milestone": ms["title"], "number": n, "kind": "removed", "from_rank": p["rank"],
                        "to_rank": None, "columns": [], "reason": reason})  # fmt: skip
    return out


# ---------------- `### 順位` の節 ----------------


def replace_rank_section(description: str, table: str) -> str:
    """`### 順位` の節だけを table に置き換える。無ければ末尾へ足す（I6）。節の外の文字は変えない（`gh_sections`）。"""
    return gh_sections.replace_section(description or "", HEADING, table)


def outside_unchanged(before: str, after: str) -> bool:
    """`### 順位` の節の外が変わっていないか（I6 の確かめ）。"""
    return replace_rank_section(before, "") == replace_rank_section(after, "")


def render_rank_table(ms: dict, limit: int | None = None) -> str:
    """マイルストーンの `### 順位` の節を描く。limit を渡すと上から limit 行だけ載せ、残りの件数を書く（決定 15）。"""
    rows = ms["rows"] if limit is None else ms["rows"][:limit]
    lines = ["| 順位 | 課題 | UBV | TC | RR/OE | Size | CoD | 順位の値 |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        issue = f"#{r['number']}" + ("（判断待ち）" if r["waiting_decision"] else "")
        ubv = f"{r['ubv']}" + ("（ラベル）" if r["sources"]["ubv"] == "label" else "")
        rr = f"{r['rr_oe']}" + ("（被依存）" if r["sources"]["rr_oe"] == "dependents" else "")
        lines.append(f"| {r['rank']} | {issue} | {ubv} | {r['tc']} | {rr} | {r['size']} | {r['cod']} | {r['value']:.1f} |")
    tail = []
    if limit is not None and len(ms["rows"]) > limit:
        tail.append(f"以下 {len(ms['rows']) - limit} 件は表に載せていない")
    slot = ms.get("large_slot")
    if slot:
        tail.append("大きい課題の枠: " + slot_text(slot))
    b = ms.get("boundary")
    if b:
        over = "。1 件で容量を超える" if b["over"] else ""
        tail.append(f"切り出しの境界: #{b['number']}（Size の和 {b['size_sum']} / 容量 {b['capacity']}{over}）")
    if tail:
        lines += ["", *tail]
    return "\n".join(lines)


def slot_text(slot: dict) -> str:
    if slot["status"] == SLOT_NONE:
        return f"なし（枠 {slot['budget']} を順位の先頭からの切り出しへ戻した）"
    if slot["status"] == SLOT_SPLIT:
        return f"#{slot['issue']} は分割が要る（Size {slot['size']} / 枠 {slot['budget']}）"
    if slot["slice"] == slot["issue"]:
        return f"#{slot['issue']}（Size {slot['size']} / 枠 {slot['budget']}）"
    return f"#{slot['slice']}（#{slot['issue']} の子、Size {slot['size']} / 枠 {slot['budget']}）"


_ROW = re.compile(
    r"^\|\s*(\d+)\s*\|\s*#(\d+)(（判断待ち）)?\s*\|\s*(\d+)(（ラベル）)?\s*\|\s*(\d+)\s*\|\s*(\d+)(（被依存）)?\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|"
)


def parse_rank_table(description: str):
    """説明の `### 順位` の表を読む。無ければ None。{"rows": [...], "truncated": 載せていない件数}。"""
    sec = gh_sections.get_section(description or "", HEADING)
    if sec is None:
        return None
    rows = []
    for line in sec.splitlines():
        m = _ROW.match(line.strip())
        if m:
            rows.append(
                {
                    "rank": int(m.group(1)),
                    "number": int(m.group(2)),
                    "waiting_decision": bool(m.group(3)),
                    "ubv": int(m.group(4)),
                    "ubv_label": bool(m.group(5)),
                    "tc": int(m.group(6)),
                    "rr_oe": int(m.group(7)),
                    "rr_oe_dependents": bool(m.group(8)),
                    "size": int(m.group(9)),
                    "cod": int(m.group(10)),
                }
            )
    m = re.search(r"以下 (\d+) 件は表に載せていない", sec)
    return {"rows": rows, "truncated": int(m.group(1)) if m else 0}


# ---------------- 並列の組 ----------------


_GROUP_ROW = re.compile(r"^\|\s*(\d+)\s*\|([^|]*)\|[^|]*\|([^|]*)\|")


def parallel_group_edges(description: str) -> list:
    """マイルストーンの説明の `### 並列の組（見込み）` の依存の列から辺を作る。

    依存の列が括弧で課題の番号を書いていればその番号に、書いていなければ指す組の課題すべてに依存する。
    """
    sec = gh_sections.get_section(description or "", "### 並列の組（見込み）")
    if sec is None:
        return []
    groups, deps = {}, {}
    for line in sec.splitlines():
        m = _GROUP_ROW.match(line.strip())
        if not m:
            continue
        g = int(m.group(1))
        groups[g] = [int(x) for x in re.findall(r"#(\d+)", m.group(2))]
        deps[g] = m.group(3)
    edges = []
    for g, cell in deps.items():
        if cell.strip() in ("", "なし"):
            continue
        named = [int(x) for x in re.findall(r"#(\d+)", cell)]
        targets = named or [n for ref in re.findall(r"(?<!#)\b(\d+)\b", cell) for n in groups.get(int(ref), [])]
        edges += [(a, b) for a in groups[g] for b in targets]
    return edges
