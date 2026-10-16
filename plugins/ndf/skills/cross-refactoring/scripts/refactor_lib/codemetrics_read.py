"""測定ツールの出力を指標の共通の形へ直す（#1319 の腐敗防止層）。

**純粋な処理だけを置く。** ツールの出力（文字列）を受け取り、関数・ファイル・重複の箇所の
辞書へ直す。形が合わなければ `UnreadableOutput` を上げ、呼び出し側が `unreadable_output` にする。
言語とツールの表・宣言・コマンドの組み立ては `codemetrics` にある。
"""

from __future__ import annotations

import ast
import csv
import json
import re
from typing import Any, Iterable, Optional

from .codemetrics import RUFF_FIELDS, TOOL_JSCPD, TOOL_SYMILAR, path_role, to_relative
from .pathkinds import is_test_path

RUFF_INVALID_SYNTAX = "invalid-syntax"
_RUFF_VALUE = re.compile(r"\((\d+) > 0\)$")


class UnreadableOutput(ValueError):
    """ツールは正常に終わったが、出力を読めない（`unreadable_output`）。"""


def parse_ruff(
    text: str,
    roots: Iterable[str],
) -> tuple[dict[tuple[str, int], dict[str, int]], set[str]]:
    """Ruff の JSON から `((パス, def の行) → 欄 → 値, 構文を読めなかったファイル)`。

    値は `message` の末尾の `(<N> > 0)` にだけある。形が合わなければ `UnreadableOutput`。
    """
    roots = list(roots)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise UnreadableOutput(f"ruff の出力が JSON でない（{exc.msg}）") from exc
    if not isinstance(data, list):
        raise UnreadableOutput("ruff の出力が配列でない")
    values: dict[tuple[str, int], dict[str, int]] = {}
    invalid: set[str] = set()
    for diag in data:
        if not isinstance(diag, dict):
            raise UnreadableOutput("ruff の診断がオブジェクトでない")
        path = to_relative(str(diag.get("filename") or ""), roots)
        if path is None:
            continue
        code = diag.get("code")
        if code == RUFF_INVALID_SYNTAX or diag.get("name") == RUFF_INVALID_SYNTAX:
            invalid.add(path)
            continue
        field = RUFF_FIELDS.get(str(code))
        if field is None:
            continue
        m = _RUFF_VALUE.search(str(diag.get("message") or "").strip())
        row = (diag.get("location") or {}).get("row")
        if m is None or not isinstance(row, int):
            raise UnreadableOutput(f"ruff の message を読めない: {diag.get('message')!r:.80}")
        values.setdefault((path, row), {})[field] = int(m.group(1))
    return values, invalid


def parse_complexipy(text: str, roots: Iterable[str]) -> dict[tuple[str, str], int]:
    """complexipy の JSON から `(パス, 関数の名前) → 認知的複雑度`。`::` は `.` にする。"""
    roots = list(roots)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise UnreadableOutput(f"complexipy の出力が JSON でない（{exc.msg}）") from exc
    if not isinstance(data, list):
        raise UnreadableOutput("complexipy の出力が配列でない")
    out: dict[tuple[str, str], int] = {}
    for entry in data:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("complexity"), int)
            or not entry.get("path")
            or not entry.get("function_name")
        ):
            raise UnreadableOutput(f"complexipy の要素を読めない: {entry!r:.80}")
        path = to_relative(str(entry["path"]), roots)
        if path is None:
            continue
        out[(path, str(entry["function_name"]).replace("::", "."))] = int(entry["complexity"])
    return out


class _FunctionCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.stack: list[str] = []
        self.found: list[dict[str, Any]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: "ast.FunctionDef | ast.AsyncFunctionDef") -> None:
        end = getattr(node, "end_lineno", None) or node.lineno
        self.found.append({"symbol": ".".join([*self.stack, node.name]), "lineno": node.lineno, "end_lineno": end})
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef


