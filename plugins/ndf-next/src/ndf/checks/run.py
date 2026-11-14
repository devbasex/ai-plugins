"""検査 1 本のエントリポイント（副命令 `check`）。見るファイルを集め、構文木を 1 回ずつ作り、すべての規則を当てる。

    ndf check [--base <起点>] [--event <GitHub の事象の JSON>]

`--base` があれば差分の検査（I2）も行う。PR 本文は `--event`（既定は環境変数 `GITHUB_EVENT_PATH`）から読む。
結果 JSON（結果の契約 `ndf.contracts.result`）を標準出力の最後の 1 行に出す。違反は止まった理由 check_failed、構文の誤りは\ninvalid、読めない（規則のデータ・git・事象の JSON）は config_broken で、終了コードは種類の終了コード（1・2・2）である。
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

from ndf import cli, identity
from ndf.checks import child_deps, facts, invariants, lint, marks, smells, structure, words
from ndf.checks.model import CheckError, Finding, Scope, Source, load_rules
from ndf.contracts import result

# 結果 JSON の tool の名前は副命令の名前（正本は `cli.COMMANDS`）
TOOL = cli.tool_of(__name__)

SKIP_DIRS = frozenset({"__pycache__", ".venv", ".pytest_cache", ".git", ".ruff_cache"})
TEXT_SUFFIXES = frozenset({".py", ".md", ".json", ".sh", ".toml", ".yml", ".yaml", ".txt"})
SCRIPT_DIRS = ("bin", "hooks")


def _walk(plugin_root: Path) -> list[Path]:
    """プラグインの下のファイル。`SKIP_DIRS` のディレクトリへは降りない（依存の環境などを列挙しない）。"""
    found = []
    for top, dirs, files in os.walk(plugin_root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        found += [Path(top, name) for name in files]
    return found


def collect(plugin_root: Path, repo_root: Path) -> list[Source]:
    """プラグインの下の文字のファイル（`bin/`・`hooks/` の拡張子の無いスクリプトを含む）を、パスの昇順で返す。"""
    found = []
    for path in sorted(_walk(plugin_root)):
        rel = path.relative_to(plugin_root)
        if path.is_file() and (path.suffix in TEXT_SUFFIXES or rel.parts[0] in SCRIPT_DIRS):
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
    """兆候を数え（ruff は全ファイルへ 1 回）、境界の印を当てる。初日から 0 件で、基準のファイルを持たない。"""
    codes = frozenset(rules["smells"])
    sources = scope.code()
    by_ruff = lint.scan(sources, rules)
    found = []
    for src in sources:
        own, caught = smells.scan(src.tree, rules["smells"])
        hits = sorted({*own, *by_ruff[src.path.resolve()]})
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
    found += child_deps.apply(scope, rules)
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


def emit(summary: str, items: list[dict], metrics: dict, kind: str | None = None) -> int:
    """結果 JSON を結果の契約（`ndf.contracts.result`）で確かめて 1 行で出し、終了コードを返す。

    `kind` が無ければ合格（ok）、あれば止まった理由の種類を `cause` に持つ stopped にする。
    """
    if kind is None:
        return result.emit(result.result(TOOL, result.OK, summary, items=items, metrics=metrics))
    return result.emit(result.stopped(TOOL, kind, summary, items=items, metrics=metrics))


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
        return emit(str(exc), [], {}, "config_broken")
    for f in found:
        print(f"{f.rule}: {f.path}:{f.line}: {f.detail}", file=sys.stderr)
    by_rule = dict(sorted(Counter(f.rule for f in found).items()))
    metrics = {"files": len(scope.sources), "findings": by_rule}
    if not found:
        return emit(f"{len(scope.sources)} ファイルに違反なし", [], metrics)
    kind = "invalid" if "syntax" in by_rule else "check_failed"
    return emit(f"違反 {len(found)} 件", [f.as_item() for f in found], metrics, kind)
