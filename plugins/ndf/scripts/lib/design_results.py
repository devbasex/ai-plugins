"""設計の結果（#1485 の決定 1）。

設計文書の「設計の結果」の節の表（課題・扱い・取り込み先・触るファイル）を読み（行は読んだ文書のパス `doc` を持つ）、`design-results.json` として
計画の置き場へ書く値オブジェクトにする。扱いは `実装する` / `取り込む` / `閉じる` の 3 つで、表のすべての行の課題が
スプリント PR の Closes の行になる。節が無い・表の形が違う設計文書は「読めなかった」として扱う。"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from design_docs import connected_docs, name_numbers

SECTION = "設計の結果"
HEADER = ("課題", "扱い", "取り込み先", "触るファイル")
IMPLEMENT, ABSORB, CLOSE = "実装する", "取り込む", "閉じる"
KINDS = (IMPLEMENT, ABSORB, CLOSE)


class DesignResultsError(ValueError):
    """設計の結果どうしが食い違う（同じ課題に違う扱い・取り込み先・触るファイル）か、取り込み先が無い。"""


PART_MAX = 120  # 読めなかった部分を要約へ出す文字数（1 件あたり）
CELL_FIX = "パスをバッククォートで囲んで直に列挙し、直した文書をマージしてから打ち直す"


@dataclass(frozen=True)
class CellProblem:
    """「実装する」の行の触るファイルのセルの形の誤り（#2453）。`part` は読めなかった部分。"""

    issue: int
    doc: str
    part: str

    def __str__(self) -> str:
        return f"#{self.issue}（{self.doc or NO_DOC}）の触るファイルのセルを読めない: {self.part[:PART_MAX]}"


class DesignCellError(DesignResultsError):
    """触るファイルのセルが書式から外れている（リンクや語で別の文書へ委ねたなど）。すべての誤りを持つ。"""

    def __init__(self, problems: list[CellProblem]):
        self.problems = list(dict.fromkeys(problems))
        super().__init__("。".join([*map(str, self.problems), CELL_FIX]))


@dataclass
class Row:
    issue: int
    kind: str
    host: int | None = None
    files: list[str] = field(default_factory=list)
    doc: str = ""  # この行を読んだ設計文書のパス（リポジトリの根からの相対。#1651）


@dataclass
class DesignResults:
    rows: list[Row] = field(default_factory=list)
    design_prs: list[int] = field(default_factory=list)
    unread: list[int] = field(default_factory=list)
    base_docs: list[str] = field(default_factory=list)  # ベースブランチの `issues/` の直下の設計文書（#2453）

    def closes(self) -> list[int]:
        return sorted({r.issue for r in self.rows})

    def row_of(self, issue: int) -> Row | None:
        return next((r for r in self.rows if r.issue == issue), None)

    def host_of(self, issue: int) -> int | None:
        r = self.row_of(issue)
        return r.host if r and r.kind == ABSORB else None

    def docs_of(self, issues: list[int]) -> list[str]:
        """課題の設計文書のパス。行を読んだ文書（重なりを除き、課題の順）を先頭に、それと番号でつながる
        `base_docs` の文書をパスの昇順で続ける。起点は先頭の文書の名前の番号と、その文書に行を持つ課題の番号（#2453）。"""
        heads = list(dict.fromkeys(r.doc for i in issues if (r := self.row_of(i)) is not None and r.doc))
        seeds: set[str] = set()
        for d in heads:
            seeds |= name_numbers(d) or set()
            seeds |= {str(r.issue) for r in self.rows if r.doc == d}
        rest = sorted(set(connected_docs(self.base_docs, seeds)) - set(heads)) if heads else []
        return heads + rest

    def files_of(self, issue: int) -> list[str]:
        r = self.row_of(issue)
        return list(r.files) if r and r.kind == IMPLEMENT else []

    def add(self, rows: list[Row]) -> None:
        """別の設計文書の行を足す。同じ課題に違う設計の結果を書いていれば DesignResultsError（食い違い。#2361）。"""
        for r in rows:
            old = self.row_of(r.issue)
            if old is None:
                self.rows.append(r)
            elif parts := _differences(old, r):
                head = f"#{r.issue} の設計の結果が 2 本の設計文書で違う（先: {old.doc or NO_DOC} / 後: {r.doc or NO_DOC} ）"
                raise DesignResultsError("。".join([head, *parts]))

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> DesignResults:
        rows = [
            Row(int(r["issue"]), r["kind"], r.get("host"), list(r.get("files") or []), str(r.get("doc") or ""))
            for r in data.get("rows") or []
        ]
        return cls(
            rows,
            [int(n) for n in data.get("design_prs") or []],
            [int(n) for n in data.get("unread") or []],
            [str(d) for d in data.get("base_docs") or []],
        )


