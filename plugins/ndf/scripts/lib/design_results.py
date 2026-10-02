"""設計の結果（#1485 の決定 1）。

設計文書の「設計の結果」の節の表（課題・扱い・取り込み先・触るファイル）を読み、`design-results.json` として
計画の置き場へ書く値オブジェクトにする。扱いは `実装する` / `取り込む` / `閉じる` の 3 つで、表のすべての行の課題が
スプリント PR の Closes の行になる。節が無い・表の形が違う設計文書は「読めなかった」として扱う。"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

SECTION = "設計の結果"
HEADER = ("課題", "扱い", "取り込み先", "触るファイル")
IMPLEMENT, ABSORB, CLOSE = "実装する", "取り込む", "閉じる"
KINDS = (IMPLEMENT, ABSORB, CLOSE)


class DesignResultsError(ValueError):
    """設計の結果どうしが食い違う（同じ課題に違う扱い）か、取り込み先が無い。"""


@dataclass
class Row:
    issue: int
    kind: str
    host: int | None = None
    files: list[str] = field(default_factory=list)


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

    def files_of(self, issue: int) -> list[str]:
        r = self.row_of(issue)
        return list(r.files) if r and r.kind == IMPLEMENT else []

    def add(self, rows: list[Row]) -> None:
        """別の設計文書の行を足す。同じ課題に違う扱いを書いていれば DesignResultsError。"""
        for r in rows:
            old = self.row_of(r.issue)
            if old is None:
                self.rows.append(r)
            elif (old.kind, old.host, sorted(old.files)) != (r.kind, r.host, sorted(r.files)):
                raise DesignResultsError(f"#{r.issue} に違う扱いが書かれている（{old.kind} と {r.kind}）")

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> DesignResults:
        rows = [Row(int(r["issue"]), r["kind"], r.get("host"), list(r.get("files") or [])) for r in data.get("rows") or []]
        return cls(rows, [int(n) for n in data.get("design_prs") or []], [int(n) for n in data.get("unread") or []])


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


def parse_results(text: str) -> list[Row] | None:
    """設計文書から「設計の結果」の表を読む。節が無い・表の形が違えば None。"""
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
        rows.append(Row(issue, kind, host if kind == ABSORB else None, _files(cells[3]) if kind == IMPLEMENT and len(cells) > 3 else []))
    return rows
