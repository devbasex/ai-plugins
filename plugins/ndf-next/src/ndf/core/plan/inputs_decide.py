"""プランを組む前の入力の確かめ（純粋な関数）。止める理由を文で返し、無ければ None か空を返す。

- 課題の本文の `## 受け入れ条件` のセクションが無い・空なら止める（#2246）
- `## 受け入れ条件の確認` の表で区分の無い条件があれば止める（#2357）
- 統合した課題の受け入れ条件が統合先の本文から落ちていれば止める（#2274）
- 承認済みの課題の設計の結果を読めない・PR の単位が 2 つ以上なら止める（#2596・#2539）。実装を外したまま進めない
- 設計の結果の触るファイルのセルが、パスを挙げずに別の設計文書へのリンクで委ねていれば止める（#2453）。リンク先は
  たどらない（分割した文書のセクションに書式の定めが無い）。触るファイルは先頭の表のセルへ直に挙げる
- テストの検証は、サブディレクトリからの相対のパスで書いた名前も実在として扱う（#2553・#1811）
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import NamedTuple, Optional

HEADING = "## 受け入れ条件"
CHECKS_HEADING = "## 受け入れ条件の確認"
CHECKS_HEAD = ("条件", "区分")
KINDS = ("マージ前", "リリース後")
_BOX = re.compile(r"- \[[ xX]\](?=[ \t\r]|$)\s*(.*)")
_FENCE = re.compile(r"^(```|~~~)")
# セクションの終わり（`#` か `##` の見出し）
SECTION_END = re.compile(r"^#{1,2} ")
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


def body_lines(body: str) -> list[str]:
    """本文を行へ分ける（CRLF は LF とみなす）。"""
    return body.replace("\r\n", "\n").split("\n")


def heading_at(lines: Sequence[str], heading: str) -> Optional[int]:
    """行全体（行末の空白を除く）が `heading` と一致する最初の行の添字。無ければ None。"""
    return next((i for i, line in enumerate(lines) if line.rstrip() == heading), None)


def section_lines(body: str, heading: str) -> Optional[list[str]]:
    """見出しのセクションの行（コードブロックの外）。セクションが無ければ None。見出しは行全体が一致するものだけ。"""
    lines = body_lines(body)
    at = heading_at(lines, heading)
    if at is None:
        return None
    out = []
    for _, line in outside_fences(lines[at + 1 :]):
        if SECTION_END.match(line):
            break
        out.append(line)
    return out


def outside_fences(lines: Iterable[str], fence: re.Pattern[str] = _FENCE) -> Iterator[tuple[int, str]]:
    """囲み（`fence` に当たる行で開いて閉じる）の外の行を `(添字 0 始まり, 行)` で返す。囲みの行そのものは返さない。"""
    fenced = False
    for i, line in enumerate(lines):
        if fence.match(line):
            fenced = not fenced
            continue
        if not fenced:
            yield i, line


def criteria(body: str) -> list[str]:
    """受け入れ条件のセクションのチェックボックスの文（上から）。セクションが無ければ空。"""
    found = section_lines(body, HEADING)
    matches = [_BOX.match(line) for line in found] if found is not None else []
    return [m.group(1).strip() for m in matches if m]


def section_lack(body: str) -> Optional[str]:
    """受け入れ条件のセクションが無い・空なら理由。あれば None。"""
    if section_lines(body, HEADING) is None:
        return f"本文に {HEADING} のセクションが無い"
    if not criteria(body):
        return f"{HEADING} のセクションにチェックボックスの条件が無い"
    return None


# 表のセルの区切り（`\|` は区切りでなくセルの中の `|`）と、セルの中の改行の書き方
_CELL_SEP = re.compile(r"(?<!\\)\|")
_CELL_BREAK = "<br>"


def cell(value: str) -> str:
    """値を表の 1 つのセルへ書ける形にする（`|` は `\\|`、改行は `<br>`）。`cells` が元の値へ戻す。"""
    return value.replace("\r\n", "\n").replace("|", "\\|").replace("\n", _CELL_BREAK)


def cells(line: str) -> list[str]:
    """表の 1 行のセル（`cell` で書いた `\\|` と `<br>` は元の `|` と改行へ戻す）。"""
    inner = line.strip().removeprefix("|")
    inner = inner[:-1] if inner.endswith("|") and not inner.endswith("\\|") else inner
    return [c.strip().replace("\\|", "|").replace(_CELL_BREAK, "\n") for c in _CELL_SEP.split(inner)]


def check_rows(body: str, head: Sequence[str]) -> list[tuple[int, list[str]]]:
    """`## 受け入れ条件の確認` の表のデータ行を、(条件の番号, `head` の列の順に並べたセル) で上から返す。

    `head` の先頭は番号の列。表の最初の行を見出し、次の行を区切りとして読む。セクション・表が無いか、見出しに `head` の列が
    1 つでも無ければ空。番号が読めない行・列が足りない行は飛ばす。
    """
    section = section_lines(body, CHECKS_HEADING)
    rows = [line for line in section if line.strip().startswith("|")] if section is not None else []
    if len(rows) < 2:
        return []
    found_head = cells(rows[0])
    if any(h not in found_head for h in head):
        return []
    cols = [found_head.index(h) for h in head]
    out = []
    for row in rows[2:]:
        found = cells(row)
        num = re.match(r"^(\d+)", found[cols[0]]) if len(found) > max(cols) else None
        if num:
            out.append((int(num.group(1)), [found[i] for i in cols]))
    return out


def check_kinds(body: str) -> dict[int, str]:
    """`## 受け入れ条件の確認` の表の {条件の番号: 区分}。表が無ければ空。区分の語が違う行は読まない。"""
    return {n: row[1] for n, row in check_rows(body, CHECKS_HEAD) if row[1] in KINDS}


def kinds_lack(body: str) -> list[int]:
    """区分の無い受け入れ条件の番号（1 から）。確認の表が無ければ、すべての条件が区分を欠く。"""
    kinds = check_kinds(body)
    return [n for n in range(1, len(criteria(body)) + 1) if n not in kinds]


def absorbed_missing(target_body: str, absorbed: Mapping[int, str]) -> dict[int, list[str]]:
    """統合した課題の受け入れ条件のうち、統合先の本文の受け入れ条件に無い文を、課題ごとに返す（#2274）。"""
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
