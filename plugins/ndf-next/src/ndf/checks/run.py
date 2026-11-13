"""検査 1 本の入口（副命令 `check`）。見るファイルを集め、構文木を 1 回ずつ作り、すべての規則を当てる。

    ndf check [--base <起点>] [--event <GitHub の事象の JSON>]

`--base` があれば差分の検査（I2）も行う。PR 本文は `--event`（既定は環境変数 `GITHUB_EVENT_PATH`）から読む。
結果 JSON を標準出力の最後の 1 行に出す。終了コードは 0 合格・1 違反・2 読めない（規則のデータ・構文・git）。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from ndf import identity
from ndf.cli import EXIT_UNREADABLE
from ndf.checks import facts, invariants, marks, smells, structure, words
from ndf.checks.model import CheckError, Finding, Scope, Source, load_rules

EXIT_OK = 0
EXIT_VIOLATION = 1
SKIP_DIRS = frozenset({"__pycache__", ".venv", ".pytest_cache", ".git", ".ruff_cache"})
TEXT_SUFFIXES = frozenset({".py", ".md", ".json", ".sh", ".toml", ".yml", ".yaml", ".txt"})
SCRIPT_DIRS = ("bin", "hooks")


def collect(plugin_root: Path, repo_root: Path) -> list[Source]:
    """プラグインの下の文字のファイル（`bin/`・`hooks/` の拡張子の無いスクリプトを含む）を、パスの昇順で返す。"""
    found = []
    for path in sorted(plugin_root.rglob("*")):
        rel = path.relative_to(plugin_root)
        if not path.is_file() or SKIP_DIRS & set(rel.parts):
            continue
        if path.suffix in TEXT_SUFFIXES or rel.parts[0] in SCRIPT_DIRS:
            found.append(Source(path, path.relative_to(repo_root).as_posix()))
    return found


def unparsable(scope: Scope) -> list[Finding]:
    """構文の誤りで読めない `.py` を違反にし、見る範囲から外す（ほかの規則が同じ木を読むため）。"""
    found, ok = [], []
    for src in scope.sources:
        try:
            _ = src.tree if src.is_python else src.text
        except (SyntaxError, UnicodeDecodeError) as exc:
            found.append(Finding("syntax", src.rel, getattr(exc, "lineno", None) or 1, f"読めない: {exc}"))
            continue
        ok.append(src)
    scope.sources = ok
    return found


def smell_findings(scope: Scope, rules: dict[str, Any]) -> list[Finding]:
    """兆候を数え、境界の印を当てる。初日から 0 件で、基準のファイルを持たない。"""
    codes = frozenset(rules["smells"])
    found = []
    for src in scope.code():
        hits, caught = smells.scan(src.tree, rules["smells"])
        applied = marks.apply(hits, marks.read_marks(src.text, codes), caught, rules)
        found += [
            Finding(code, src.rel, line, rules["smells"][code]["summary"] + (f"。印を受けない: {why}" if why else ""))
            for line, code, why in applied.counted
        ]
        found += [Finding("mark-unused", src.rel, m.line, f"{code} の兆候が無い行の印") for m, code in applied.unused]
    return found


def check(scope: Scope, rules: dict[str, Any]) -> list[Finding]:
    """見る範囲にすべての規則を当て、指摘をパスと行の順で返す。"""
    found = unparsable(scope)
    found += smell_findings(scope, rules)
    found += structure.check(scope, rules) + structure.cycles(scope)
    found += facts.check(scope, rules) + words.check(scope, rules) + invariants.check(scope, rules)
    return sorted(found, key=lambda f: (f.path, f.line, f.rule, f.detail))


def _repo_root(plugin_root: Path) -> Path:
    try:
        done = subprocess.run(["git", "-C", str(plugin_root), "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CheckError(f"git の worktree で実行する: {plugin_root}") from exc
    return Path(done.stdout.strip()).resolve()


def _pr_body(event: str) -> str:
    """GitHub の事象の JSON から PR 本文を読む。事象が無い・PR でないときは空。"""
    if not event:
        return ""
    try:
        data = json.loads(Path(event).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CheckError(f"事象の JSON を読めない: {event}: {exc}") from exc
    pull = data["pull_request"] if "pull_request" in data else {}
    return pull["body"] if isinstance(pull.get("body"), str) else ""


def emit(status: str, summary: str, items: list[dict], metrics: dict) -> None:
    report = {"tool": "check", "status": status, "summary": summary, "items": items, "metrics": metrics}
    if status == "stopped" and not items:
        report["cause"] = {"kind": "config_broken"}
    print(json.dumps(report, ensure_ascii=False))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="check", description="新しいプラグインだけを見る検査")
    parser.add_argument("--base", help="差分の起点（I2）。無ければ差分を見ない")
    parser.add_argument("--event", default=os.environ.get("GITHUB_EVENT_PATH", ""), help="PR 本文を読む GitHub の事象の JSON")
    args = parser.parse_args(argv)
    try:
        ident = identity.load()
        rules = load_rules()
        repo_root = _repo_root(ident.root)
        scope = Scope(repo_root, ident.root.resolve(), ident.name, args.base, _pr_body(args.event))
        scope.sources = collect(scope.plugin_root, repo_root)
        found = check(scope, rules)
    except (CheckError, identity.IdentityError) as exc:
        emit("stopped", str(exc), [], {})
        return EXIT_UNREADABLE
    for f in found:
        print(f"{f.rule}: {f.path}:{f.line}: {f.detail}", file=sys.stderr)
    by_rule = dict(sorted(Counter(f.rule for f in found).items()))
    metrics = {"files": len(scope.sources), "findings": by_rule}
    if not found:
        emit("ok", f"{len(scope.sources)} ファイルに違反なし", [], metrics)
        return EXIT_OK
    emit("stopped", f"違反 {len(found)} 件", [f.as_item() for f in found], metrics)
    return EXIT_UNREADABLE if "syntax" in by_rule else EXIT_VIOLATION
