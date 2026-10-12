"""承認資料の作り直しで残す節（#1834）。

`approval-facts` は自分の書く `## ` の節（`own`）だけを作り直し、前の承認資料のそれ以外の節（手で足した節・
`notes --approval` と `changed-plugins --approval` の節）を見出しと中身を変えずに残す。残す節は、前の資料で
直前にあった自分の節（錨）の後へ前と同じ順で戻す。錨が作り直し後に無ければ `## 同意を求めること` の前へ置く。
読むのと書くのは呼び手である（純粋な処理だけを持つ）。
"""

from __future__ import annotations

from dataclasses import dataclass

import md
from release_lib.names import h2_lines, next_h2

CONSENT_HEADING = "## 同意を求めること"


@dataclass(frozen=True)
class Kept:
    """残す節。lines は見出しの次から次の `## ` の前までの行。anchor は前の資料で直前にあった自分の節の見出し
    （無ければ None。H1 の直後を指す）。"""

    heading: str
    lines: tuple[str, ...]
    anchor: str | None


def _sections(lines: list[str]) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """(最初の `## ` より前の行, [(見出し, 中身の行)])。"""
    starts = h2_lines(lines)
    head = lines[: starts[0]] if starts else list(lines)
    return head, [(lines[at], lines[at + 1 : next_h2(lines, at)]) for at in starts]


def kept(old_lines: list[str], own) -> list[Kept]:
    """前の承認資料のうち、見出しが own に無い `## ` の節（資料の順）。H1 が無い・同じ `## ` の見出しが 2 つ
    あるときは ValueError（手の節を黙って失わないため、呼び手は書かずに止める）。"""
    if not any(h.level == 1 for h in md.headings("\n".join(old_lines))):
        raise ValueError("承認資料に H1 の見出しが無い")
    _, sections = _sections(old_lines)
    seen: set[str] = set()
    for heading, _ in sections:
        if heading in seen:
            raise ValueError(f"承認資料に同じ見出しが 2 つある: {heading}")
        seen.add(heading)
    out, anchor = [], None
    for heading, body in sections:
        if heading in own:
            anchor = heading
        else:
            out.append(Kept(heading, tuple(body), anchor))
    return out


def _block(k: Kept) -> list[str]:
    body = list(k.lines)
    if not body or body[-1] != "":
        body.append("")  # 次の見出しと詰まらないよう、間に空行を 1 つ置く
    return [k.heading, *body]


def insert_kept(new_lines: list[str], keep: list[Kept]) -> list[str]:
    """作り直した承認資料の行へ残す節を差し込む。"""
    if not keep:
        return list(new_lines)
    head, sections = _sections(new_lines)
    present = {h for h, _ in sections}
    lost = [_block(k) for k in keep if k.anchor is not None and k.anchor not in present]
    out = list(head)
    out += [line for k in keep if k.anchor is None for line in _block(k)]
    for heading, body in sections:
        if heading == CONSENT_HEADING:
            out += [line for b in lost for line in b]
            lost = []
        out += [heading, *body]
        out += [line for k in keep if k.anchor == heading for line in _block(k)]
    return out + [line for b in lost for line in b]
