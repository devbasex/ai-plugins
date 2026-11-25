"""`ndf document-rebuild gate` の判断の部品（#4446）。ファイル・git・GitHub を触らない。

承認資料のマーカーの外し方・変わった段落の切り出し・PR の本文の直しの見分け・worker の記録の確かめ・前に残った文の引き継ぎ・
読ませた行の組み立てを持つ。
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Optional

from ndf import contracts
from ndf.domains.records import reading

RESULT_SCHEMA = contracts.schema_path("io/document-rebuild.result")
# worker の記録の verdict（読み手役の pass・fail と、立てられなかった not_run。run の worker も同じ値を使う）
PASS, FAIL = contracts.schema_enum(RESULT_SCHEMA, "/$defs/reader/properties/checks/items/properties/verdict")
NOT_RUN = "not_run"
_SPACE = re.compile(r"\s+")
_PARAGRAPH = re.compile(r"\n\s*\n")


label = reading.target_label


def strip_marks(text: str, expected: str) -> str:
    """末尾の 1 組のマーカーの範囲が `expected` と同じならそれを除き、違えばマーカーの行だけを消す（設計書の手順 2）。"""
    body, cut = reading.split_section(text, expected)
    if cut:
        return body
    return "".join(line for line in text.splitlines(keepends=True) if line.strip() not in (reading.BEGIN, reading.END))


def changed_part(old: str, new: str) -> str:
    """`new` のうち `old` から変わった段落と、その前後の段落（設計書の「一部だけ読ませるとき」）。"""
    a = [p for p in _PARAGRAPH.split(old) if p.strip()]
    b = [p for p in _PARAGRAPH.split(new) if p.strip()]
    keep: set[int] = set()
    for tag, _, _, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag != "equal":
            keep.update(range(max(j1 - 1, 0), min(j2 + 1, len(b))))
    return "\n\n".join(b[i] for i in sorted(keep)) + "\n"


def body_edited(body: str, final: str) -> bool:
    """承認資料の最終版で、PR の本文から写した行が消えたか変わったか（差分に `-` の行がある）。行を足しただけなら偽。"""
    return any(line.startswith("- ") for line in difflib.ndiff(body.splitlines(), final.splitlines()))


def question_text(questions: Sequence[Mapping[str, Any]]) -> str:
    """読み手役に読ませる質問の文と選択肢。"""
    q = questions[0]
    return "\n".join([q["question"], "", *(f"- {o['label']}: {o['description']}" for o in q["options"])]) + "\n"


def quotes_of(check: Mapping[str, Any]) -> list[dict[str, str]]:
    """確かめ 1 件の、判断や作業を誤らせる文の引用と理由。形が違えば空。"""
    raw = check["quotes"] if "quotes" in check else None
    if not isinstance(raw, list) or not raw or not all(_is_quote(q) for q in raw):
        return []
    return [{"quote": q["quote"], "why": q["why"]} for q in raw]


def _is_quote(q: Any) -> bool:
    return isinstance(q, Mapping) and isinstance(q.get("quote"), str) and bool(q["quote"].strip()) and isinstance(q.get("why"), str)


def record_gap(rec: Any) -> Optional[str]:
    """worker の対象 1 件の記録の欠けの理由。欠けが無ければ None（`not_run` は欠けとしない。呼び手が止める）。"""
    if not isinstance(rec, Mapping) or rec.get("verdict") not in (PASS, FAIL, NOT_RUN):
        return f"verdict が {PASS}・{FAIL}・{NOT_RUN} のどれでもない"
    if rec["verdict"] == NOT_RUN:
        return None
    checks = rec.get("checks")
    if not isinstance(checks, list) or len(checks) not in (1, 2) or not all(_is_check(c) for c in checks):
        return "checks が、verdict（pass・fail）と hash を持つ 1 つか 2 つの確かめの並びでない"
    if checks[-1]["verdict"] != rec["verdict"] or (len(checks) == 2 and checks[0]["verdict"] != FAIL):
        return "checks の並びと verdict が合わない"
    if rec["verdict"] == FAIL and (len(checks) != 2 or not quotes_of(checks[-1])):
        return "fail は 2 回の確かめで終え、2 回目に quotes（quote・why）を書く"
    return None


def _is_check(c: Any) -> bool:
    return isinstance(c, Mapping) and c.get("verdict") in (PASS, FAIL) and isinstance(c.get("hash"), str) and bool(c["hash"])


def _squeezed(text: str) -> str:
    return _SPACE.sub(" ", text).strip()


def carried_quotes(
    prev: Iterable[Mapping[str, Any]],
    contents: Mapping[str, str],
    read_parts: Mapping[str, Optional[str]],
    raised: Mapping[str, Sequence[Mapping[str, str]]],
) -> dict[str, list[dict[str, str]]]:
    """前の確認で残った文のうち引き継ぐもの（設計書の「前に残った文の状態」と、計画で決めたこと (3)）。

    `contents` は対象の今の全体、`read_parts` は今回読ませた対象の名前から読ませた部分（全体を読ませたなら None）、`raised` は今回も
    挙がった引用。引用は連続する空白を 1 つにそろえて比べる。
    """
    out: dict[str, list[dict[str, str]]] = {}
    for row in prev:
        name = row["name"]
        seen = {_squeezed(q["quote"]) for q in [*(raised[name] if name in raised else ()), *(out[name] if name in out else ())]}
        for q in row["quotes"]:
            quote = _squeezed(q["quote"])
            if name not in contents or quote not in _squeezed(contents[name]) or quote in seen or _read_again(name, quote, read_parts):
                continue
            out.setdefault(name, []).append(dict(q))
            seen.add(quote)
    return out


def _read_again(name: str, quote: str, read_parts: Mapping[str, Optional[str]]) -> bool:
    """引用した文を今回読ませたか（全体を読ませたか、読ませた部分に含まれる）。"""
    if name not in read_parts:
        return False
    part = read_parts[name]
    return part is None or quote in _squeezed(part)


def rows_of_record(name: str, rec: Mapping[str, Any]) -> list[dict[str, Any]]:
    """worker の記録 1 件を、回ごとの読ませた行にする。2 回目の不合格は remained（引用付き）。"""
    out: list[dict[str, Any]] = []
    for i, check in enumerate(rec["checks"], 1):
        row: dict[str, Any] = {"name": name, "reader": reading.reader_of(name), "round": i, "hash": check["hash"]}
        if check["verdict"] == PASS:
            row["verdict"] = "passed"
        elif i == 2:
            row.update(verdict=reading.REMAINED, quotes=quotes_of(check))
        else:
            row["verdict"] = "failed"
        out.append(row)
    return out


def doc_rows_of(
    found: Mapping[str, Mapping[str, Any]], carried: Mapping[str, Sequence[Mapping[str, str]]], hashes: Mapping[str, str]
) -> list[dict[str, Any]]:
    """今回の確認の読ませた行。引き継いだ残った文は、対象の今の中身のハッシュで remained の行にする（設計書の手順 9 の 1）。"""
    rows = [row for name, rec in found.items() for row in rows_of_record(name, rec)]
    for name, quotes in carried.items():
        last = next((r for r in reversed(rows) if r["name"] == name and r["verdict"] == reading.REMAINED), None)
        if last is not None and last["hash"] == hashes[name]:
            last["quotes"] = [*last["quotes"], *quotes]
        else:
            rows.append(
                {
                    "name": name,
                    "reader": reading.reader_of(name),
                    "round": 2,
                    "verdict": reading.REMAINED,
                    "hash": hashes[name],
                    "quotes": list(quotes),
                }
            )
    return rows


comment_has = reading.comment_has


def remained_items(prefix: str, remained: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """2 回目にも残った文を、結果の最上位の `items`（`<prefix>-<番号>`）にする。"""
    pairs = [(r, q) for r in remained for q in r["quotes"]]
    return [
        {"id": f"{prefix}-{i}", "doc": r["name"], "text": f"{label(r['name'])}: {q['quote']}（{q['why']}）"}
        for i, (r, q) in enumerate(pairs, 1)
    ]
