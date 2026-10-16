"""project_mvv_body.py: プロジェクト MVV の本文（`.ndf/mvv.md`）の解析（#1366）。標準ライブラリだけで書く。

見出しの語（`## Mission` など。括弧の前まで）・Value の番号つきの箇条・固有の操作の表の `P<番号>` を読み、
形の誤り（I4）と版どうしの差分を返す。本文を書き換えない。
"""

from __future__ import annotations

import re

# 本文の見出し（I4）。Mission / Vision / Value は必須、固有の操作の見出しは任意
REQUIRED_SECTIONS = ("Mission", "Vision", "Value")

OPERATIONS_HEADING = "必ず人の承認が要る操作"

# 共通原則の写しに当たる見出し（本文に持てない）
PRINCIPLE_HEADINGS = ("上位の原則", "優先順位")

TABLE_ID_RE = re.compile(r"^\|\s*([CPR])(\d+)\s*\|")


def body_sections(text: str) -> dict[str, list[str]]:
    """`## ` の見出しの語 → その節の行（見出しは含まない）。見出しの語は括弧の前まで。"""
    found: dict[str, list[str]] = {}
    cur: list[str] | None = None
    for ln in text.splitlines():
        if ln.startswith("## "):
            title = re.split(r"[（(]", ln[3:].strip(), maxsplit=1)[0].strip()
            cur = found.setdefault(title, [])
            continue
        if ln.startswith("# "):
            cur = None
            continue
        if cur is not None:
            cur.append(ln)
    return found


def _table_ids(text: str) -> list[tuple[str, int]]:
    return [(m.group(1), int(m.group(2))) for ln in text.splitlines() if (m := TABLE_ID_RE.match(ln.strip()))]


def _section(secs: dict[str, list[str]], word: str) -> list[str] | None:
    return next((v for k, v in secs.items() if k == word or k.startswith(word)), None)


def item_ids(text: str) -> list[str]:
    """本文の項目の番号。Mission / Vision と、Value の番号つきの箇条（Value N）と、固有の操作の表の P の番号。"""
    secs = body_sections(text)
    ids = [k for k in ("Mission", "Vision") if _section(secs, k) is not None]
    for ln in _section(secs, "Value") or []:
        m = re.match(r"^(\d+)\.\s", ln)
        if m:
            ids.append(f"Value {int(m.group(1))}")
    ops = _section(secs, OPERATIONS_HEADING)
    if ops is not None:
        ids += [f"P{n}" for k, n in _table_ids("\n".join(ops)) if k == "P"]
    return ids


def shape_problems(text: str) -> list[dict]:
    """本文の形の誤り（I4）。`[{"item", "reason"}]`。空なら形は正しい。"""
    secs = body_sections(text)
    return [
        *_missing_sections(secs),
        *_value_shape(secs),
        *_principle_copies(secs),
        *_foreign_table_ids(text),
        *_operations_shape(secs),
    ]


def _missing_sections(secs) -> list[dict]:
    return [{"item": word, "reason": f"`## {word}` の見出しが無い"} for word in REQUIRED_SECTIONS if _section(secs, word) is None]


def _value_shape(secs) -> list[dict]:
    value = _section(secs, "Value")
    if value is not None and not any(re.match(r"^\d+\.\s", ln) for ln in value):
        return [{"item": "Value", "reason": "`## Value` に番号つきの箇条（`1. ...`）が無い"}]
    return []


def _principle_copies(secs) -> list[dict]:
    return [
        {"item": "priority" if word == "優先順位" else "principle", "reason": f"共通原則の写し（`## {word}` の節）を持てない"}
        for word in PRINCIPLE_HEADINGS
        if any(k.startswith(word) for k in secs)
    ]


def _foreign_table_ids(text: str) -> list[dict]:
    out = []
    for k, n in _table_ids(text):
        if k == "C":
            out.append({"item": f"C{n}", "reason": f"共通原則の操作（C{n} の行）を本文へ写せない。固有の操作は P の番号で書く"})
        if k == "R":
            out.append({"item": f"R{n}", "reason": "R の番号はスプリント MVV のものである。固有の操作は P の番号で書く"})
    return out


def _operations_shape(secs) -> list[dict]:
    ops = _section(secs, OPERATIONS_HEADING)
    if ops is not None:
        others = [f"{k}{n}" for k, n in _table_ids("\n".join(ops)) if k != "P"]
        if not any(k == "P" for k, _ in _table_ids("\n".join(ops))) and not others:
            return [{"item": OPERATIONS_HEADING, "reason": f"`## {OPERATIONS_HEADING}` に `| P1 | ... |` の行が無い"}]
    return []


def item_texts(text: str) -> dict[str, str]:
    """項目の番号 → その項目の文（差分の計算に使う）。"""
    secs = body_sections(text)
    out = {}
    for k in ("Mission", "Vision"):
        s = _section(secs, k)
        if s is not None:
            out[k] = "\n".join(s).strip()
    cur = None
    for ln in _section(secs, "Value") or []:
        m = re.match(r"^(\d+)\.\s", ln)
        if m:
            cur = f"Value {int(m.group(1))}"
            out[cur] = ln
        elif cur and ln.strip():
            out[cur] += "\n" + ln
    ops = _section(secs, OPERATIONS_HEADING) or []
    for ln in ops:
        m = TABLE_ID_RE.match(ln.strip())
        if m and m.group(1) == "P":
            out[f"P{int(m.group(2))}"] = ln.strip()
    return out


def changes(old: str, new: str) -> list[dict]:
    """前の版との差分を項目の単位で並べる。"""
    a, b = item_texts(old), item_texts(new)
    out = []
    for k in b:
        if k not in a:
            out.append({"item": k, "kind": "added"})
        elif a[k] != b[k]:
            out.append({"item": k, "kind": "changed"})
    out += [{"item": k, "kind": "removed"} for k in a if k not in b]
    return out
