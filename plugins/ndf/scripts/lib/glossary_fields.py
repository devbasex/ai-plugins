"""用語集の語の項目が持つ廃止した語・識別子の欄を読み、欄の形を確かめる（`glossary.py` から分けた。#3190）。

欄は `deprecated`（文字列の中で照合する語）・`deprecated_standalone`（前後が漢字の出現には当てない語）・`code`・
`deprecated_code` である。形は `skills/requirements-design/references/glossary-format.md` にある。
"""

from __future__ import annotations

import re

from glossary_words import is_kanji_edged

CODE_SHAPE = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*")


def deprecated_of(t: dict) -> list[str]:
    d = t.get("deprecated") or []
    return [w for w in d if isinstance(w, str)] if isinstance(d, list) else []


def deprecated_standalone_of(t: dict) -> list[str]:
    """単独で照合する廃止した語（前後が漢字の出現には当てない。1 文字でよい）。空でない文字列の要素だけを元の順で返す。"""
    d = t.get("deprecated_standalone")
    return [w for w in d if isinstance(w, str) and w] if isinstance(d, list) else []


def all_deprecated_of(t: dict) -> list[str]:
    """語の項目の廃止した語を 2 つの欄を合わせて返す（重なりは先に出た方を残す）。"""
    return list(dict.fromkeys(deprecated_of(t) + deprecated_standalone_of(t)))


def code_of(t: dict) -> str | None:
    c = t.get("code")
    return c if isinstance(c, str) and CODE_SHAPE.fullmatch(c) else None


def deprecated_code_of(t: dict) -> list[str]:
    d = t.get("deprecated_code") or []
    return [w for w in d if isinstance(w, str) and CODE_SHAPE.fullmatch(w)] if isinstance(d, list) else []


def deprecated_findings(i: int, t: dict, live_by_ctx: dict[str, set[str]], codes_by_ctx: dict[str, set[str]], hit) -> None:
    for w in deprecated_of(t):
        if len(w) < 2:
            hit("schema", w, f"terms[{i}] の廃止した語が 1 文字で照合できない")
        elif w in live_by_ctx.get(t["context"], set()):
            hit("schema", w, f"terms[{i}] の廃止した語が同じコンテキストの生きた語と重なる")
    if "code" in t and code_of(t) is None:
        hit("schema", str(t["code"]), f"terms[{i}] の code は英小文字の snake_case で書く（例: approval_gate）")
    if "deprecated_code" in t and (not isinstance(t["deprecated_code"], list) or len(deprecated_code_of(t)) != len(t["deprecated_code"])):
        hit("schema", t["term"], f"terms[{i}] の deprecated_code は英小文字の snake_case の文字列の配列で書く")
    for w in deprecated_code_of(t):
        if w in codes_by_ctx.get(t["context"], set()):
            hit("schema", w, f"terms[{i}] の廃止した識別子が同じコンテキストの生きた識別子と重なる")


def standalone_findings(i: int, t: dict, live_by_ctx: dict[str, set[str]], hit) -> None:
    """単独で照合する廃止した語の欄の形（空でない文字列の配列・先頭か末尾が漢字・同じコンテキストの生きた語と重ならない）。"""
    if "deprecated_standalone" not in t:
        return
    raw = t["deprecated_standalone"]
    if not isinstance(raw, list) or len(deprecated_standalone_of(t)) != len(raw):
        hit("schema", t["term"], f"terms[{i}] の deprecated_standalone は空でない文字列の配列で書く")
    for w in deprecated_standalone_of(t):
        if not is_kanji_edged(w):
            hit(
                "schema",
                w,
                f"terms[{i}] の単独で照合する廃止した語「{w}」は先頭か末尾を漢字にする（漢字で始まりも終わりもしない語は deprecated へ置く）",
            )
        if w in live_by_ctx.get(t["context"], set()):
            hit("schema", w, f"terms[{i}] の単独で照合する廃止した語が同じコンテキストの生きた語と重なる")
