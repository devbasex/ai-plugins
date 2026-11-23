"""検査 1 本のエントリポイント（サブコマンド `check`）。見るファイルを集め、構文木を 1 回ずつ作り、すべての規則を当てる。

    ndf check [--only <規則>]... [--write-spec]

`--only` があれば、その規則の指摘だけを違反にする。`--write-spec` があれば、規約の
生成物（コントラクトのページと引数のスキーマ。`spec_docs`）を書いてから検査する。
結果 JSON（結果のコントラクト `ndf.contracts.result`）を標準出力の最後の 1 行に出す。違反は停止理由 check_failed、構文の誤りは\ninvalid、読めない（規則のデータ・git）は config_broken で、終了コードは種類の終了コード（1・2・2）である。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ndf import cli, identity
from ndf.checks import (
    budget,
    child_deps,
    experimental,
    facts,
    invariants,
    lint,
    suppressions,
    smells,
    spec_docs,
    structure,
    toolchain,
    words,
)
from ndf.checks.model import SCRIPT_PREFIXES, SKIP_DIRS, CheckError, Finding, Scope, Source, load_rules
from ndf.contracts import result

# 結果 JSON の tool の名前はサブコマンドの名前（SSOT は `cli.COMMANDS`）
TOOL = cli.tool_of(__name__)

# 文字のファイルの拡張子。Skill が配る JavaScript（`skills/release/assets/slides/build-deck.js` など）も既定で動くコードとして見る。
# TypeScript はビルドしてから動くため、そのまま配る形に無い
TEXT_SUFFIXES = frozenset({".py", ".md", ".json", ".sh", ".toml", ".yml", ".yaml", ".txt", ".js", ".mjs", ".cjs"})
# リポジトリの設定（`Scope.settings`）を読む規則: 規則の名前 → 規則。`--only` に名前が無ければ当てない（設定を読まない）。
# 設定を読まない規則だけを選んだ検査は、編集中の設定のファイルが壊れていても止まらない
SETTINGS_RULES: dict[str, Callable[[Scope, dict[str, Any]], list[Finding]]] = {toolchain.RULE: toolchain.check}


def _walk(plugin_root: Path) -> list[Path]:
    """プラグインの下のファイル。`SKIP_DIRS` のディレクトリへは降りない（依存の環境などを列挙しない）。"""
    found = []
    for top, dirs, files in os.walk(plugin_root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        found += [Path(top, name) for name in files]
    return found


def collect(plugin_root: Path, repo_root: Path) -> list[Source]:
    """プラグインの下の文字のファイル（`bin/`・`hooks/` の拡張子の無いスクリプトを含む）を、パスの昇順で返す。

    取得した他人のリポジトリを検査する経路があるため、解決した先がリポジトリの外のもの（外を指すシンボリックリンク）は
    集めない。どの規則も本文を読む前に（構文の確かめ `unparsable` を含む）外のファイルを見る範囲から除く。
    """
    root = repo_root.resolve()
    found = []
    for path in sorted(_walk(plugin_root)):
        rel = path.relative_to(plugin_root)
        if not (path.suffix in TEXT_SUFFIXES or rel.as_posix().startswith(SCRIPT_PREFIXES)):
            continue
        # 名前と配置で候補に絞ってから、候補だけを解決してリポジトリの外を除く
        if path.resolve().is_relative_to(root) and path.is_file():
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
    """兆候を数え（ruff は全ファイルへ 1 回）、抑制コメントを当てる。初日から 0 件で、基準のファイルを持たない。"""
    codes = frozenset(rules["smells"])
    sources = scope.code()
    by_ruff = lint.scan(sources, rules)
    found = []
    for src in sources:
        own, caught = smells.scan(src.tree, rules["smells"])
        hits = sorted({*own, *by_ruff[src.path.resolve()]})
        applied = suppressions.apply(hits, suppressions.read_suppressions(src.text, codes), caught, rules)
        found += [
            Finding(code, src.rel, line, rules["smells"][code]["summary"] + (f"。抑制コメントを受けない: {why}" if why else ""))
            for line, code, why in applied.counted
        ]
        found += [Finding("suppression-unused", src.rel, m.line, f"{code} の兆候が無い行の抑制コメント") for m, code in applied.unused]
    return found


def _order(f: Finding) -> tuple[str, int, str, str]:
    """指摘の並びのキー（パス・行・規則・中身）。"""
    return (f.path, f.line, f.rule, f.detail)


def check(
    scope: Scope,
    rules: dict[str, Any],
    write_spec: bool = False,
    written: list[str] | None = None,
    only: list[str] | None = None,
    metrics: dict[str, Any] | None = None,
) -> list[Finding]:
    """見る範囲に規則を当て、指摘をパスと行の順で返す。`write_spec` なら規約の生成物を書き、パスを `written` へ足す。

    設定を読む規則（`SETTINGS_RULES` と規則 budget）と規則 experimental は、`only` が無いか `only` に名前があるときだけ当てる。
    規則 budget を当てたときは、件数を違反の有無にかかわらず `metrics` の `budget` へ入れる。
    """
    found = unparsable(scope)
    found += smell_findings(scope, rules)
    found += structure.check(scope, rules) + structure.cycles(scope)
    found += facts.check(scope, rules) + words.check(scope, rules) + invariants.check(scope, rules)
    found += child_deps.apply(scope, rules)
    found += spec_docs.check(scope, rules, write_spec, written)
    if not only or experimental.RULE in only:
        found += experimental.check(scope, rules)
    if not only or budget.RULE in only:
        counts = budget.measure(scope)
        found += budget.check(scope, rules, counts)
        if metrics is not None:
            metrics[budget.RULE] = counts.as_metrics()
    for name, rule in SETTINGS_RULES.items():
        if not only or name in only:
            found += rule(scope, rules)
    return sorted(found, key=_order)


def select(
    scope: Scope,
    rules: dict[str, Any],
    only: list[str] | None,
    write_spec: bool = False,
    written: list[str] | None = None,
    metrics: dict[str, Any] | None = None,
) -> list[Finding]:
    """`only` の規則の指摘だけを返す（無ければすべての規則）。計測値は `metrics` へ入れる（`check` と同じ）。

    `only` が設定を読む規則（`SETTINGS_RULES`）だけなら、プラグインのファイルを集めず、ほかの規則も当てない
    （ワークフローだけを変えた Pull Request の `--only toolchain` を、その規則の分の読み取りで終える）。
    """
    if only and set(only) <= SETTINGS_RULES.keys() and not write_spec:
        return sorted((f for name in SETTINGS_RULES if name in only for f in SETTINGS_RULES[name](scope, rules)), key=_order)
    scope.sources = collect(scope.plugin_root, scope.repo_root)
    found = check(scope, rules, write_spec, written, only, metrics)
    return [f for f in found if not only or f.rule in only]


def _repo_root(plugin_root: Path) -> Path:
    try:
        done = subprocess.run(["git", "-C", str(plugin_root), "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CheckError(f"git の worktree で実行する: {plugin_root}") from exc
    return Path(done.stdout.strip()).resolve()


def emit(summary: str, items: list[dict], metrics: dict, kind: str | None = None) -> int:
    """結果 JSON を結果のコントラクト（`ndf.contracts.result`）で確かめて 1 行で出し、終了コードを返す。

    `kind` が無ければ合格（ok）、あれば停止理由の種類を `cause` に持つ stopped にする。
    """
    if kind is None:
        return result.emit(result.result(TOOL, result.OK, summary, items=items, metrics=metrics))
    return result.emit(result.stopped(TOOL, kind, summary, items=items, metrics=metrics))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="check", description="プラグインだけを見る検査")
    parser.add_argument("--only", action="append", metavar="RULE", help="この規則の指摘だけを違反にする（繰り返せる）")
    parser.add_argument(
        "--write-spec", action="store_true", help="規約の生成物（コントラクトのページと引数のスキーマ）を書いてから検査する"
    )
    args = parser.parse_args(argv)
    written: list[str] | None = [] if args.write_spec else None
    measured: dict[str, Any] = {}
    try:
        ident = identity.load()
        rules = load_rules()
        repo_root = _repo_root(ident.root)
        scope = Scope(repo_root, ident.root.resolve(), ident.name)
        found = select(scope, rules, args.only, args.write_spec, written, measured)
    except (CheckError, identity.IdentityError) as exc:
        return emit(str(exc), [], {}, "config_broken")
    for f in found:
        print(f"{f.rule}: {f.path}:{f.line}: {f.detail}", file=sys.stderr)
    by_rule = dict(sorted(Counter(f.rule for f in found).items()))
    metrics: dict[str, Any] = {"files": len(scope.sources), "findings": by_rule, **measured}
    if written is not None:
        metrics["written"] = len(written)
    if not found:
        return emit(f"{len(scope.sources)} ファイルに違反なし", [], metrics)
    kind = "invalid" if "syntax" in by_rule else "check_failed"
    return emit(f"違反 {len(found)} 件", [f.as_item() for f in found], metrics, kind)
