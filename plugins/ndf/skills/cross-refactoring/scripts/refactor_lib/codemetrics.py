"""提案の前に測る指標の、言語・ツール・宣言・出力の読み取り（#1319）。

**純粋な処理だけを置く。** 起動も書き出しもしない（起動と書き出しは `commands/measure.py`）。
ここにあるのは、言語の表・ツールの表（版の固定）・測定の宣言の読み取りと重ね合わせ・
ファイルの言語分け・コマンドの組み立て・ツールの出力の読み取り・Python の関数の列挙と
値の突き合わせ・測れなかった理由の識別子である。

**表にパスもリポジトリ固有の設定も入れない**（AC8）。ほかのリポジトリでも同じ既定で測る。
同じ名前の指標（循環的複雑度）でも、ツールが違えば値の意味が違う。言語をまたいで比べない。
"""
from __future__ import annotations

import ast
import csv
import json
import os
import posixpath
import re
from typing import Any, Callable, Iterable, Optional

from .pathkinds import is_test_path

# ---------------- 識別子 ----------------

# 測れなかった理由（I5）。指標のファイル・計画・報告はこの値だけを書く。
TOOL_MISSING = "tool_missing"
UNSUPPORTED_LANGUAGE = "unsupported_language"
DISABLED = "disabled"
TOOL_FAILED = "tool_failed"
UNREADABLE_OUTPUT = "unreadable_output"
TIMEOUT = "timeout"
TOO_MANY_FILES = "too_many_files"
REASONS = (TOOL_MISSING, UNSUPPORTED_LANGUAGE, DISABLED, TOOL_FAILED, UNREADABLE_OUTPUT,
           TIMEOUT, TOO_MANY_FILES)

# 言語ごと・重複検出のツールごとの結果。
MEASURED = "measured"
FAILED = "failed"

# 実行の単位の値（`code_metrics.status`）。
STATUS_PENDING = "pending"
STATUS_WRITTEN = "written"
STATUS_DISABLED = "disabled"
STATUS_NO_LANGUAGE = "no_language"
STATUS_WRITE_FAILED = "write_failed"
STATUSES = (STATUS_PENDING, STATUS_WRITTEN, STATUS_DISABLED, STATUS_NO_LANGUAGE,
            STATUS_WRITE_FAILED)

# 宣言を使わなかった理由（前提 8）。
DECLARATION_INVALID = "declaration_invalid"

# 宣言の置き場（書き込み用の作業ディレクトリからの相対）。
DECLARATION_FILE = ".ndf/code-metrics.json"
DECLARATION_VERSION = 1

# 宣言の出所。
SOURCE_DEFAULT = "default"
SOURCE_DECLARED = "declared"
SOURCE_INVALID = "invalid"

# ---------------- 言語とツール ----------------

TOOL_RUFF_COMPLEXIPY = "ruff-complexipy"
TOOL_LIZARD = "lizard"
TOOL_SYMILAR = "symilar"
TOOL_JSCPD = "jscpd"

# 言語 → 拡張子。lizard 1.24.0 で関数を検出できた拡張子だけを置く（2026-09-26）。
LANGUAGE_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "python": (".py",),
    "javascript": (".js", ".mjs", ".cjs", ".jsx"),
    "typescript": (".ts", ".tsx", ".mts"),
    "php": (".php",),
    "go": (".go",),
    "ruby": (".rb",),
    "java": (".java",),
    "kotlin": (".kt",),
    "swift": (".swift",),
    "rust": (".rs",),
    "c": (".c", ".h"),
    "cpp": (".cc", ".cpp"),
    "csharp": (".cs",),
    "lua": (".lua",),
    "shell": (".sh", ".bash"),
}
EXTENSION_LANGUAGE = {ext: lang for lang, exts in LANGUAGE_EXTENSIONS.items() for ext in exts}

# 言語 → 既定のツール。`None` はツールが無い（`unsupported_language`）。
DEFAULT_TOOLS: dict[str, Optional[str]] = {
    lang: (TOOL_RUFF_COMPLEXIPY if lang == "python" else None if lang == "shell" else TOOL_LIZARD)
    for lang in LANGUAGE_EXTENSIONS
}

# 測定ツール → 起動するコマンド。`ruff-complexipy` は 2 つを 1 つの測定ツールとして扱う。
TOOL_COMMANDS: dict[str, tuple[str, ...]] = {
    TOOL_RUFF_COMPLEXIPY: ("ruff", "complexipy"),
    TOOL_LIZARD: ("lizard",),
}

