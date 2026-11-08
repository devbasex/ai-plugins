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

SECTION = "設計の結果"
HEADER = ("課題", "扱い", "取り込み先", "触るファイル")
IMPLEMENT, ABSORB, CLOSE = "実装する", "取り込む", "閉じる"
KINDS = (IMPLEMENT, ABSORB, CLOSE)


class DesignResultsError(ValueError):
    """設計の結果どうしが食い違う（同じ課題に違う扱い・取り込み先・触るファイル）か、取り込み先が無い。"""


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

    def closes(self) -> list[int]:
        return sorted({r.issue for r in self.rows})

    def row_of(self, issue: int) -> Row | None:
        return next((r for r in self.rows if r.issue == issue), None)

    def host_of(self, issue: int) -> int | None:
        r = self.row_of(issue)
        return r.host if r and r.kind == ABSORB else None

    def docs_of(self, issues: list[int]) -> list[str]:
        """課題の行を読んだ設計文書のパス（重なりを除き、課題の順）。"""
        docs = [r.doc for i in issues if (r := self.row_of(i)) is not None and r.doc]
        return list(dict.fromkeys(docs))

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
        return cls(rows, [int(n) for n in data.get("design_prs") or []], [int(n) for n in data.get("unread") or []])


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


def _files(cell: str) -> list[str]:
    return [f.strip() for f in re.findall(r"`([^`]+)`", cell) if f.strip()]


def parse_results(text: str, doc: str = "") -> list[Row] | None:
    """設計文書から「設計の結果」の表を読む。節が無い・表の形が違えば None。`doc` は行へ写す文書のパス。"""
    import md  # Markdown のパーサー（deps の md）

    sec = md.section_named(text, SECTION)
    if sec is None:
        return None
    table = next((t for t in md.tables(text) if sec.start <= t.start < sec.end), None)
    if table is None or tuple(h.strip() for h in table.header[:4]) != HEADER:
        return None
    rows = []
    for cells in table.rows:
        issue = _issue(cells[0]) if cells else None
        kind = cells[1].strip() if len(cells) > 1 else ""
        if issue is None or kind not in KINDS:
            return None
        host = _issue(cells[2]) if len(cells) > 2 else None
        if kind == ABSORB and host is None:
            return None
        files = _files(cells[3]) if kind == IMPLEMENT and len(cells) > 3 else []
        rows.append(Row(issue, kind, host if kind == ABSORB else None, files, doc))
    return rows
