"""即時読み込み・読み込みの量・指示の数の判定（`instructions-check.py` から分けた。#1142 の C7）。
"""
from __future__ import annotations

import os
import re

from instructions_lib.model import BULLET_RE, Criteria, FENCE_RE, Finding, HEADING_RE, ScopeRoot, Target
from instructions_lib.declaration import Declaration
from instructions_lib.collect import _matches_file_pattern, display_path, interprets_imports, references, resolve


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
        if not _matches_file_pattern(key, decl.import_syntax):
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

    in_fence = False
    for line in text.splitlines():
        if FENCE_RE.match(line):
            count += flush()
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        stripped = line.strip()
        if not stripped:
            count += flush()
            continue
        if HEADING_RE.match(line):
            count += flush()
            count += 1
            continue
        if stripped.startswith("|") or stripped.startswith(">") or stripped.startswith("<"):
            count += flush()
            continue
        if BULLET_RE.match(line):
            count += flush()
            count += 1
            continue
        paragraph.append(stripped)
    count += flush()
    return count


def count_findings(target: Target, text: str) -> int:
    return count_instructions(text)