# コマンド → (ランナー, 版を固定したパッケージ, 記録に書く版)。版は NDF が固定する（決定 3）。
COMMANDS: dict[str, tuple[str, str, str]] = {
    "ruff": ("uvx", "ruff==0.16.9", "ruff 0.16.9"),
    "complexipy": ("uvx", "complexipy==8.0.1", "complexipy 8.0.1"),
    "lizard": ("uvx", "lizard==1.24.0", "lizard 1.24.0"),
    "symilar": ("uvx", "pylint==4.0.9", "pylint 4.0.9"),
    "jscpd": ("npx", "jscpd@4.3.0", "jscpd 4.3.0"),
}

# 1 回の起動に渡すファイルの数（言語ごとの測定）。引数の長さの上限を超えないため。
BATCH_FILES = 500
# 重複の最小の行数（決定 14）。宣言では変えない。
DUPLICATE_MIN_LINES = 8
# 重複検出に渡すパスの長さの合計の上限（バイト）。重複検出は分けて起動できない。
DUPLICATE_ARG_BYTES = 256 * 1024

# Ruff の診断の `code` → 指標の欄。閾値を 0 にし、値が 1 以上の関数をすべて出させる。
RUFF_FIELDS = {
    "C901": "cc",
    "PLR0912": "branches",
    "PLR0915": "statements",
    "PLR0913": "args",
    "PLR0911": "returns",
}
RUFF_INVALID_SYNTAX = "invalid-syntax"
_RUFF_VALUE = re.compile(r"\((\d+) > 0\)$")
_VERSION = re.compile(r"(\d+\.\d+(?:\.\d+)*)")


class UnreadableOutput(ValueError):
    """ツールは正常に終わったが、出力を読めない（`unreadable_output`）。"""


# ---------------- 宣言 ----------------

def _declaration_error(data: Any) -> Optional[str]:
    """宣言の形の誤り。正しければ `None`。"""
    if not isinstance(data, dict):
        return "オブジェクトでない"
    unknown = sorted(set(data) - {"version", "tools"})
    if unknown:
        return f"知らない鍵: {', '.join(unknown)}"
    version = data.get("version")
    if isinstance(version, bool) or version != DECLARATION_VERSION:
        return f"version が {DECLARATION_VERSION} でない: {version!r}"
    tools = data.get("tools")
    if not isinstance(tools, dict):
        return "tools がオブジェクトでない"
    for lang, tool in tools.items():
        if lang not in LANGUAGE_EXTENSIONS:
            return f"知らない言語: {lang}"
        if tool is not None and tool not in TOOL_COMMANDS:
            return f"知らないツール: {lang} = {tool!r}"
        if tool == TOOL_RUFF_COMPLEXIPY and lang != "python":
            return f"{TOOL_RUFF_COMPLEXIPY} は python にだけ当てられる: {lang}"
    return None


def load_config(text: Optional[str], enabled: bool = True) -> dict[str, Any]:
    """宣言の文字列（無ければ `None`）から測定の設定を組む。

    **形が違えば宣言の全体を使わず既定で測る**（前提 8）。一部の鍵だけを生かさない。
    宣言した言語だけを置き換え、ほかの言語は既定のまま（AC6）。
    """
    tools = dict(DEFAULT_TOOLS)
    config: dict[str, Any] = {
        "enabled": bool(enabled), "source": SOURCE_DEFAULT, "declaration": DECLARATION_FILE,
        "error": None, "tools": tools, "disabled": [],
    }
    if text is None:
        return config
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        config.update(source=SOURCE_INVALID, error=f"JSON として読めない（{exc.msg}）"
                      if isinstance(exc, json.JSONDecodeError) else "UTF-8 として読めない")
        return config
    error = _declaration_error(data)
    if error is not None:
        config.update(source=SOURCE_INVALID, error=error)
        return config
    for lang, tool in data["tools"].items():
        tools[lang] = tool
    config["source"] = SOURCE_DECLARED
    config["disabled"] = sorted(lang for lang, tool in data["tools"].items() if tool is None)
    return config


def language_tool(config: dict[str, Any], lang: str) -> tuple[Optional[str], Optional[str]]:
    """言語の `(ツール, 測らない理由)`。測るなら理由は `None`。"""
    if lang in (config.get("disabled") or []):
        return None, DISABLED
    tool = (config.get("tools") or {}).get(lang, DEFAULT_TOOLS.get(lang))
    if tool is None:
        return None, UNSUPPORTED_LANGUAGE
    return tool, None


# ---------------- ファイル ----------------

