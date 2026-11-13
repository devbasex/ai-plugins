"""詳細設計の文書（`issues/issue-<番号>-design-detail.md`）の判定（#3144）。

`design-survey.py check` が呼ぶ 4 規則を持つ。

- `detail_section`: 雛形の節の見出しが無い・節が表の行も `省く: <理由>` も持たない・関数の仕様の表に列が無い
- `detail_failure`: 関数の仕様の表で副作用が `無し` でない関数が、異常系の処理の表の 1 列目に無い
- `detail_reuse`: 関数の仕様の表で種類が `新設` の関数が、既存の部品の再利用の表の 1 列目に無い
- `detail_file`: ファイル配置の表のパスが、頭の設計文書の「設計の結果」の触るファイルに無い

**節の見出しは雛形（`design/references/detail-template.md`）だけが持つ。** 雛形のパスは設計文書の種類の契約
（`doc_kinds`）の `detail` の行から引き、見出しを定数に写さない。読めない雛形は `DetailTemplateError` で止める。
"""

from __future__ import annotations

import functools
import re
from pathlib import Path

import doc_kinds
import md
from design_docs import DESIGN_NAME

# 規則が名指す節と列（規則そのものの一部。雛形の見出しに無ければ `detail_sections()` が止める）
_FILES, _SPEC, _FAILURES, _REUSE = "ファイル配置", "関数の仕様", "異常系の処理", "既存の部品の再利用"
_NAMED = (_FILES, _SPEC, _FAILURES, _REUSE)
_SKIP = re.compile(r"^\s*(?:[-*+]\s+)?省く\s*[:：](?P<why>.*)$")


class DetailTemplateError(Exception):
    """詳細設計の雛形を読めない・見出しが無い・規則が名指す節が欠ける。"""


@functools.lru_cache(maxsize=8)
def _template_titles(path: str) -> tuple[str, ...]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise DetailTemplateError(f"詳細設計の雛形 {path} を読めない: {exc}") from None
    titles = tuple(h.title for h in md.headings(text) if h.level == 2)
    if not titles:
        raise DetailTemplateError(f"詳細設計の雛形 {path} に ## の見出しが無い")
    lacking = [t for t in _NAMED if t not in titles]
    if lacking:
        raise DetailTemplateError(f"詳細設計の雛形 {path} に節が無い: {', '.join(lacking)}")
    return titles


def detail_sections() -> tuple[str, ...]:
    """詳細設計の雛形の `##` 見出しを並びのまま返す。"""
    return _template_titles(str(doc_kinds.template_path("detail")))


def is_detail(path: str) -> bool:
    """パスの名前が詳細設計の文書（`issue-<番号>-design-detail.md`）か。"""
    name = str(path).replace("\\", "/").rsplit("/", 1)[-1]
    return bool(DESIGN_NAME.match(name)) and doc_kinds.doc_kind(path, placed=False) == "detail"


def _cell_text(cell: str) -> str:
    return cell.replace("`", "").strip()


def _section_table(text: str, title: str) -> tuple[md.Section | None, md.Table | None, str | None]:
    """`##` の見出しが `title` の節と、その節の最初の表と、`省く:` の理由を返す。"""
    sec = md.section_named(text, title, level=2)
    if sec is None:
        return None, None, None
    table = next((t for t in md.tables(text) if sec.start <= t.start < sec.end), None)
    lines = text.splitlines()
    fenced = md.fenced_lines(text)
    why = None
    for i in range(sec.start, min(sec.end, len(lines))):
        m = None if fenced[i] else _SKIP.match(lines[i])
        if m:
            why = m.group("why").strip()
            break
    return sec, table, why


def _doc_line(table: md.Table, k: int) -> int:
    """表の本文の k 行目の、文書の中の行（0 始まり。見出しと区切りの 2 行の後）。"""
    return table.start + 2 + k


def _first_column(table: md.Table | None) -> set[str]:
    return {_cell_text(r[0]) for r in table.rows if r} if table else set()


def check_detail(path: str, text: str, result_files: set[str] | None, why: str = "") -> list[dict]:
    """1 本の詳細設計の文書を 4 規則で判定し、指摘の行（`name`・`rule`・`text`）を規則の順・行の順で返す。

    `result_files` は頭の設計文書の「設計の結果」の触るファイル。読めなければ `None` と理由 `why`。
    `None` で `why` が空なら照合する先が無い（`legacy-refactor` の書式）ことを表し、`detail_file` を当てない。
    """
    lines = text.splitlines()
    found: dict[str, list[dict]] = {"detail_section": [], "detail_failure": [], "detail_reuse": [], "detail_file": []}
    read: dict[str, md.Table | None] = {}
    for title in detail_sections():
        sec, table, skip = _section_table(text, title)
        read[title] = table
        if sec is None:
            found["detail_section"].append({"name": f"{path} {title}", "rule": "detail_section", "text": f"節「{title}」が無い"})
        elif not (table and table.rows) and not skip:
            found["detail_section"].append(
                {"name": f"{path} {title}", "rule": "detail_section", "text": f"節「{title}」が表の行も `省く: <理由>` も持たない"}
            )

    def _row_item(rule: str, table: md.Table, k: int, name: str) -> dict:
        at = _doc_line(table, k)
        src = lines[at].strip() if at < len(lines) else name
        return {"name": f"{path}:{at + 1} {name}", "rule": rule, "text": src}

    spec = read.get(_SPEC)
    if spec is not None and spec.rows:
        header = [_cell_text(h) for h in spec.header]
        missing = [c for c in ("種類", "副作用") if c not in header]
        for col in missing:
            found["detail_section"].append(
                {"name": f"{path} {_SPEC}", "rule": "detail_section", "text": f"節「{_SPEC}」の表に列「{col}」が無い"}
            )
        failures, reuses = _first_column(read.get(_FAILURES)), _first_column(read.get(_REUSE))
        for k, row in enumerate(spec.rows):
            name = _cell_text(row[0]) if row else ""
            if not name:
                continue
            if "副作用" in header:
                cell = row[header.index("副作用")] if header.index("副作用") < len(row) else ""
                if _cell_text(cell) != "無し" and name not in failures:
                    found["detail_failure"].append(_row_item("detail_failure", spec, k, name))
            if "種類" in header:
                cell = row[header.index("種類")] if header.index("種類") < len(row) else ""
                if _cell_text(cell) == "新設" and name not in reuses:
                    found["detail_reuse"].append(_row_item("detail_reuse", spec, k, name))

    if result_files is None and why:
        found["detail_file"].append({"name": f"{path} 設計の結果", "rule": "detail_file", "text": why})
    elif result_files is not None:
        files = read.get(_FILES)
        for k, row in enumerate(files.rows if files else []):
            rel = _cell_text(row[0]) if row else ""
            if rel and rel not in result_files:
                found["detail_file"].append(_row_item("detail_file", files, k, rel))
    return [it for rule in found.values() for it in rule]
