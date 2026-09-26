"""提案の前に測る指標の、言語・ツール・宣言・出力の読み取り（#1319）。

**純粋な処理だけを置く。** 起動も書き出しもしない（起動と書き出しは `commands/measure.py`）。
ここにあるのは、言語の表・ツールの表（版の固定）・測定の宣言の読み取りと重ね合わせ・
ファイルの言語分け・コマンドの組み立て・ツールの出力の読み取り・Python の関数の列挙と
値の突き合わせ・測れなかった理由の識別子である。

**表にパスもリポジトリ固有の設定も入れない**（AC8）。ほかのリポジトリでも同じ既定で測る。
同じ名前の指標（循環的複雑度）でも、ツールが違えば値の意味が違う。言語をまたいで比べない。
"""
from __future__ import annotations

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
_VERSION = re.compile(r"(\d+\.\d+(?:\.\d+)*)")



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


def split_by_language(paths: Iterable[str]) -> tuple[dict[str, list[str]], int]:
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


def path_role(path: str) -> str:
    """本体（`main`）かテスト（`test`）か（決定 1）。"""
    return "test" if is_test_path(path) else "main"


# ---------------- ランナーとコマンド ----------------

def resolve_runner(command: str, which: Callable[[str], Optional[str]]) -> Optional[tuple[str, list[str]]]:
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
