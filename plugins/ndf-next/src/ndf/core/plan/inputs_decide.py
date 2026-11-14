"""プランを組む前の入力の確かめ（純粋な関数）。止める理由を文で返し、無ければ None か空を返す。

- 課題の本文の `## 受け入れ条件` の節が無い・空なら止める（#2246）
- `## 受け入れ条件の確認` の表で区分の無い条件があれば止める（#2357）
- 取り込んだ課題の受け入れ条件が取り込み先の本文から落ちていれば止める（#2274）
- 承認済みの課題の設計の結果を読めない・PR の単位が 2 つ以上なら止める（#2596・#2539）。実装を外したまま進めない
- 設計の結果の触るファイルのセルが、パスを挙げずに別の設計文書へのリンクで委ねていれば止める（#2453）。リンク先は
  たどらない（分割した文書の節に書式の定めが無い）。触るファイルは先頭の表のセルへ直に挙げる
- テストの照合は、サブディレクトリからの相対のパスで書いた名前も実在として扱う（#2553・#1811）
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import NamedTuple, Optional

HEADING = "## 受け入れ条件"
CHECKS_HEADING = "## 受け入れ条件の確認"
CHECKS_HEAD = ("条件", "区分")
KINDS = ("マージ前", "リリース後")
_BOX = re.compile(r"- \[[ xX]\](?=[ \t\r]|$)\s*(.*)")
_FENCE = re.compile(r"^(```|~~~)")
_CODE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+\.md)(?:#[^)]*)?\)")


class DesignResult(NamedTuple):
    """課題 1 つの設計の結果。`units` は PR の単位の名前（触るファイルのセルの先頭の括弧）。"""

    issue: int
    files: tuple[str, ...]
    units: tuple[str, ...] = ()
    delegated: tuple[str, ...] = ()


def files_cell(cell: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """触るファイルのセルから、バッククォートで囲んだパスと、触るファイルを委ねたリンク先の文書（`.md`）を返す。

    リンクの外のバッククォートだけをパスとして拾う。委ねた文書が 1 つでもあれば、`design_gaps` が止める（#2453）。
    """
    links = tuple(dict.fromkeys(_LINK.findall(cell)))
    files = tuple(dict.fromkeys(_CODE.findall(_LINK.sub("", cell))))
    return files, links


def _section(body: str, heading: str) -> Optional[list[str]]:
    """見出しの節の行（コードブロックの外）。節が無ければ None。見出しは行全体が一致するものだけ。"""
    lines = body.replace("\r\n", "\n").split("\n")
    heads = [i for i, line in enumerate(lines) if line.rstrip() == heading]
    if not heads:
        return None
    out, fenced = [], False
    for line in lines[heads[0] + 1 :]:
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced and re.match(r"^#{1,2} ", line):
            break
        if not fenced:
            out.append(line)
    return out


def criteria(body: str) -> list[str]:
    """受け入れ条件の節のチェックボックスの文（上から）。節が無ければ空。"""
    found = _section(body, HEADING)
    matches = [_BOX.match(line) for line in found] if found is not None else []
    return [m.group(1).strip() for m in matches if m]


def section_lack(body: str) -> Optional[str]:
    """受け入れ条件の節が無い・空なら理由。あれば None。"""
    if _section(body, HEADING) is None:
        return f"本文に {HEADING} の節が無い"
    if not criteria(body):
        return f"{HEADING} の節にチェックボックスの条件が無い"
    return None


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def check_kinds(body: str) -> dict[int, str]:
    """`## 受け入れ条件の確認` の表の {条件の番号: 区分}。表が無ければ空。区分の語が違う行は読まない。"""
    section = _section(body, CHECKS_HEADING)
    rows = [line for line in section if line.strip().startswith("|")] if section is not None else []
    if len(rows) < 2:
        return {}
    head = _cells(rows[0])
    if any(h not in head for h in CHECKS_HEAD):
        return {}
    num_at, kind_at = head.index(CHECKS_HEAD[0]), head.index(CHECKS_HEAD[1])
    out: dict[int, str] = {}
    for row in rows[2:]:
        cells = _cells(row)
        num = re.match(r"^(\d+)", cells[num_at]) if len(cells) > max(num_at, kind_at) else None
        if num and cells[kind_at] in KINDS:
            out[int(num.group(1))] = cells[kind_at]
    return out


def kinds_lack(body: str) -> list[int]:
    """区分の無い受け入れ条件の番号（1 から）。確認の表が無ければ、すべての条件が区分を欠く。"""
    kinds = check_kinds(body)
    return [n for n in range(1, len(criteria(body)) + 1) if n not in kinds]


def absorbed_missing(target_body: str, absorbed: Mapping[int, str]) -> dict[int, list[str]]:
    """取り込んだ課題の受け入れ条件のうち、取り込み先の本文の受け入れ条件に無い文を、課題ごとに返す（#2274）。"""
    present = set(criteria(target_body))
    out = {}
    for issue, body in absorbed.items():
        lost = [c for c in criteria(body) if c not in present]
        if lost:
            out[issue] = lost
    return out


def design_gaps(approved: Sequence[int], results: Mapping[int, Optional[DesignResult]]) -> list[str]:
    """承認済みの課題の設計の結果から、実装のプランを組めない理由を並べる（#2596・#2539・#2453）。"""
    reasons = []
    for issue in approved:
        found = results[issue] if issue in results else None
        if found is None:
            reasons.append(f"#{issue} の設計の結果を読めない（実装を外したまま進めない）")
        elif found.delegated:
            reasons.append(f"#{issue} の触るファイルを {'、'.join(found.delegated)} へ委ねている（設計の結果の表のセルへ直に挙げる）")
        elif len(set(found.units)) > 1:
            reasons.append(f"#{issue} の設計が PR を {len(set(found.units))} つに分けている（課題ごとに 1 本のプランで組めない）")
    return reasons


def merged_files(results: Sequence[DesignResult]) -> tuple[str, ...]:
    """主題で束ねた課題の、触るファイルの和（並びは最初に現れた順。#2056）。"""
    return tuple(dict.fromkeys(f for r in results for f in r.files))


def path_present(name: str, files: Sequence[str], subdir: str = "") -> bool:
    """テストの名前 `name` が実在のファイルを指すか。リポジトリの根からの相対・`subdir` からの相対・末尾の一致のどれか。"""
    name = name.strip().removeprefix("./")
    prefix = subdir.strip("/") + "/" if subdir.strip("/") else ""
    return any(f in (name, prefix + name) or f.endswith("/" + name) for f in files)
