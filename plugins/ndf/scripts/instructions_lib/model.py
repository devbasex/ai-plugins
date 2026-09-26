"""指示書のチェックの型と観点のデータ（`instructions-check.py` から分けた。#1142 の C7）。

指摘・対象・スコープの型、観点のデータ（`data/instruction-criteria.json`）の読み込み、複数のモジュールが使う
本文の読み方（見出しと囲みは lib/md.py）を持つ。ほかの `instructions_lib` のモジュールを import しない。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import md


SUPPORTED_CRITERIA_VERSIONS = (1,)

# 観点の id と判定の手続きの対応。**一覧に無い id があれば終了コード 2 で止まる。**
KNOWN_CRITERIA = {
    "broken-import": "import_findings",
    "unlisted-import": "import_findings",
    "released-version-paragraph": "version_findings",
    "read-size": "budget_findings",
    "read-size-budget": "budget_findings",
    "instruction-count": "count_findings",
}
BULLET_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+\.\s+)")


def code_lines(text: str) -> list[bool]:
    """行ごとに、コードの囲み（開き・閉じの行を含む）かコードブロックの中なら真（CommonMark の規則）。"""
    return md.fenced_lines(text)


def atx_headings(text: str) -> dict[int, str]:
    """`#` で書いた見出しの行番号（1 始まり）→ `#` の後ろの字面。引用や箇条書きの中の見出しは含めない。"""
    lines = text.splitlines()
    out: dict[int, str] = {}
    for h in md.headings(text):
        raw = lines[h.line].strip() if h.line < len(lines) else ""
        if raw.startswith("#"):
            out[h.line + 1] = raw.lstrip("#").strip()
    return out


class CheckError(Exception):
    """確かめられなかった（終了コード 2）。"""


# --- 型 ----------------------------------------------------------------------

@dataclass
class Source:
    """指摘の配布元。プラグインのスコープの指摘だけが持つ。"""

    name: str = ""
    version: str = ""
    origin: str = ""
    update: str = ""
    ndf: bool = False


@dataclass
class Finding:
    """1 件の指摘。扱いは出力の直前に `action_of` が決める。"""

    criterion_id: str
    message: str
    scope: str = "project"
    path: str | None = None
    line: int | None = None
    source: Source | None = None


@dataclass
class Target:
    """走査する指示書 1 本。どのスコープから集めたかと、そのスコープの根を持つ。"""

    path: Path
    rel: str
    scope: str
    root: Path
    is_root_file: bool
    imports: dict | None = None
    source: Source | None = None


@dataclass
class ScopeRoot:
    """スコープの根 1 つ。許可（`imports`）はこの単位で持つ。"""

    root: Path
    scope: str
    imports: dict | None = None
    source: Source | None = None
    targets: list[Target] = field(default_factory=list)


# --- 観点のデータ ------------------------------------------------------------

@dataclass
class Criteria:
    """観点と出典。**読めなければ止まる**（何を見るかが決まらないまま走らせない）。"""

    checked_at: str | None
    enforce: dict[str, str]
    sources_of: dict[str, list[str]]
    sources: list[dict]

    def enabled(self, criterion_id: str) -> bool:
        return criterion_id in self.enforce

    def is_error(self, criterion_id: str) -> bool:
        return self.enforce.get(criterion_id) == "error"

    def origins_of(self, criterion_id: str) -> list[str]:
        """観点の根拠の出典（名前と参照日）。**値そのものは持たない。**"""
        by_id = {s.get("id"): s for s in self.sources if isinstance(s, dict)}
        names = []
        for source_id in self.sources_of.get(criterion_id, []):
            source = by_id.get(source_id)
            if source:
                names.append(f"{source.get('name', source_id)}"
                             f"（{source.get('checked_at', '-')}）")
        return names


def load_criteria(path: Path) -> Criteria:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CheckError(f"観点のデータを読めない（{path}）: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("version") not in SUPPORTED_CRITERIA_VERSIONS:
        raise CheckError(f"観点のデータの version が無いか未対応である（{path}）")
    entries = raw.get("criteria")
    if not isinstance(entries, list):
        raise CheckError("観点のデータの criteria が配列ではない")
    enforce: dict[str, str] = {}
    sources_of: dict[str, list[str]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise CheckError("観点のデータの criteria の要素がオブジェクトではない")
        criterion_id = entry.get("id")
        if criterion_id not in KNOWN_CRITERIA:
            raise CheckError(f"知らない観点の id である: {criterion_id!r}")
        if entry.get("enforce") not in ("error", "report"):
            raise CheckError(f"観点 {criterion_id} の enforce が error / report ではない")
        enforce[criterion_id] = entry["enforce"]
        sources_of[criterion_id] = list(entry.get("sources") or [])
    sources = raw.get("sources")
    if not isinstance(sources, list):
        raise CheckError("観点のデータの sources が配列ではない")
    return Criteria(raw.get("checked_at"), enforce, sources_of, sources)
