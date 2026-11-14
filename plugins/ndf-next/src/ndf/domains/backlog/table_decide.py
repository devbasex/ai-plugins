"""マイルストーンの説明の `### 順位` の節の描画と置き換え（純粋）。

節は見出し `### 順位` から、次の同じか上の階層の見出し（`#`〜`###`）の手前までである。節の外の文字は変えない。
説明が長さの上限で拒まれたら、表の行を半分ずつ減らして書き直す（`next_limit`）。載せなかった行の件数は表の下に書く。
"""

from __future__ import annotations

import re
from typing import Any, Optional

from ndf.domains.backlog.model import SLOT_NONE, SLOT_SPLIT, SOURCE_DEPENDENTS, SOURCE_LABEL, SOURCE_RECORDS

HEADING = "### 順位"
_UPPER_HEADING = re.compile(r"^#{1,3} ", re.MULTILINE)


def _section_span(desc: str) -> Optional[tuple[int, int]]:
    """節の (始まり, 終わり) の位置。無ければ None。"""
    m = re.search(r"^" + re.escape(HEADING) + r"[ \t]*$", desc, re.MULTILINE)
    if m is None:
        return None
    nxt = _UPPER_HEADING.search(desc, m.end())
    return m.start(), nxt.start() if nxt else len(desc)


def replace_section(desc: str, table: str) -> str:
    """`### 順位` の節だけを `table` に置き換える。節が無ければ末尾へ足す。"""
    block = f"{HEADING}\n\n{table.rstrip()}\n"
    span = _section_span(desc)
    if span is None:
        return f"{desc.rstrip()}\n\n{block}" if desc.strip() else block
    start, end = span
    tail = desc[end:]
    return desc[:start] + block + ("\n" + tail if tail else "")


def outside(desc: str) -> str:
    """節の外の文字（節を除いた説明）。書く前後でこれが同じことを確かめる。"""
    span = _section_span(desc)
    return desc if span is None else desc[: span[0]] + desc[span[1] :]


def has_section(desc: str) -> bool:
    return _section_span(desc) is not None


def slot_text(slot: dict[str, Any]) -> str:
    if slot["status"] == SLOT_NONE:
        return f"なし（枠 {slot['budget']} を順位の先頭からの切り出しへ戻した）"
    if slot["status"] == SLOT_SPLIT:
        return f"#{slot['issue']} は分割が要る（Size {slot['size']} / 枠 {slot['budget']}）"
    if slot["slice"] == slot["issue"]:
        return f"#{slot['issue']}（Size {slot['size']} / 枠 {slot['budget']}）"
    return f"#{slot['slice']}（#{slot['issue']} の子、Size {slot['size']} / 枠 {slot['budget']}）"


def _row_line(r: dict[str, Any]) -> str:
    issue = f"#{r['number']}" + ("（判断待ち）" if r["waiting_decision"] else "")
    ubv = f"{r['ubv']}" + ("（ラベル）" if r["sources"]["ubv"] == SOURCE_LABEL else "")
    rr = f"{r['rr_oe']}" + ("（被依存）" if r["sources"]["rr_oe"] == SOURCE_DEPENDENTS else "")
    size = f"{r['size']}" + ("（見積もり）" if r["sources"].get("size") == SOURCE_RECORDS else "")
    return f"| {r['rank']} | {issue} | {ubv} | {r['tc']} | {rr} | {size} | {r['cod']} | {r['value']:.1f} |"


def _tail(ms: dict[str, Any], shown: int) -> list[str]:
    tail = []
    if len(ms["rows"]) > shown:
        tail.append(f"以下 {len(ms['rows']) - shown} 件は表に載せていない")
    if ms["large_slot"]:
        tail.append("大きい課題の枠: " + slot_text(ms["large_slot"]))
    q = ms["quality_slot"] if "quality_slot" in ms else None
    if q is not None:
        got = " ".join(f"#{n}" for n in q["issues"]) or "なし"
        budget = "容量なし" if q["budget"] is None else f"枠 {q['budget']}"
        tail.append(f"品質の枠: {got}（ラベル {q['label']}、Size の和 {q['size_sum']} / {budget}、最低 {q['min']} 件）")
    b = ms["boundary"]
    if b and b.get("empty"):
        tail.append(f"切り出しの境界: 無し（枠で容量 {b['capacity']} を使い切った）")
    elif b:
        over = "。1 件で容量を超える" if b.get("over") else ""
        tail.append(f"切り出しの境界: #{b['number']}（Size の和 {b['size_sum']} / 容量 {b['capacity']}{over}）")
    return tail


def render(ms: dict[str, Any], limit: Optional[int] = None) -> str:
    """マイルストーン 1 本の `### 順位` の表。`limit` を渡すと上から `limit` 行だけ載せる。"""
    rows = ms["rows"] if limit is None else ms["rows"][:limit]
    lines = ["| 順位 | 課題 | UBV | TC | RR/OE | Size | CoD | 順位の値 |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    lines += [_row_line(r) for r in rows]
    tail = _tail(ms, len(rows))
    return "\n".join([*lines, "", *tail] if tail else lines)


def next_limit(rows: int, limit: Optional[int]) -> Optional[int]:
    """長さで拒まれた後に載せる行の数（半分ずつ減らす）。0 行でも拒まれたら None（もう減らせない）。"""
    if limit == 0:
        return None
    return rows // 2 if limit is None else limit // 2
