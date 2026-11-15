"""課題の本文・追跡されたファイルの一覧・設計文書から、モードの判定の材料を組む（#3569。旧い `mode-materials.py` の移し先）。

課題ごとに、種類・公開インタフェースの変更の有無（本文の「影響」の表）・触るファイルの候補・候補ごとのテストのファイルを
組む。判定表の行は選ばない（行を選ぶのは conductor）。候補は code span とリンクの宛先からだけ取り、取れなければ候補 0 件と
出す（推して埋めない）。Markdown は markdown-it-py で読み、コードの囲みの中は読まない。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple, Optional

from markdown_it import MarkdownIt
from markdown_it.token import Token

from ndf.core.plan.inputs_decide import files_cell
from ndf.domains.relay import signals

LINE_SUFFIX = re.compile(r":\d+(?:-\d+)?$")
TEST_FORMS = ("test_{}.", "{}_test.", "{}.test.", "{}.spec.")
# 設計文書の保存先（課題の番号ごと）と、触るファイルを持つ節
DESIGN_FILE = re.compile(r"^issues/issue-(\d+)-design[^/]*\.md$")
RESULTS_HEADING = "設計の結果"
ISSUE_CELL = re.compile(r"^#(\d+)$")
KIND_HEADING = "種類"
IMPACT_HEADING = "影響"
INTERFACE_ROW = "公開インタフェース"
NOT_STATED = "記載なし"
# 「公開インタフェース」の 2 列目の書き出しと値（この順に当てる）
INTERFACE_VALUES = ("変わらない", "変わる")


class Head(NamedTuple):
    title: str
    level: int
    line: int


class Table(NamedTuple):
    line: int
    rows: list[list[str]]


class Gathered(NamedTuple):
    """読めた課題の材料（`items`）と、読めなかった課題（`gaps`）。"""

    items: list[dict[str, Any]]
    gaps: list[dict[str, str]]


def _tokens(body: str) -> list[Token]:
    return MarkdownIt("commonmark").enable("table").parse(body)


def _line(tok: Token) -> int:
    return tok.map[0] if tok.map else 0


def _heads(tokens: list[Token]) -> list[Head]:
    return [Head(tokens[i + 1].content.strip(), int(tok.tag[1:]), _line(tok)) for i, tok in enumerate(tokens) if tok.type == "heading_open"]


def _section(heads: list[Head], title: str) -> Optional[tuple[int, int]]:
    """見出しが `title` の節の行の範囲（見出しの次の行から、同じか上の水準の次の見出しの前まで）。"""
    for i, head in enumerate(heads):
        if head.title == title:
            end = next((h.line for h in heads[i + 1 :] if h.level <= head.level), None)
            return head.line + 1, end if end is not None else 1 << 30
    return None


def _section_of(heads: list[Head], line: int) -> str:
    return next((h.title for h in reversed(heads) if h.line <= line), "")


def _inline_refs(tokens: list[Token]) -> list[tuple[int, str]]:
    """インラインの code span の中身とリンクの宛先を (行, 文字列) で並べる。"""
    found: list[tuple[int, str]] = []
    for tok in tokens:
        if tok.type != "inline" or tok.children is None:
            continue
        for child in tok.children:
            if child.type == "code_inline":
                found.append((_line(tok), child.content))
            elif child.type == "link_open":
                href = str(child.attrGet("href"))
                found.append((_line(tok), href.split("#", 1)[0] if href.split("#", 1)[0] else href))
    return found


def _tables(tokens: list[Token]) -> list[Table]:
    tables: list[Table] = []
    for i, tok in enumerate(tokens):
        if tok.type != "table_open":
            continue
        rows: list[list[str]] = []
        for t in tokens[i + 1 :]:
            if t.type == "table_close":
                break
            if t.type == "tr_open":
                rows.append([])
            elif t.type == "inline" and rows:
                rows[-1].append(t.content)
        tables.append(Table(_line(tok), rows))
    return tables


def _section_tables(tokens: list[Token], title: str) -> list[Table]:
    """見出しが `title` の節の行の範囲に始まる表（出てくる順）。節が無ければ空。"""
    span = _section(_heads(tokens), title)
    if span is None:
        return []
    return [table for table in _tables(tokens) if span[0] <= table.line < span[1]]


def raw_candidates(body: str) -> list[tuple[str, str]]:
    """本文の code span の中身とリンクの宛先を整えて (パス, 節の見出し) で返す（出てくる順）。"""
    tokens = _tokens(body)
    heads = _heads(tokens)
    out: list[tuple[str, str]] = []
    for line, text in sorted(_inline_refs(tokens), key=lambda t: t[0]):
        s = LINE_SUFFIX.sub("", text.strip().removeprefix("./"))
        if not s or any(ch.isspace() for ch in s) or s[0] in "-$#" or "://" in s:
            continue
        out.append((s, _section_of(heads, line)))
    return out


def exists_in(path: str, files: list[str], fileset: set[str]) -> bool:
    """追跡されたファイルに同じパスがあるか、そのパスで始まるディレクトリに当たるか。"""
    base = path.rstrip("/")
    return base in fileset or any(f.startswith(base + "/") for f in files)


def looks_new(path: str) -> bool:
    """`/` を含み、拡張子を持つか `/` で終わる（新しく作るファイル・ディレクトリ）。識別子や語は当たらない。"""
    return "/" in path and (path.endswith("/") or PurePosixPath(path).suffix != "")


def is_test(path: str) -> bool:
    """`tests/` の下か、`test_<名>.*`・`<名>_test.*`・`<名>.test.*`・`<名>.spec.*` の名前のファイル。"""
    p = PurePosixPath(path)
    first = p.name.split(".")[0]
    return "tests" in p.parts[:-1] or p.name.startswith("test_") or first.endswith("_test") or ".test." in p.name or ".spec." in p.name


def tests_of(path: str, files: list[str], fileset: set[str]) -> list[str]:
    """存在するファイルのテストのファイル。テストのファイル自身なら自分だけ。ディレクトリ・無いファイルは空。"""
    if path not in fileset:
        return []
    if is_test(path):
        return [path]
    names = tuple(f.format(PurePosixPath(path).stem) for f in TEST_FORMS)
    return [f for f in files if PurePosixPath(f).name.startswith(names) and f != path]


def issue_kind(body: str) -> Optional[str]:
    """本文の「種類」の節の最初の空でない行。節が無ければ None。"""
    span = _section(_heads(_tokens(body)), KIND_HEADING)
    if span is None:
        return None
    lines = body.splitlines()[span[0] : span[1]]
    return next((x.strip() for x in lines if x.strip()), None)


def _interface_cell(rows: list[list[str]]) -> Optional[str]:
    """表の行のうち最初の `公開インタフェース` の行の 2 列目（前後の空白を落とす）。行が無ければ None（空のセルと区別する）。"""
    return next((row[1].strip() for row in rows if len(row) >= 2 and row[0].strip() == INTERFACE_ROW), None)


def _interface_value(cell: str) -> dict[str, str]:
    """セルを値（書き出しが `INTERFACE_VALUES` のどれか。どれでもなければ `記載なし`）とセルの組にする。"""
    return {"value": next((v for v in INTERFACE_VALUES if cell.startswith(v)), NOT_STATED), "cell": cell}


def public_interface(body: str) -> dict[str, str]:
    """本文の「影響」の表の `公開インタフェース` の行の 2 列目。無ければ `記載なし`。"""
    for table in _section_tables(_tokens(body), IMPACT_HEADING):
        cell = _interface_cell(table.rows)
        if cell is not None:
            return _interface_value(cell)
    return {"value": NOT_STATED, "cell": ""}


def issue_materials(number: int, body: str, files: list[str], designs: list[tuple[str, list[str]]]) -> dict[str, Any]:
    """1 つの課題の材料。`designs` は (設計文書のパス, その課題の「触るファイル」) の並び。"""
    fileset = set(files)
    seen: dict[str, dict[str, Any]] = {}
    for path, section in raw_candidates(body):
        if path in seen:
            continue
        exists = exists_in(path, files, fileset)
        if not exists and not looks_new(path):
            continue
        seen[path] = {"path": path, "exists": exists, "source": "本文", "section": section}
    for doc, paths in designs:
        for path in paths:
            if path not in seen:
                seen[path] = {"path": path, "exists": exists_in(path, files, fileset), "source": "設計", "section": doc}
    out = [{**f, "tests": tests_of(f["path"], files, fileset) if f["exists"] else []} for f in seen.values()]
    return {"number": number, "kind": issue_kind(body), "public_interface": public_interface(body), "files": out, "files_found": len(out)}


def design_rows(text: str, number: int) -> list[str]:
    """設計文書の「設計の結果」の表から、課題 `number` の行の触るファイル（バッククォートで囲んだパス）を読む。"""
    found: list[str] = []
    for table in _section_tables(_tokens(text), RESULTS_HEADING):
        for row in table.rows[1:]:
            hit = ISSUE_CELL.match(row[0].strip()) if row else None
            if hit is not None and int(hit.group(1)) == number and len(row) >= 4:
                found += files_cell(row[3])[0]
    return list(dict.fromkeys(found))


def design_files(root: Path, number: int, files: Iterable[str]) -> list[tuple[str, list[str]]]:
    """課題の設計文書（`issues/issue-<番号>-design*.md`）ごとの、その課題の触るファイル。読めない文書は飛ばす。"""
    out: list[tuple[str, list[str]]] = []
    for rel in sorted(f for f in files if (m := DESIGN_FILE.match(f)) is not None and int(m.group(1)) == number):
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):  # noqa: S112 ファイル: 読めない設計文書は材料にしない（旧い design_files と同じ）
            continue
        paths = design_rows(text, number)
        if paths:
            out.append((rel, paths))
    return out


def gather(root: Path, numbers: list[int], files: list[str], body_of: Callable[[int], str]) -> Gathered:
    """課題ごとの材料と、本文を読めなかった課題を返す。`body_of` は本文を返し、読めなければ `OSError`・`ValueError`。"""
    items: list[dict[str, Any]] = []
    gaps: list[dict[str, str]] = []
    for n in numbers:
        try:
            body = body_of(n)
        except (OSError, ValueError) as exc:
            gaps.append({"kind": "材料", "name": f"#{n}", "reason": f"課題の本文を読めない: {exc}"})
            continue
        items.append(issue_materials(n, body, files, design_files(root, n, files)))
    return Gathered(items, gaps)


def draft(items: list[dict[str, Any]]) -> dict[str, Any]:
    """判定の根拠のファイルの下書き。`files` は存在する候補だけ。row・mode・condition は conductor が埋める。"""
    return {
        "issues": [
            {
                "number": it["number"],
                "row": None,
                "mode": None,
                "condition": "",
                "files": [f["path"] for f in it["files"] if f["exists"]],
                "materials": it,
            }
            for it in items
        ]
    }


def write_draft(path: Path, data: Mapping[str, Any]) -> None:
    """下書きを一時ファイルからの置き換えで書く。"""
    signals.replace_json(path, data)
