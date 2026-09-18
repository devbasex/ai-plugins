#!/usr/bin/env python3
"""エージェント向け指示書を適切に保つ検査（#554）。

指示書（既定では `AGENTS.md` / `CLAUDE.md` / `KIRO.md`）は、**根に置いたものをそのランタイムの
全セッションと全サブエージェントが毎回読む**。何を書いてもよい場所ではなく、毎回の読み込みの量を
そのまま増やす場所である。いま指示書を機械で見ているものが無いため、出た版の判断・参照先の
消えた即時読み込み・総量の増加は、起きても誰も止めない。

**何を見るかは外部の一次情報から導く。** 観点・強さ・出典は `data/instruction-criteria.json` が
持ち、ここには判定の手続きだけを置く（観点を 1 つ足すことと、手続きを変えることを別の変更にする）。

**判定の強さはリポジトリ側の宣言（`.ndf/instructions.json`）が決める。** 宣言が無くても
動くのは、リポジトリの性質によらず誤りである「参照先の無い即時読み込み」と、落とさずに
数える「読み込みの量」「指示の数」だけである。宣言を書くまで使えない検査は、入れた利用者が
最初に外す。

**この検査はどのファイルも書き換えず、課題も立てない。** 指摘へ扱いの印（`直す` / `起票` /
`報告`）を載せるまでで、書き込みと投稿は呼び出し側が行う。

    python3 instructions-check.py --root .
    python3 instructions-check.py --root . --scope project --scope user --scope plugins
    python3 instructions-check.py --refresh        # 通信する唯一の経路

終了コード:

    0  指摘が無い（対象が 1 本も無いときも 0）
    1  指摘がある
    2  確かめられなかった（宣言の不正・観点のデータを読めない・版を取れない・git が無い）
    3  呼び出しの誤り（知らない引数）

**2 を 0 へ畳まない。** 確かめられなかったことを、通ったと報告しない。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import refresh as refresh_lib  # noqa: E402

# 宣言の既定値。**配布物は閾値を持たない** ── ランタイムごとに助言の値が違い、版で動く。
# 落とすのは利用者が宣言に書いたときだけである。
DEFAULT_FILES = ["AGENTS.md", "CLAUDE.md", "KIRO.md"]
# 即時読み込みの記法を解釈する指示書。Codex / Kiro はただの文字列として扱うため、
# 解釈しない指示書へこの判定を掛けると、書いてよい文字列で落ちる。
DEFAULT_IMPORT_SYNTAX = ["CLAUDE.md"]
# たどる深さの上限。根拠は Claude Code のドキュメント（memory）が最大 4 段と書いていること。
DEFAULT_IMPORT_DEPTH = 4
DEFAULT_REVIEW_INTERVAL_DAYS = 90
SUPPORTED_DECLARATION_VERSIONS = (1,)
SUPPORTED_CRITERIA_VERSIONS = (1,)
DECLARATION_RELATIVE_PATH = ".ndf/instructions.json"

SCOPES = ("project", "user", "plugins")
ACTION_FIX = "直す"
ACTION_FILE = "起票"
ACTION_REPORT = "報告"

# 観点の id と判定の手続きの対応。**一覧に無い id があれば終了コード 2 で止まる。**
KNOWN_CRITERIA = {
    "broken-import": "import_findings",
    "unlisted-import": "import_findings",
    "released-version-paragraph": "version_findings",
    "read-size": "budget_findings",
    "read-size-budget": "budget_findings",
    "instruction-count": "count_findings",
}

# 版数の形（semver）。数字 3 つと、任意の接尾辞（`.` で割った各要素が空でなく、英数字と
# `-` だけで、数として読める要素に先頭の 0 が無い）。
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
# 行の書き出しに現れる版数。`1.2.3.4` のような別の形へは当たらない。
VERSION_AT_START = re.compile(
    r"^v?(?P<version>\d+\.\d+\.\d+(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?)(?![0-9A-Za-z.-])")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
BULLET_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+\.\s+)")
# 段落の書き出しから読み飛ばす印（箇条書きの記号と強調）。
LEAD_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+\.\s+)?[*_~]*")
# 即時読み込みの参照。**`@` の直前が行頭・空白・`*`・`_`・`~` のものだけを参照とする**
# （Claude Code 2.1.274 で 8 通りを実測した範囲）。
IMPORT_RE = re.compile(r"(?:(?<=^)|(?<=[\s*_~]))@([A-Za-z0-9._\-/~]+)")


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


@dataclass
class Declaration:
    """リポジトリ側の宣言。**無くても作る**（既定値を持ち、`None` の判定は動かない）。"""

    present: bool = False
    files: list[str] = field(default_factory=lambda: list(DEFAULT_FILES))
    imports: dict | None = None
    released: dict | None = None
    decisions: str | None = None
    pending_marker: str | None = None
    budget: dict | None = None
    scopes: dict = field(default_factory=dict)
    import_syntax: list[str] = field(default_factory=lambda: list(DEFAULT_IMPORT_SYNTAX))
    import_depth: int = DEFAULT_IMPORT_DEPTH
    refresh_timeout_seconds: int = refresh_lib.DEFAULT_TIMEOUT_SECONDS
    review_interval_days: int = DEFAULT_REVIEW_INTERVAL_DAYS
    reviewed_at: str | None = None

    @staticmethod
    def load(root: Path) -> "Declaration":
        path = root / DECLARATION_RELATIVE_PATH
        if not path.is_file():
            return Declaration()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CheckError(f"{DECLARATION_RELATIVE_PATH} を読めない: {exc}") from exc
        if not isinstance(raw, dict):
            raise CheckError(f"{DECLARATION_RELATIVE_PATH} の中身がオブジェクトではない")
        return _declaration_from(raw)


def _declaration_from(raw: dict) -> Declaration:
    version = raw.get("version")
    if version not in SUPPORTED_DECLARATION_VERSIONS:
        raise CheckError(f"宣言の version が無いか未対応である: {version!r}")

    decl = _populate_declaration(raw)
    _apply_field_constraints(raw, decl)
    if decl.released is not None:
        _validate_released(decl.released)
    if decl.imports is not None:
        _validate_imports(decl.imports, "imports")
    _validate_scopes(decl.scopes)
    return decl


def _populate_declaration(raw: dict) -> Declaration:
    decl = Declaration(present=True)
    decl.files = _typed(raw, "files", list, decl.files)
    if any(not isinstance(name, str) for name in decl.files):
        raise CheckError("宣言の files は文字列の配列である")
    decl.imports = _typed(raw, "imports", dict, None)
    decl.released = _typed(raw, "released", dict, None)
    decl.decisions = _typed(raw, "decisions", str, None)
    decl.budget = _typed(raw, "budget", dict, None)
    decl.scopes = _typed(raw, "scopes", dict, {})
    decl.import_syntax = _typed(raw, "import_syntax", list, decl.import_syntax)
    if any(not isinstance(name, str) for name in decl.import_syntax):
        raise CheckError("宣言の import_syntax は文字列の配列である")
    decl.reviewed_at = _typed(raw, "reviewed_at", str, None)
    return decl


def _apply_field_constraints(raw: dict, decl: Declaration) -> None:
    if "pending_marker" in raw:
        marker = _typed(raw, "pending_marker", str, None)
        # **空文字列はすべての段落へ当たり、出た版の判定が働かなくなる。**
        if marker == "":
            raise CheckError("宣言の pending_marker が空文字列である")
        decl.pending_marker = marker

    decl.import_depth = _positive(raw, "import_depth", decl.import_depth)
    decl.refresh_timeout_seconds = _positive(
        raw, "refresh_timeout_seconds", decl.refresh_timeout_seconds)
    decl.review_interval_days = _positive(
        raw, "review_interval_days", decl.review_interval_days)

    if decl.budget is not None and "bytes" in decl.budget:
        decl.budget["bytes"] = _positive(decl.budget, "bytes", 0)
    if decl.reviewed_at is not None:
        _parse_date(decl.reviewed_at, "reviewed_at")


def _validate_scopes(scopes: dict) -> None:
    for name in ("user", "plugins"):
        entries = scopes.get(name)
        if entries is None:
            continue
        if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
            raise CheckError(f"宣言の scopes.{name} はオブジェクトの配列である")
        for index, entry in enumerate(entries):
            allow = entry.get("imports")
            if allow is not None:
                _validate_imports(allow, f"scopes.{name}[{index}].imports")


def _validate_imports(allow, label: str) -> None:
    """許可の形を `{指示書: {参照先: 理由}}` まで見る。

    **読み取りの時点で止める。** 途中まで判定してから型の誤りで落ちると、宣言の不正が
    終了コード 2 ではなく未捕捉の例外として現れる。
    """
    if not isinstance(allow, dict):
        raise CheckError(f"宣言の {label} はオブジェクトである")
    for key, entries in allow.items():
        if not isinstance(entries, dict):
            raise CheckError(f"宣言の {label}.{key} は "
                             "{参照先: 理由} のオブジェクトである")
        for name, reason in entries.items():
            if not isinstance(reason, str):
                raise CheckError(f"宣言の {label}.{key}.{name} の理由は文字列である")


def _typed(raw: dict, key: str, kind: type, fallback):
    if key not in raw or raw[key] is None:
        return fallback
    value = raw[key]
    if not isinstance(value, kind) or isinstance(value, bool):
        raise CheckError(f"宣言の {key} の型が違う（{kind.__name__} を期待した）")
    return value


def _positive(raw: dict, key: str, fallback: int) -> int:
    if key not in raw or raw[key] is None:
        return fallback
    value = raw[key]
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        # 0 や負の値を「制限なし」と読むか「すべて落とす」と読むかは実装で分かれる。
        raise CheckError(f"宣言の {key} は 1 以上の整数である: {value!r}")
    return value


def _parse_date(value: str, label: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat(value)
    except ValueError as exc:
        raise CheckError(f"{label} が日付として読めない: {value!r}") from exc


def _validate_released(released: dict) -> None:
    source = released.get("source")
    if source not in ("changelog", "tags"):
        raise CheckError(f"released.source は changelog か tags である: {source!r}")
    pattern = released.get("pattern")
    if not isinstance(pattern, str):
        raise CheckError("released.pattern が無いか文字列ではない")
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        raise CheckError(f"released.pattern が正規表現として読めない: {exc}") from exc
    if "version" not in (compiled.groupindex or {}):
        raise CheckError("released.pattern に名前付きの捕捉 (?P<version>…) が無い")
    if source == "changelog" and not isinstance(released.get("path"), str):
        raise CheckError("released.path が無いか文字列ではない")


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


# --- 指示書を集める ----------------------------------------------------------

def _matches(rel: str, patterns: list[str]) -> bool:
    """`/` を含まない値はファイル名の一致、含む値は根からの相対パスの一致。"""
    name = rel.rsplit("/", 1)[-1]
    for pattern in patterns:
        if "/" in pattern:
            if rel == pattern:
                return True
        elif name == pattern:
            return True
    return False


def _expand(value: str, root: Path) -> Path:
    """先頭の `~` を home へ広げ、相対で書かれた値は `--root` からの相対として読む。"""
    path = Path(os.path.expanduser(value))
    return path if path.is_absolute() else (root / path)


def collect_project(root: Path, decl: Declaration) -> ScopeRoot:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True, text=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CheckError(f"git が使えない（{root}）: {exc}") from exc
    scope_root = ScopeRoot(root=root, scope="project", imports=decl.imports)
    # 非 ASCII のパスを 8 進で表さない形で受け取るため `-z` を使う。
    for rel in sorted(entry for entry in result.stdout.split("\0") if entry):
        if not _matches(rel, decl.files):
            continue
        path = root / rel
        # **追跡されていても、実体が根の外を指す symlink は読まない。** 字句の上では
        # 中にある指示書でも、開いた先は作業ツリーの外である。
        if not path.is_file() or not _inside_root(str(path), root):
            continue
        scope_root.targets.append(Target(
            path=path, rel=rel, scope="project", root=root,
            is_root_file="/" not in rel, imports=decl.imports,
        ))
    return scope_root


def _build_scope_metadata(
    entry: dict, scope: str,
) -> tuple[Source | None, dict | None]:
    source = None
    if scope == "plugins":
        source = Source(
            name=str(entry.get("name", "")), version=str(entry.get("version", "")),
            origin=str(entry.get("origin", "")), update=str(entry.get("update", "")),
            ndf=bool(entry.get("ndf", False)),
        )
    allow = entry.get("imports")
    if allow is not None and not isinstance(allow, dict):
        raise CheckError(f"宣言の scopes.{scope} の imports がオブジェクトではない")
    return source, allow


def _enumerate_scope_files(
    located: Path, scope: str, allow: dict | None, source: Source | None,
) -> tuple[ScopeRoot, list[Path]] | None:
    if located.is_file():
        return ScopeRoot(located.parent, scope, allow, source), [located]
    if located.is_dir():
        scope_root = ScopeRoot(located, scope, allow, source)
        files = sorted(p for p in located.rglob("*") if p.is_file())
        return scope_root, files
    return None


def _collect_scope_entry(
    entry: dict, scope: str, root: Path, decl: Declaration,
) -> ScopeRoot | None:
    """`scopes` の 1 件から集める。位置が無ければ `None` を返す。"""
    raw_path = entry.get("path")
    if not isinstance(raw_path, str):
        raise CheckError(f"宣言の scopes.{scope} の path が無いか文字列ではない")
    located = _expand(raw_path, root)
    source, allow = _build_scope_metadata(entry, scope)
    found = _enumerate_scope_files(located, scope, allow, source)
    if found is None:
        return None
    scope_root, files = found
    for path in files:
        rel = path.relative_to(scope_root.root).as_posix()
        if located.is_dir() and not _matches(rel, decl.files):
            continue
        if not _inside_root(str(path), scope_root.root):
            continue
        scope_root.targets.append(Target(
            path=path, rel=rel, scope=scope, root=scope_root.root,
            is_root_file="/" not in rel, imports=allow, source=source,
        ))
    return scope_root


def collect_declared(scope: str, root: Path, decl: Declaration) -> list[ScopeRoot]:
    """`scopes` が挙げた位置から集める。**宣言に無ければそのスコープは走査しない。**"""
    roots: list[ScopeRoot] = []
    for entry in decl.scopes.get(scope) or []:
        scope_root = _collect_scope_entry(entry, scope, root, decl)
        if scope_root is not None:
            roots.append(scope_root)
    return roots


def display_path(target: Target) -> str:
    return target.rel if target.scope == "project" else str(target.path)


# --- 本文の読み方 ------------------------------------------------------------

def mask_code(lines: list[str]) -> list[str]:
    """コードブロックとコードスパンを空白へ潰す。**位置は保つ**（行と桁が動かない）。"""
    masked: list[str] = []
    in_fence = False
    for line in lines:
        if FENCE_RE.match(line):
            in_fence = not in_fence
            masked.append(" " * len(line))
            continue
        if in_fence:
            masked.append(" " * len(line))
            continue
        out: list[str] = []
        span = False
        for char in line:
            if char == "`":
                span = not span
                out.append(" ")
            else:
                out.append(" " if span else char)
        masked.append("".join(out))
    return masked


def interprets_imports(target: Target, decl: Declaration) -> bool:
    return _matches(target.rel, decl.import_syntax)


def references(text: str) -> list[tuple[int, str]]:
    """本文から即時読み込みの参照を拾う。返すのは（行番号, 書かれたとおりの名前）。"""
    found: list[tuple[int, str]] = []
    for number, line in enumerate(mask_code(text.splitlines()), start=1):
        for match in IMPORT_RE.finditer(line):
            name = match.group(1).rstrip("._~")
            if name:
                found.append((number, name))
    return found


def _inside_root(candidate: str, root: Path) -> bool:
    """字句のパスと実体のパスが、どちらも根の中に収まるか。"""
    try:
        Path(candidate).relative_to(root)
        # **読む前に実体のパスが根の中に収まることを確かめる。** 字句の上では中でも、
        # 追跡された symlink が外を指していれば、開いた先は作業ツリーの外である。
        Path(os.path.realpath(candidate)).relative_to(Path(os.path.realpath(root)))
    except ValueError:
        return False
    return True


def resolve(name: str, base: Path, root: Path) -> Path | None:
    """参照先を解く。**解けないもの（`~`・根の外）は `None`** で、存在を判定しない。"""
    if name.startswith("~") or name.startswith("/"):
        return None
    candidate = os.path.normpath(str(base / name))
    if not _inside_root(candidate, root):
        return None
    return Path(candidate)


# --- 判定: 即時読み込み ------------------------------------------------------

def import_findings(target: Target, text: str, decl: Declaration,
                    criteria: Criteria) -> list[Finding]:
    findings: list[Finding] = []
    if not interprets_imports(target, decl):
        return findings
    allow = target.imports
    allowed = (allow or {}).get(target.rel) if allow is not None else None
    for line, name in references(text):
        resolved = resolve(name, target.path.parent, target.root)
        if criteria.enabled("broken-import") and resolved is not None \
                and not resolved.exists():
            findings.append(Finding(
                "broken-import",
                f"@{name} の参照先が無い。参照を消すか実在するパスへ直す",
                target.scope, display_path(target), line, target.source))
        if criteria.enabled("unlisted-import") and allow is not None \
                and name not in (allowed or {}):
            findings.append(Finding(
                "unlisted-import",
                f"@{name} は許可していない即時読み込み。"
                "@ を外すか imports へ理由とともに足す",
                target.scope, display_path(target), line, target.source))
    return findings


def stale_allowance_findings(scope_root: ScopeRoot, decl: Declaration,
                             criteria: Criteria) -> list[Finding]:
    """宣言の陳腐化。**効かない許可と、指し先の無い許可**を指摘する。"""
    findings: list[Finding] = []
    if scope_root.imports is None or not criteria.enabled("unlisted-import"):
        return findings
    for key, entries in scope_root.imports.items():
        if not _matches(key, decl.import_syntax):
            findings.append(Finding(
                "unlisted-import",
                f"imports の {key} は即時読み込みを解釈しない指示書で、効かない許可である。"
                "import_syntax へ足すか、この行を外す",
                scope_root.scope, key, None, scope_root.source))
            continue
        base = (scope_root.root / key).parent
        for name in (entries or {}):
            resolved = resolve(name, base, scope_root.root)
            # 存在を判定しないパス（`~`・根の外）の許可は、存在しないことを理由に
            # 指摘しない。
            if resolved is not None and not resolved.exists():
                findings.append(Finding(
                    "unlisted-import",
                    f"imports の {key} が許す @{name} の指し先が無い。"
                    "宣言の imports からその行を外す",
                    scope_root.scope, key, None, scope_root.source))
    return findings


# --- 判定: 読み込みの量 ------------------------------------------------------

def read_size(target: Target, decl: Declaration) -> tuple[int, list[tuple[str, int]]]:
    """根の指示書 1 本の読み込みの量。**同じ参照先は 1 回だけ数え、循環でも止まる。**"""
    total = target.path.stat().st_size
    breakdown: list[tuple[str, int]] = []
    if not interprets_imports(target, decl):
        return total, breakdown
    seen = {os.path.realpath(target.path)}
    frontier = [(target.path, 0)]
    while frontier:
        current, depth = frontier.pop(0)
        if depth >= decl.import_depth:
            continue
        try:
            text = current.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for _, name in references(text):
            resolved = resolve(name, current.parent, target.root)
            if resolved is None or not resolved.is_file():
                continue
            real = os.path.realpath(resolved)
            if real in seen:
                continue
            seen.add(real)
            size = resolved.stat().st_size
            total += size
            rel = os.path.relpath(str(resolved), str(target.root))
            breakdown.append((rel, size))
            frontier.append((resolved, depth + 1))
    return total, breakdown


def budget_findings(sizes: dict[str, int], targets: list[Target], decl: Declaration,
                    criteria: Criteria) -> list[Finding]:
    """上限は**根の指示書ごとに**掛ける。1 本でも超えれば指摘する。"""
    findings: list[Finding] = []
    if not criteria.enabled("read-size-budget") or not decl.budget:
        return findings
    limit = decl.budget.get("bytes")
    if not isinstance(limit, int):
        return findings
    for target in targets:
        if not target.is_root_file:
            continue
        total = sizes[display_path(target)]
        if total > limit:
            findings.append(Finding(
                "read-size-budget",
                f"読み込みの量が上限を超えた（{total:,} > {limit:,} バイト）。"
                "現行でない記述を退避先へ移すか、宣言の budget.bytes を直す",
                target.scope, display_path(target), None, target.source))
    return findings


# --- 判定: 指示の数 ----------------------------------------------------------

SENTENCE_END = "。！？!?"


@dataclass
class MarkdownLine:
    """分類済みの 1 行。`count_instructions` と `paragraph_starts` が同じ規則で読む。"""

    number: int
    line: str
    stripped: str
    kind: str  # fence / blank / heading / table / quote / html / bullet / text
    heading_text: str = ""


def classify_markdown_lines(text: str) -> Iterator[MarkdownLine]:
    """Markdown の各行をフェンス状態を追いながら種別へ分類する。

    フェンスの中の行 (`fence` 自身を含む) は返さない。両関数が別々に持っていた
    フェンス・空行・見出し・表・引用・HTML・箇条書き・通常文の分類規則を 1 か所に
    まとめる。`html`（行頭が `<`）は種別として区別し、数え方の違いは呼び出し側が決める。
    """
    in_fence = False
    for number, line in enumerate(text.splitlines(), start=1):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            yield MarkdownLine(number, line, "", "fence")
            continue
        if in_fence:
            continue
        stripped = line.strip()
        if not stripped:
            yield MarkdownLine(number, line, "", "blank")
            continue
        heading = HEADING_RE.match(line)
        if heading:
            yield MarkdownLine(number, line, stripped, "heading", heading.group(2))
            continue
        if stripped.startswith("|"):
            yield MarkdownLine(number, line, stripped, "table")
            continue
        if stripped.startswith(">"):
            yield MarkdownLine(number, line, stripped, "quote")
            continue
        if stripped.startswith("<"):
            yield MarkdownLine(number, line, stripped, "html")
            continue
        if BULLET_RE.match(line):
            yield MarkdownLine(number, line, stripped, "bullet")
            continue
        yield MarkdownLine(number, line, stripped, "text")


def count_instructions(text: str) -> int:
    """箇条書きの項目と段落の文を数える。**表・引用・コードブロック・HTML は数えない。**"""
    count = 0
    paragraph: list[str] = []

    def flush() -> int:
        if not paragraph:
            return 0
        joined = "".join(paragraph)
        parts = [p for p in re.split(f"[{re.escape(SENTENCE_END)}]", joined) if p.strip()]
        paragraph.clear()
        return len(parts)

    for item in classify_markdown_lines(text):
        if item.kind == "text":
            paragraph.append(item.stripped)
            continue
        # フェンス・空行・見出し・表・引用・HTML・箇条書きはいずれも段落を区切る。
        count += flush()
        if item.kind in ("heading", "bullet"):
            count += 1
    count += flush()
    return count


def count_findings(target: Target, text: str) -> int:
    return count_instructions(text)


# --- 判定: 出た版の段落 ------------------------------------------------------

def version_key(version: str) -> tuple:
    """semver の順序。**数として読める要素は読めない要素より前**に来る。"""
    base, _, suffix = version.partition("-")
    numbers = tuple(int(part) for part in base.split("."))
    if not suffix:
        # 接尾辞の無い版は、同じ基底の接尾辞付きより後である。
        return (numbers, 1, ())
    parts = []
    for element in suffix.split("."):
        if element.isdigit():
            parts.append((0, int(element), ""))
        else:
            parts.append((1, 0, element))
    return (numbers, 0, tuple(parts))


def base_triple(version: str) -> tuple[int, int, int]:
    base = version.partition("-")[0]
    major, minor, patch = base.split(".")
    return (int(major), int(minor), int(patch))


def _read_changelog_lines(root: Path, raw: str) -> list[str]:
    candidate = os.path.normpath(str(root / raw))
    if not _inside_root(candidate, root):
        raise CheckError(f"released.path が根の外を指す: {raw}")
    try:
        return Path(candidate).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise CheckError(f"released.path を読めない: {raw}（{exc}）") from exc


def _read_tag_lines(root: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "tag"],
            capture_output=True, text=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CheckError(f"git tag を読めない: {exc}") from exc
    return result.stdout.splitlines()


def released_versions(root: Path, released: dict) -> list[str]:
    pattern = re.compile(released["pattern"])
    if released["source"] == "changelog":
        lines = _read_changelog_lines(root, released["path"])
    else:
        lines = _read_tag_lines(root)

    versions: list[str] = []
    for line in lines:
        match = pattern.search(line)
        if not match:
            continue
        value = match.group("version")
        if not VERSION_RE.match(value) or not _valid_suffix(value):
            # その行を読み飛ばさずに止める。判定できないことを通過にしない。
            raise CheckError(f"捕捉した値が版数の形ではない: {value!r}")
        versions.append(value)
    return versions


def _valid_suffix(version: str) -> bool:
    suffix = version.partition("-")[2]
    if not suffix:
        return True
    for element in suffix.split("."):
        if not element or not re.fullmatch(r"[0-9A-Za-z-]+", element):
            return False
        if element.isdigit() and len(element) > 1 and element.startswith("0"):
            return False
    return True


def paragraph_starts(text: str) -> list[tuple[int, str, bool]]:
    """（行番号, 本文, 見出しか）。**段落の先頭行と見出しだけ**を返す。"""
    starts: list[tuple[int, str, bool]] = []
    previous = "blank"
    for item in classify_markdown_lines(text):
        if item.kind == "fence":
            # 開きフェンスの次は段落の先頭になりうる、閉じフェンスの次は続き扱い。
            previous = "fence" if previous != "fence" else "blank"
            continue
        if item.kind == "blank":
            previous = "blank"
            continue
        if item.kind == "heading":
            starts.append((item.number, item.heading_text, True))
            previous = "blank"
            continue
        if item.kind in ("table", "quote"):
            # 表の行・引用の行は段落の先頭として扱わない。
            previous = "other"
            continue
        # 箇条書き・通常文・HTML（行頭 `<`）。箇条書きか直前が空行なら段落の先頭。
        if item.kind == "bullet" or previous == "blank":
            starts.append((item.number, LEAD_RE.sub("", item.line, count=1), False))
        previous = "text"
    return starts


def version_findings(target: Target, text: str, latest: str,
                     decl: Declaration, criteria: Criteria) -> list[Finding]:
    findings: list[Finding] = []
    if not criteria.enabled("released-version-paragraph"):
        return findings
    latest_base = base_triple(latest)
    where = decl.decisions or "退避先の文書"
    for number, body, _is_heading in paragraph_starts(text):
        match = VERSION_AT_START.match(body.strip())
        if not match:
            continue
        version = match.group("version")
        rest = body.strip()[match.end():].lstrip()
        pending = bool(decl.pending_marker) and rest.startswith(decl.pending_marker)
        if pending:
            hit = base_triple(version) < latest_base
            reason = (f"{version} は版が決まる前の段落で、基底が最新（{latest}）未満である")
        else:
            hit = base_triple(version) <= latest_base
            reason = f"{version} の段落が残っている（最新は {latest}）"
        if not hit:
            continue
        findings.append(Finding(
            "released-version-paragraph",
            f"{reason}。次の見出しまでを {where} へ移す",
            target.scope, display_path(target), number, target.source))
    return findings


# --- 扱いの判定 --------------------------------------------------------------

def action_of(finding: Finding, in_ndf_repo: bool) -> str:
    """扱いは**スコープと、NDF の開発リポジトリかどうか**の 2 つで決まる。"""
    if in_ndf_repo:
        return ACTION_FIX
    if finding.scope in ("project", "user"):
        return ACTION_FIX
    if finding.source is not None and finding.source.ndf:
        return ACTION_FILE
    return ACTION_REPORT


def in_development_repo(root: Path) -> bool:
    """**実行しているスクリプトの実体が `--root` の下にあるか**で判定する。

    取得元の名前・リポジトリ名・リモートの URL は fork と移設で変わる。
    """
    script = Path(os.path.realpath(__file__))
    try:
        script.relative_to(Path(os.path.realpath(root)))
    except ValueError:
        return False
    return True


def format_finding(finding: Finding, action: str, criteria: Criteria) -> str:
    if finding.path and finding.line:
        where = f"{finding.path}:{finding.line}: "
    elif finding.path:
        where = f"{finding.path}: "
    else:
        # どちらも持たないものは `<位置>: ` ごと省く。
        where = ""
    line = f"ERROR: [{action}] {where}{finding.message}"
    if action == ACTION_REPORT:
        # **報告だけで終わらせない。** 何を更新すればよいかと、助言の出どころを添える。
        # **持っている値だけを `/` で連ねる** ── 配布元を持たない指摘で区切りだけが残らない。
        parts: list[str] = []
        if finding.source is not None:
            parts.append(f"配布元 {finding.source.name} {finding.source.version}")
            parts.append(finding.source.origin)
            parts.append(f"更新: {finding.source.update}")
        parts.append(f"観点 {finding.criterion_id}")
        origins = criteria.origins_of(finding.criterion_id)
        if origins:
            parts.append("出典 " + "、".join(origins))
        line += "（" + " / ".join(parts) + "）"
    if action == ACTION_FILE and finding.source is not None:
        line += f"（起票 {issue_title(finding)} / 宛先 {finding.source.origin}）"
    return line


def issue_title(finding: Finding) -> str:
    return f"[instructions] {finding.criterion_id} {finding.path or '-'}"


# --- 調べ直し ----------------------------------------------------------------

def run_refresh(criteria: Criteria, timeout: float, out) -> int:
    lines, failed = refresh_lib.refresh(criteria.sources, timeout)
    for line in lines:
        print(line, file=out)
    # 成功した分を理由に 0 へ畳まない。
    return 2 if failed else 0


# --- 実行 --------------------------------------------------------------------

class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # noqa: D102
        self.print_usage(sys.stderr)
        print(f"ERROR: {message}", file=sys.stderr)
        raise SystemExit(3)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(description="エージェント向け指示書を適切に保つ検査")
    parser.add_argument("--root", default=".", help="リポジトリの根（既定は現在地）")
    parser.add_argument("--scope", action="append", choices=SCOPES,
                        help="走査するスコープ（重ねて指定できる。既定は project）")
    parser.add_argument("--report", action="store_true", help="たどった先の内訳を足す")
    parser.add_argument("--refresh", action="store_true",
                        help="観点の出典を取得して提示する（通信する唯一の経路）")
    parser.add_argument("--refresh-timeout", type=float, default=None,
                        help="出典 1 件あたりの待ち（秒）")
    parser.add_argument("--criteria", default=None,
                        help="観点のデータの位置（既定は配布物の data/ の下）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve()
    criteria_path = Path(args.criteria) if args.criteria else (
        Path(__file__).resolve().parent / "data" / "instruction-criteria.json")

    try:
        criteria = load_criteria(criteria_path)
        decl = Declaration.load(root)
        if args.refresh:
            timeout = args.refresh_timeout or decl.refresh_timeout_seconds
            return run_refresh(criteria, timeout, sys.stdout)
        return check(root, decl, criteria, args)
    except CheckError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


@dataclass
class Measurements:
    """対象ごとの計測と判定の結果。段の間で持ち回る。"""

    findings: list[Finding] = field(default_factory=list)
    sizes: dict[str, int] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    breakdowns: dict[str, list[tuple[str, int]]] = field(default_factory=dict)


@dataclass
class ReportInput:
    """報告に必要な対象・計測結果・判定条件。"""

    targets: list[Target]
    measurements: Measurements
    notes: list[str]
    criteria: Criteria
    in_ndf_repo: bool
    with_report: bool


def resolve_latest(root: Path, decl: Declaration, criteria: Criteria) -> str | None:
    """`released` の宣言から最新の版を解く。**取れなければ止まる。**"""
    # **止まるなら、何も判定する前に止まる。** 版を取れないことは走査の前に分かる。
    if decl.released is None or not criteria.enabled("released-version-paragraph"):
        return None
    versions = released_versions(root, decl.released)
    if not versions:
        raise CheckError("released の宣言から版を 1 つも取れない")
    return max(versions, key=version_key)


def collect_scope_roots(root: Path, decl: Declaration,
                        scopes: list[str]) -> list[ScopeRoot]:
    """走査するスコープの根と、その配下の対象を集める。"""
    scope_roots: list[ScopeRoot] = []
    if "project" in scopes:
        scope_roots.append(collect_project(root, decl))
    for scope in ("user", "plugins"):
        if scope in scopes:
            scope_roots.extend(collect_declared(scope, root, decl))
    return scope_roots


def measure_targets(targets: list[Target], latest: str | None,
                    decl: Declaration, criteria: Criteria) -> Measurements:
    """対象ごとの findings・sizes・counts・breakdowns を作る。"""
    result = Measurements()
    for target in targets:
        text = target.path.read_text(encoding="utf-8", errors="replace")
        result.findings.extend(import_findings(target, text, decl, criteria))
        if latest is not None and target.scope == "project":
            # 版の段落の判定はプロジェクトのスコープだけに掛ける。`released` は
            # そのリポジトリの版を指すため、他の製品の版数を古いとは言わない。
            result.findings.extend(
                version_findings(target, text, latest, decl, criteria))
        total, breakdown = read_size(target, decl)
        result.sizes[display_path(target)] = total
        result.breakdowns[display_path(target)] = breakdown
        result.counts[display_path(target)] = count_findings(target, text)
    return result


def finalize(scope_roots: list[ScopeRoot], targets: list[Target],
             result: Measurements, decl: Declaration,
             criteria: Criteria) -> list[str]:
    """スコープ横断の findings を足し、注記を作る。注記を返す。"""
    for scope_root in scope_roots:
        result.findings.extend(stale_allowance_findings(scope_root, decl, criteria))
    result.findings.extend(budget_findings(result.sizes, targets, decl, criteria))

    notes: list[str] = []
    if not decl.present:
        notes.append(f"NOTE: 宣言（{DECLARATION_RELATIVE_PATH}）が無いため、"
                     "出た版と許可の判定は動かない")
    stale = criteria_is_stale(criteria, decl)
    if stale:
        notes.append(f"NOTE: 観点の一覧を最後に調べ直したのは {stale} である"
                     "（--refresh で出典を読み直す）")
    return notes


def check(root: Path, decl: Declaration, criteria: Criteria, args) -> int:
    scopes = args.scope or ["project"]

    latest = resolve_latest(root, decl, criteria)
    scope_roots = collect_scope_roots(root, decl, scopes)
    targets = [t for sr in scope_roots for t in sr.targets]
    if not targets:
        print("対象の指示書が 1 本も無い")
        return 0

    result = measure_targets(targets, latest, decl, criteria)
    notes = finalize(scope_roots, targets, result, decl, criteria)

    return report(ReportInput(
        targets=targets,
        measurements=result,
        notes=notes,
        criteria=criteria,
        in_ndf_repo=in_development_repo(root),
        with_report=args.report,
    ))


def criteria_is_stale(criteria: Criteria, decl: Declaration) -> str | None:
    anchor = decl.reviewed_at or criteria.checked_at
    if not anchor:
        return None
    try:
        day = datetime.date.fromisoformat(anchor)
    except ValueError:
        return None
    age = (datetime.date.today() - day).days
    return anchor if age > decl.review_interval_days else None


def report(report_input: ReportInput) -> int:
    targets = report_input.targets
    measurements = report_input.measurements
    roots = [t for t in targets if t.is_root_file]
    print(f"指示書 {len(targets)} 本（根 {len(roots)} / 配下 {len(targets) - len(roots)}）")
    show_size = report_input.criteria.enabled("read-size")
    show_count = report_input.criteria.enabled("instruction-count")
    for target in targets:
        key = display_path(target)
        parts = [key]
        if show_size and target.is_root_file:
            parts.append(f"{measurements.sizes[key]:,} バイト")
        if show_count:
            parts.append(f"指示 {measurements.counts[key]}")
        if len(parts) > 1:
            print("  ".join(parts))
        if report_input.with_report and show_size and target.is_root_file:
            for rel, size in measurements.breakdowns[key]:
                print(f"    {rel}  {size:,} バイト")
    for note in report_input.notes:
        print(note)

    failed = 0
    for finding in measurements.findings:
        action = action_of(finding, report_input.in_ndf_repo)
        line = format_finding(finding, action, report_input.criteria)
        if report_input.criteria.is_error(finding.criterion_id):
            print(line, file=sys.stderr)
            failed += 1
        else:
            print(line.replace("ERROR:", "NOTE:", 1))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