def language_of(path: str) -> Optional[str]:
    """拡張子から言語を判定する。表に無い拡張子と拡張子の無いファイルは `None`。"""
    return EXTENSION_LANGUAGE.get(posixpath.splitext(path)[1].lower())


def classify(paths: Iterable[str]) -> tuple[dict[str, list[str]], int]:
    """ファイルを言語ごとに分ける。`(言語 → パスの一覧, 言語を判定しなかったファイルの数)`。"""
    by_lang: dict[str, list[str]] = {}
    ignored = 0
    for path in paths:
        lang = language_of(path)
        if lang is None:
            ignored += 1
        else:
            by_lang.setdefault(lang, []).append(path)
    return {lang: sorted(files) for lang, files in sorted(by_lang.items())}, ignored


def duplication_targets(
    config: dict[str, Any], by_lang: dict[str, list[str]],
) -> list[tuple[str, list[str], list[str]]]:
    """重複検出の `(ツール, 言語, ファイル)` の並び（決定 13）。

    対象の言語は、判定できた言語のうち宣言で `null` にしていない言語である。言語ごとの
    測定のツールの置き換えは重複検出のツールを変えない。Python は symilar、ほかは jscpd。
    """
    disabled = set(config.get("disabled") or [])
    out: list[tuple[str, list[str], list[str]]] = []
    if "python" in by_lang and "python" not in disabled:
        out.append((TOOL_SYMILAR, ["python"], list(by_lang["python"])))
    others = [lang for lang in sorted(by_lang) if lang != "python" and lang not in disabled]
    if others:
        out.append((TOOL_JSCPD, others, sorted(f for lang in others for f in by_lang[lang])))
    return out


def arg_bytes(paths: Iterable[str]) -> int:
    """引数に渡すパスの長さの合計（区切りの 1 バイトを含む）。"""
    return sum(len(p.encode("utf-8")) + 1 for p in paths)


def chunks(items: list[str], size: int = BATCH_FILES) -> list[list[str]]:
    return [items[i:i + size] for i in range(0, len(items), size)] or [[]]


def to_relative(name: str, roots: Iterable[str]) -> Optional[str]:
    """ツールが出したパスを作業ディレクトリからの相対へ直す。外なら `None`（I3）。"""
    name = str(name)
    if not os.path.isabs(name):
        rel = posixpath.normpath(name.replace(os.sep, "/"))
        return None if rel.startswith("../") or rel == ".." else rel
    for root in roots:
        root = str(root).rstrip("/")
        if name.startswith(root + "/"):
            return posixpath.normpath(name[len(root) + 1:])
    return None


def count_lines(text: str) -> int:
    """空白だけの行を除いた行の数（決定 11）。全言語で同じ数え方にする。"""
    return sum(1 for line in text.splitlines() if line.strip())


def version_from(text: str) -> Optional[str]:
    """`--version` の出力から版の数字を取る。無ければ `None`。"""
    m = _VERSION.search(text or "")
    return m.group(1) if m else None


def role_of(path: str) -> str:
    """本体（`main`）かテスト（`test`）か（決定 1）。"""
    return "test" if is_test_path(path) else "main"


# ---------------- ランナーとコマンド ----------------

def resolve(command: str, which: Callable[[str], Optional[str]]) -> Optional[tuple[str, list[str]]]:
    """コマンドの `(ランナー, 起動の頭の語の並び)`。見つからなければ `None`（決定 3）。

    ランナー（uvx / npx）があれば版を固定して一時実行する。無いときだけ PATH のコマンドを使う。
    ランナーの失敗で PATH へ切り替えない（失敗の理由を隠すため）。
    """
    runner, package, _ = COMMANDS[command]
    if which(runner):
        if runner == "uvx":
            return "uvx", ["uvx", "--from", package, command]
        return "npx", ["npx", "-y", package]
    if which(command):
        return "path", [command]
    return None


def pinned_version(command: str) -> str:
    return COMMANDS[command][2]


def ruff_argv(prefix: list[str], files: list[str]) -> list[str]:
    """Ruff の起動。`--isolated` で対象の設定を読まず、閾値を 0 にして全関数の値を出させる。"""
    return [*prefix, "check", "--isolated", "--no-cache", "--exit-zero",
            "--select", ",".join(RUFF_FIELDS),
            "--config", "lint.mccabe.max-complexity=0",
            "--config", "lint.pylint.max-branches=0",
            "--config", "lint.pylint.max-returns=0",
            "--config", "lint.pylint.max-args=0",
            "--config", "lint.pylint.max-statements=0",
            "--output-format", "json", *files]