def python_functions(source: str) -> Optional[list[dict[str, Any]]]:
    """Python の関数を列挙する（名前・`def` の行・終わりの行）。読めなければ `None`。

    名前は `クラス.メソッド`、入れ子は `外側.内側`。`lineno` はデコレータの行ではなく `def` の行。
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    collector = _FunctionCollector()
    collector.visit(tree)
    return collector.found


def python_function_metrics(
    listed: dict[str, Optional[list[dict[str, Any]]]],
    ruff: dict[tuple[str, int], dict[str, int]],
    cognitive: dict[tuple[str, str], int],
    unreadable: set[str],
) -> list[dict[str, Any]]:
    """`ast` の関数へ Ruff（パス・def の行）と complexipy（パス・名前）の値を結び付ける。

    閾値が 0 なので、出ない `PLR` の欄は 0 と読む。complexipy が出さない関数の認知は空。
    """
    out: list[dict[str, Any]] = []
    for path in sorted(listed):
        functions = listed[path]
        if functions is None or path in unreadable:
            continue
        for fn in functions:
            values = ruff.get((path, fn["lineno"]), {})
            out.append(
                {
                    "path": path,
                    "symbol": fn["symbol"],
                    "cc": values.get("cc"),
                    "cognitive": cognitive.get((path, fn["symbol"])),
                    "branches": values.get("branches", 0),
                    "statements": values.get("statements", 0),
                    "args": values.get("args", 0),
                    "returns": values.get("returns", 0),
                    "lines": fn["end_lineno"] - fn["lineno"] + 1,
                    "role": path_role(path),
                }
            )
    return out


def parse_lizard(text: str, roots: Iterable[str]) -> list[dict[str, Any]]:
    """lizard の CSV（1 行 11 列）から関数の指標。使うのは CCN・length・file・function。"""
    roots = list(roots)
    out: list[dict[str, Any]] = []
    for row in csv.reader(text.splitlines()):
        if not row:
            continue
        if len(row) != 11:
            raise UnreadableOutput(f"lizard の CSV の列が 11 でない（{len(row)} 列）")
        try:
            cc, length = int(row[1]), int(row[4])
        except ValueError as exc:
            raise UnreadableOutput(f"lizard の CSV の数を読めない: {row[:5]}") from exc
        path = to_relative(row[6], roots)
        if path is None:
            continue
        out.append({"path": path, "symbol": row[7], "cc": cc, "lines": length, "role": path_role(path)})
    return out


def file_metrics(
    paths: Iterable[str],
    line_counts: dict[str, int],
    functions: list[dict[str, Any]],
    unreadable: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """ファイルごとの行数・関数の数・最長の関数の行。**ファイルの集合は集めた一覧が正**。"""
    skip = set(unreadable)
    by_path: dict[str, list[int]] = {}
    for fn in functions:
        by_path.setdefault(fn["path"], []).append(int(fn["lines"]))
    return [
        {
            "path": p,
            "lines": int(line_counts.get(p, 0)),
            "functions": len(by_path.get(p, [])),
            "max_function_lines": max(by_path.get(p, [0])),
            "role": path_role(p),
        }
        for p in sorted(paths)
        if p not in skip
    ]


def _clone(tool: str, fmt: str, lines: int, locations: list[dict[str, Any]]) -> dict[str, Any]:
    role = "test" if all(is_test_path(loc["path"]) for loc in locations) else "main"
    return {"tool": tool, "format": fmt, "lines": lines, "locations": locations, "role": role}


_SIM_HEAD = re.compile(r"^(\d+) similar lines in (\d+) files$")
_SIM_LOC = re.compile(r"^==(.+):\[(\d+):(\d+)\]$")
_SIM_TOTAL = re.compile(r"^TOTAL lines=(\d+) duplicates=(\d+) percent=[\d.]+$")


def parse_symilar(text: str, roots: Iterable[str]) -> dict[str, Any]:
    """symilar の文字列から重複の箇所と合計。

    `start` は 0 始まり、`end` はその行を含まない終わり。1 始まりの `start + 1`〜`end` へ直す。
    行の数 `N` は比べた行（コメントと空行を除く）で、範囲の行数とは一致しない。
    """
    roots = list(roots)
    lines = [line.strip() for line in text.splitlines()]
    clones: list[dict[str, Any]] = []
    total: Optional[tuple[int, int]] = None
    i = 0
    while i < len(lines):
        head = _SIM_HEAD.match(lines[i])
        if head:
            count, k = int(head.group(1)), int(head.group(2))
            clones.append(_clone(TOOL_SYMILAR, "python", count, _parse_symilar_block(lines, i + 1, k, roots)))
            i += 1 + k
            continue
        m = _SIM_TOTAL.match(lines[i])
        if m:
            total = (int(m.group(1)), int(m.group(2)))
        i += 1
    if total is None:
        raise UnreadableOutput("symilar の出力に TOTAL の行が無い")
    return {"clones": clones, "total_lines": total[0], "duplicated_lines": total[1]}


def _parse_symilar_block(lines: list[str], start: int, k: int, roots: list[str]) -> list[dict[str, Any]]:
    """ヘッダに続く `k` 行の場所の行を読み、1 始まりの範囲へ直した場所を返す。"""
    locations = []
    for line in lines[start : start + k]:
        loc = _SIM_LOC.match(line)
        if loc is None:
            raise UnreadableOutput(f"symilar の場所の行を読めない: {line!r:.80}")
        path = to_relative(loc.group(1), roots) or loc.group(1)
        locations.append({"path": path, "start": int(loc.group(2)) + 1, "end": int(loc.group(3))})
    if len(locations) < k:
        raise UnreadableOutput("symilar の場所の行が足りない")
    return locations


def parse_jscpd(text: Optional[str], roots: Iterable[str]) -> dict[str, Any]:
    """jscpd の報告から重複の箇所と合計。報告が無い（調べるファイルが 0 本）なら 0 箇所。"""
    if text is None:
        return {"clones": [], "total_lines": 0, "duplicated_lines": 0, "sources": 0}
    roots = list(roots)
    try:
        data = json.loads(text)
        duplicates = data["duplicates"]
        stats = (data.get("statistics") or {}).get("total") or {}
        clones = []
        for dup in duplicates:
            locations = []
            for side in ("firstFile", "secondFile"):
                entry = dup[side]
                name = str(entry["name"])
                locations.append({"path": to_relative(name, roots) or name, "start": int(entry["start"]), "end": int(entry["end"])})
            clones.append(_clone(TOOL_JSCPD, str(dup.get("format") or "—"), int(dup["lines"]), locations))
        return {
            "clones": clones,
            "total_lines": int(stats.get("lines") or 0),
            "duplicated_lines": int(stats.get("duplicatedLines") or 0),
            "sources": int(stats.get("sources") or 0),
        }
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, AttributeError) as exc:
        raise UnreadableOutput(f"jscpd の報告を読めない（{exc}）") from exc


def tail_lines(text: str, n: int = 5) -> str:
    """標準エラーの末尾 n 行を 1 行にまとめる（`tool_failed` の詳細）。"""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return " / ".join(lines[-n:])
