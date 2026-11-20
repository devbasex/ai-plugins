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
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from typing import NamedTuple, Optional, TypeVar

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
_T = TypeVar("_T")


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
_RULE_CELL = re.compile(r"^:?-+:?$")


def cell(value: str) -> str:
    """値を表の 1 つのセルへ書ける形にする（`|` は `\\|`、改行は `<br>`）。`cells` が元の値へ戻す。"""
    return value.replace("\r\n", "\n").replace("|", "\\|").replace("\n", _CELL_BREAK)


def split_row(line: str) -> list[str]:
    """Markdown の表の 1 行の列。エスケープしたパイプ（`\\|`）は列を区切らず、列の中では `|` に戻す。"""
    inner = line.strip().removeprefix("|")
    inner = inner[:-1] if inner.endswith("|") and not inner.endswith("\\|") else inner
    return [c.strip().replace("\\|", "|") for c in _CELL_SEP.split(inner)]


def cells(line: str) -> list[str]:
    """表の 1 行のセル（`cell` で書いた `\\|` と `<br>` は元の `|` と改行へ戻す）。"""
    return [c.replace(_CELL_BREAK, "\n") for c in split_row(line)]


def section_span(lines: Sequence[str], heading: str) -> Optional[tuple[int, int]]:
    """見出しのセクションの本文の行の範囲（見出しの次の行から、囲みの外の次の `#`・`##` の見出しの前まで）。無ければ None。"""
    at = heading_at(lines, heading)
    if at is None:
        return None
    start = at + 1
    end = next((start + i for i, line in outside_fences(lines[start:]) if SECTION_END.match(line)), len(lines))
    return start, end


def checks_table_spans(lines: Sequence[str]) -> list[tuple[int, int]]:
    """`## 受け入れ条件の確認` のセクションの、囲みの外の表の行の範囲（終わりは含まない）を上から。セクション・表が無ければ空。"""
    section = section_span(lines, CHECKS_HEADING)
    if section is None:
        return []
    start, end = section
    spans: list[tuple[int, int]] = []
    for i, line in outside_fences(lines[start:end]):
        at = start + i
        if not line.strip().startswith("|") or (spans and at < spans[-1][1]):
            continue
        last = next((j for j in range(at, end) if not _table_line(lines[j], j > at)), end)
        spans.append((at, last))
    return spans


def _table_line(line: str, after_row: bool) -> bool:
    """表の続きの行か。`|` で始まる行のほか、表の行の直後の区切りの行（`--- | ---` のように先頭の `|` を省いた形）も含める。

    区切りの行で表を分けると、見出しとデータ行が別の表になり、データ行を先頭の表の列の順で読み違える（#3886 の r7-1）。"""
    stripped = line.strip()
    return stripped.startswith("|") or (after_row and "|" in stripped and _is_rule(stripped))


def checks_table_span(lines: Sequence[str]) -> Optional[tuple[int, int]]:
    """`## 受け入れ条件の確認` のセクションの、囲みの外の最初の表の行の範囲（終わりは含まない）。セクション・表が無ければ None。

    書き換える（`acceptance_decide.recorded_body`）のはこの表だけ。読む（`check_rows`）ときもこの表の番号を後続の表より
    優先するので、書き換えた行が後続の表の古い行で上書きされない（#3886 の r4-1）。"""
    spans = checks_table_spans(lines)
    return spans[0] if spans else None


def _is_rule(line: str) -> bool:
    """表の見出しの下の区切りの行（`| :--- | ---: |` のようにすべてのセルが `-` と `:` だけ）か。"""
    found = cells(line)
    return bool(found) and all(_RULE_CELL.match(c) for c in found)


def check_rows(body: str, head: Sequence[str], valid: Callable[[list[str]], bool] = lambda row: True) -> list[tuple[int, list[str]]]:
    """`## 受け入れ条件の確認` の表のデータ行を、(条件の番号, `head` の列の順に並べたセル) で上から返す。

    `head` の先頭は番号の列。最初の表（`checks_table_span`）の最初の行を見出し、次の行を区切りとして読む。最初の表が無いか、
    見出しに `head` の列が 1 つでも無ければ空。後続の表は、見出し（最初の行の次が区切りの行）があれば、`head` の列が
    そろうときだけその見出しの列で読み、そろわない表は別の表として読み飛ばす（#3886 の r6-2）。見出しの無い表だけを最初の
    表の見出しの列の順で読む。後続の表からは、それより前の表で `valid` な記録の得られた番号の行を除いて足す（重複は先頭の表を
    優先し、後続の表にしかない記録や、前の表の行が無効な番号の記録は引き継ぐ。#3886 の r4-1・r5-1・r6-1）。番号が読めない
    行・列が足りない行は飛ばす。`valid` は `head` の順に並べたセルを受け取り、記録として有効かを返す。
    """
    out: list[tuple[int, list[str]]] = []
    seen: set[int] = set()
    for k, (rows, cols) in enumerate(_check_tables(body_lines(body), head)):
        table_rows = []
        for row in rows:
            found = cells(row)
            num = re.match(r"^(\d+)", found[cols[0]]) if len(found) > max(cols) else None
            if num and (k == 0 or int(num.group(1)) not in seen):
                table_rows.append((int(num.group(1)), [found[i] for i in cols]))
        out.extend(table_rows)
        seen.update(n for n, row in table_rows if valid(row))
    return out


def _check_tables(lines: Sequence[str], head: Sequence[str]) -> list[tuple[list[str], list[int]]]:
    """`check_rows` が読む表を、(データ行, `head` の順の列の添字) で上から返す。先頭は常に最初の表。

    最初の表が無いか、2 行に満たないか、見出しに `head` の列が 1 つでも無ければ空。後続の表は、見出しがあって `head` の列が
    そろわなければ除き、見出しが無ければ最初の表の列の順で読む。"""
    tables = [list(lines[a:b]) for a, b in checks_table_spans(lines)]
    if not tables or len(tables[0]) < 2:
        return []
    first_head = cells(tables[0][0])
    if any(h not in first_head for h in head):
        return []
    base = [first_head.index(h) for h in head]
    out: list[tuple[list[str], list[int]]] = []
    for k, rows in enumerate(tables):
        # 見出しは、次の行が区切りで列数も同じときだけ。見出しの無い表のデータ行の直後の区切り（列数が違う）を見出しと
        # 取り違えると、表全体を読み飛ばす（#3886 の r8-1）
        headed = k == 0 or (len(rows) >= 2 and _is_rule(rows[1]) and len(cells(rows[1])) == len(cells(rows[0])))
        found_head = cells(rows[0])
        if headed and any(h not in found_head for h in head):
            continue
        cols = [found_head.index(h) for h in head] if headed else base
        out.append((rows[2:] if headed else rows, cols))
    return out


def check_values(body: str, head: Sequence[str], parse: Callable[[list[str]], Optional[_T]]) -> dict[int, _T]:
    """`check_rows` の行を `parse`（`head` の順のセルを受け取り、無効なら None）で解釈した {条件の番号: 値}。

    無効な行は読まない。同じ番号の行が続けば後の行が勝つ。
    """
    out: dict[int, _T] = {}
    for num, row in check_rows(body, head, lambda row: parse(row) is not None):
        value = parse(row)
        if value is not None:
            out[num] = value
    return out


def check_kinds(body: str) -> dict[int, str]:
    """`## 受け入れ条件の確認` の表の {条件の番号: 区分}。表が無ければ空。区分の語が違う行は読まない。"""
    return check_values(body, CHECKS_HEAD, lambda row: row[1] if row[1] in KINDS else None)


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