NO_DOC = "パスの記録なし"  # 行が設計文書のパスを持たないとき（#2361 の決定 3）


def _host(host: int | None) -> str:
    return f"#{host}" if host is not None else "—"


def _only_in(mine: list[str], other: list[str]) -> list[str]:
    """`mine` にだけある分を書かれた順で。重なりを数えて取る（#2361 の決定 2）。"""
    extra = Counter(mine) - Counter(other)
    listed = []
    for f in mine:
        if extra[f] > 0:
            extra[f] -= 1
            listed.append(f)
    return listed


def _differences(old: Row, new: Row) -> list[str]:
    """違う項目ごとの文（扱い → 取り込み先 → 触るファイル）。値は先 / 後の順。"""
    parts = []
    if old.kind != new.kind:
        parts.append(f"扱い: {old.kind} / {new.kind}")
    if old.host != new.host:
        parts.append(f"取り込み先: {_host(old.host)} / {_host(new.host)}")
    if sorted(old.files) != sorted(new.files):
        only = []
        for side, mine, other in (("先", old.files, new.files), ("後", new.files, old.files)):
            if listed := _only_in(mine, other):
                only.append(f"{side}にだけある: {', '.join(listed)}")
        parts.append(f"触るファイル: {len(old.files)} 個 / {len(new.files)} 個（{' / '.join(only)} ）")
    return parts


def load_results(path) -> DesignResults | None:
    """`design-results.json` を読む。無い・読めなければ None。"""
    try:
        return DesignResults.from_json(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _issue(cell: str) -> int | None:
    m = re.fullmatch(r"#?(\d+)", cell.strip())
    return int(m.group(1)) if m else None


OPEN, CLOSE_PAREN = "(（", ")）"
SEPARATORS = "、,"
EMPTY_CELLS = ("", "—", "-")


def _files(cell: str) -> tuple[list[str], list[str]]:
    """触るファイルのセルを読み、括弧の外のコードスパンの中身（パス）と、読めなかった部分を返す（#2453 の決定 3・5）。

    括弧の外の読点・`,`・空白は区切り。括弧の中は注記として読み飛ばすが、リンクは読めなかった部分にする。"""
    import md  # Markdown のパーサー（deps の md）

    if cell.strip() in EMPTY_CELLS:
        return [], []
    files: list[str] = []
    bad: list[str] = []
    word: list[str] = []
    depth = 0
    in_link = False

    def flush() -> None:
        if word:
            bad.append("".join(word))
            word.clear()

    kids = [c for t in md.md_tokens(cell) if t.type == "inline" for c in t.children or []]
    for c in kids:
        if in_link:
            in_link = c.type != "link_close"
            continue
        if c.type == "link_open":
            flush()
            bad.append(str(c.attrs.get("href", "")))
            in_link = True
        elif c.type == "code_inline":
            flush()
            if depth == 0 and c.content.strip():
                files.append(c.content.strip())
        elif c.type == "text":
            for ch in c.content:
                if ch in OPEN:
                    flush()
                    depth += 1
                elif ch in CLOSE_PAREN:
                    if depth:
                        depth -= 1
                    else:
                        word.append(ch)
                elif depth:
                    continue
                elif ch in SEPARATORS or ch.isspace():
                    flush()
                else:
                    word.append(ch)
        elif c.type in ("softbreak", "hardbreak"):
            flush()
        elif depth == 0:
            flush()
            bad.append(c.content or c.markup or c.type)
    flush()
    if depth:
        bad.append("閉じていない括弧")
    return files, bad


def parse_results(text: str, doc: str = "") -> list[Row] | None:
    """設計文書から「設計の結果」の表を読む。節が無い・表の形が違えば None。`doc` は行へ写す文書のパス。

    「実装する」の行の触るファイルのセルが書式から外れていれば、表の最後まで読んでから DesignCellError（#2453）。"""
    import md  # Markdown のパーサー（deps の md）

    sec = md.section_named(text, SECTION)
    if sec is None:
        return None
    table = next((t for t in md.tables(text) if sec.start <= t.start < sec.end), None)
    if table is None or tuple(h.strip() for h in table.header[:4]) != HEADER:
        return None
    rows = []
    problems: list[CellProblem] = []
    for cells in table.rows:
        issue = _issue(cells[0]) if cells else None
        kind = cells[1].strip() if len(cells) > 1 else ""
        if issue is None or kind not in KINDS:
            return None
        host = _issue(cells[2]) if len(cells) > 2 else None
        if kind == ABSORB and host is None:
            return None
        files, bad = _files(cells[3]) if kind == IMPLEMENT and len(cells) > 3 else ([], [])
        if bad:
            problems.append(CellProblem(issue, doc, " / ".join(bad)))
        rows.append(Row(issue, kind, host if kind == ABSORB else None, files, doc))
    if problems:
        raise DesignCellError(problems)
    return rows