def complexipy_argv(prefix: list[str], files: list[str], output: str, cache_dir: str) -> list[str]:
    """complexipy の起動。上限を外して終了コード 1 を出させず、キャッシュを一時ディレクトリへ向ける。"""
    return [*prefix, *files, "--output-format", "json", "--output", output, "-q",
            "--max-complexity-allowed", "1000000", "--no-ignore", "--cache-dir", cache_dir]


def lizard_argv(prefix: list[str], files: list[str]) -> list[str]:
    return [*prefix, "--csv", *files]


def symilar_argv(prefix: list[str], files: list[str]) -> list[str]:
    """symilar の起動。引数は pylint の duplicate-code（R0801）の既定と同じ扱いにする。"""
    return [*prefix, "-d", str(DUPLICATE_MIN_LINES), "-i", "--ignore-docstrings",
            "--ignore-imports", "--ignore-signatures", *files]


def jscpd_argv(prefix: list[str], abs_files: list[str], output_dir: str) -> list[str]:
    """jscpd の起動。ファイルを明示して渡し、`.gitignore` を二重に当てない。"""
    return [*prefix, *abs_files, "--absolute", "--no-gitignore",
            "--min-lines", str(DUPLICATE_MIN_LINES), "--reporters", "json",
            "--output", output_dir, "--silent"]


# ---------------- 出力の読み取り ----------------

def parse_ruff(
    text: str, roots: Iterable[str],
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
        if not isinstance(entry, dict) or not isinstance(entry.get("complexity"), int) \
                or not entry.get("path") or not entry.get("function_name"):
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
        self.found.append({"symbol": ".".join([*self.stack, node.name]),
                           "lineno": node.lineno, "end_lineno": end})
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
            out.append({
                "path": path, "symbol": fn["symbol"],
                "cc": values.get("cc"),
                "cognitive": cognitive.get((path, fn["symbol"])),
                "branches": values.get("branches", 0),
                "statements": values.get("statements", 0),
                "args": values.get("args", 0),
                "returns": values.get("returns", 0),
                "lines": fn["end_lineno"] - fn["lineno"] + 1,
                "role": role_of(path),
            })
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
        out.append({"path": path, "symbol": row[7], "cc": cc, "lines": length,
                    "role": role_of(path)})
    return out


def file_metrics(
    paths: Iterable[str], line_counts: dict[str, int], functions: list[dict[str, Any]],
    unreadable: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """ファイルごとの行数・関数の数・最長の関数の行。**ファイルの集合は集めた一覧が正**。"""
    skip = set(unreadable)
    by_path: dict[str, list[int]] = {}
    for fn in functions:
        by_path.setdefault(fn["path"], []).append(int(fn["lines"]))
    return [{"path": p, "lines": int(line_counts.get(p, 0)),
             "functions": len(by_path.get(p, [])),
             "max_function_lines": max(by_path.get(p, [0])),
             "role": role_of(p)}
            for p in sorted(paths) if p not in skip]


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
            locations = []
            for line in lines[i + 1:i + 1 + k]:
                loc = _SIM_LOC.match(line)
                if loc is None:
                    raise UnreadableOutput(f"symilar の場所の行を読めない: {line!r:.80}")
                path = to_relative(loc.group(1), roots) or loc.group(1)
                locations.append({"path": path, "start": int(loc.group(2)) + 1,
                                  "end": int(loc.group(3))})
            if len(locations) < k:
                raise UnreadableOutput("symilar の場所の行が足りない")
            clones.append(_clone(TOOL_SYMILAR, "python", count, locations))
            i += 1 + k
            continue
        m = _SIM_TOTAL.match(lines[i])
        if m:
            total = (int(m.group(1)), int(m.group(2)))
        i += 1
    if total is None:
        raise UnreadableOutput("symilar の出力に TOTAL の行が無い")
    return {"clones": clones, "total_lines": total[0], "duplicated_lines": total[1]}


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
                locations.append({"path": to_relative(name, roots) or name,
                                  "start": int(entry["start"]), "end": int(entry["end"])})
            clones.append(_clone(TOOL_JSCPD, str(dup.get("format") or "—"),
                                 int(dup["lines"]), locations))
        return {"clones": clones, "total_lines": int(stats.get("lines") or 0),
                "duplicated_lines": int(stats.get("duplicatedLines") or 0),
                "sources": int(stats.get("sources") or 0)}
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, AttributeError) as exc:
        raise UnreadableOutput(f"jscpd の報告を読めない（{exc}）") from exc


def tail_lines(text: str, n: int = 5) -> str:
    """標準エラーの末尾 n 行を 1 行にまとめる（`tool_failed` の詳細）。"""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return " / ".join(lines[-n:])
