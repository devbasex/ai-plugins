"""検査 1 本のエントリポイント（サブコマンド `check`）。見るファイルを集め、構文木を 1 回ずつ作り、すべての規則を当てる。

    ndf check [--only <規則>]... [--write-spec] [--ratchet-base <ref>]

`--only` があれば、その規則の指摘だけを違反にする。`--only review-items` だけなら、その規則の入力だけを読み、ほかの規則を当てない。`--write-spec` があれば、規約の
生成物（コントラクトのページと引数のスキーマ。`spec_docs`）を書き、減った件数へラチェットの基準を下げてから検査する。
`--ratchet-base` があれば、ラチェットの基準が、その commit の基準より上がっていないかも見る（CI はマージ先の先頭を渡す）。
結果 JSON（結果のコントラクト `ndf.contracts.result`）を標準出力の最後の 1 行に出す。違反は停止理由 check_failed、構文の誤りは\ninvalid、読めない（規則のデータ・git）は config_broken で、終了コードは種類の終了コード（1・2・2）である。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ndf import cli, identity
from ndf.checks import (
    child_deps,
    copy_dup,
    facts,
    internal_names,
    invariants,
    limits_readers,
    lint,
    ratchet,
    smells,
    spec_docs,
    structure,
    suppressions,
    words,
)
from ndf.checks.model import SCRIPT_PREFIXES, CheckError, Finding, Scope, Source, contained, load_rules
from ndf.contracts import result

# 結果 JSON の tool の名前はサブコマンドの名前（SSOT は `cli.COMMANDS`）
TOOL = cli.tool_of(__name__)

SKIP_DIRS = frozenset({"__pycache__", ".venv", ".pytest_cache", ".git", ".ruff_cache"})
# ラチェットを当てる規則（件数が基準と同じなら違反にしない）。ほかの規則は 0 件を保つ
RATCHETED = (copy_dup.RULE,)
TEXT_SUFFIXES = frozenset({".py", ".md", ".json", ".sh", ".toml", ".yml", ".yaml", ".txt"})


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
        if path.is_file() and (path.suffix in TEXT_SUFFIXES or rel.as_posix().startswith(SCRIPT_PREFIXES)):
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


def check(scope: Scope, rules: dict[str, Any], write_spec: bool = False, written: list[str] | None = None) -> list[Finding]:
    """見る範囲にすべての規則を当て、指摘をパスと行の順で返す。`write_spec` なら規約の生成物を書き、パスを `written` へ足す。

    ラチェットを当てる規則の指摘も、基準と比べずにすべて返す（`ratcheted` が比べる）。
    """
    found = unparsable(scope)
    found += smell_findings(scope, rules)
    found += structure.check(scope, rules) + structure.cycles(scope)
    found += facts.check(scope, rules) + words.check(scope, rules) + invariants.check(scope, rules)
    found += child_deps.apply(scope, rules) + limits_readers.check(scope, rules) + copy_dup.check(scope, rules)
    found += internal_names.check(scope, rules)
    found += spec_docs.check(scope, rules, write_spec, written)
    return sorted(found, key=_order)


def _git_objects(out: bytes) -> list[Optional[bytes]]:
    """`git cat-file --batch` の出力の、求めたものごとの中身（無いものは None）。"""
    found: list[Optional[bytes]] = []
    while out:
        head, _, out = out.partition(b"\n")
        size = None if head.endswith((b" missing", b" ambiguous")) else int(head.rpartition(b" ")[2])
        found.append(None if size is None else out[:size])
        out = out if size is None else out[size + 1 :]
    return found


def _ratchet_base(scope: Scope, path: Path, ref: str) -> dict[str, ratchet.Baseline]:
    """commit `ref` のラチェットの基準。`ref` が基準のファイルを持たなければ空、`ref` を読めなければ `CheckError`。"""
    where = f"{ref}:{scope.rel(path)}"
    objects = _git_objects(_git_stdout(scope.repo_root, "cat-file", "--batch", stdin=f"{ref}^{{commit}}\n{where}\n"))
    if len(objects) != 2 or objects[0] is None:
        raise CheckError(f"ラチェットの変更の前の commit を読めない: {ref}")
    return ratchet.limits_of(objects[1], where) if objects[1] is not None else {}


def ratcheted(scope: Scope, found: list[Finding], base_ref: Optional[str], written: list[str] | None) -> list[Finding]:
    """ラチェットを当てる規則の指摘を、基準と比べた結果に置き換える。`written` があれば、減った件数へ基準を下げ、パスを足す。

    件数が基準と同じ規則の指摘は外す。増えた規則は、増えた箇所を探せるように指摘をすべて残す。構文の誤りで読めないファイルが
    あれば、件数が足りないので比べない。
    """
    kept = [f for f in found if f.rule not in RATCHETED]
    if any(f.rule == "syntax" for f in kept):
        return kept
    path = contained(scope.plugin_root, ratchet.RATCHET_REL)
    counts = {rule: sum(f.rule == rule for f in found) for rule in RATCHETED}
    if written is not None:
        commit = _git_stdout(scope.repo_root, "rev-parse", "HEAD").decode().strip()
        if ratchet.lower(path, counts, commit, datetime.now(timezone.utc).date().isoformat()):
            written.append(ratchet.RATCHET_REL)
    limits = ratchet.baseline(path)
    why = ratchet.verdicts(counts, limits, _ratchet_base(scope, path, base_ref) if base_ref is not None else {})
    kept += [f for f in found if f.rule in why and counts[f.rule] > limits[f.rule].max]
    return sorted(kept + [Finding(rule, scope.rel(path), 1, text) for rule, text in why.items()], key=_order)


def select(
    scope: Scope, rules: dict[str, Any], only: list[str] | None, written: list[str] | None = None, base_ref: Optional[str] = None
) -> list[Finding]:
    """`only` の規則の指摘だけを返す（無ければすべての規則）。`written` があれば生成物を書き、パスを足す。

    `review-items` だけなら、その規則だけを当てる（項目の表だけを変えた PR で、全体の検査を打たない）。
    """
    if only and set(only) == {spec_docs.REVIEW_ITEMS} and written is None:
        return spec_docs.check_review_items(scope, rules)
    scope.sources = collect(scope.plugin_root, scope.repo_root)
    found = ratcheted(scope, check(scope, rules, written is not None, written), base_ref, written)
    return [f for f in found if not only or f.rule in only]


def _git_stdout(root: Path, *args: str, stdin: str = "") -> bytes:
    """git の標準出力。起動できない・失敗したときは `CheckError`。"""
    try:
        return subprocess.run(["git", "-C", str(root), *args], input=stdin.encode(), capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CheckError(f"git の worktree で実行する: {root}: git {' '.join(args)}") from exc


def _repo_root(plugin_root: Path) -> Path:
    return Path(os.fsdecode(_git_stdout(plugin_root, "rev-parse", "--show-toplevel")).strip()).resolve()


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
        "--write-spec",
        action="store_true",
        help="規約の生成物（コントラクトのページと引数のスキーマ）を書き、減った件数へラチェットの基準を下げてから検査する",
    )
    parser.add_argument("--ratchet-base", metavar="REF", help="ラチェットの基準が、この commit の基準より上がっていないかも見る")
    args = parser.parse_args(argv)
    written: list[str] | None = [] if args.write_spec else None
    try:
        ident = identity.load()
        rules = load_rules()
        repo_root = _repo_root(ident.root)
        scope = Scope(repo_root, ident.root.resolve(), ident.name)
        found = select(scope, rules, args.only, written, args.ratchet_base)
    except (CheckError, identity.IdentityError) as exc:
        return emit(str(exc), [], {}, "config_broken")
    except ratchet.RatchetError as exc:
        return emit(str(exc), [], {}, "unknown")
    for f in found:
        print(f"{f.rule}: {f.path}:{f.line}: {f.detail}", file=sys.stderr)
    by_rule = dict(sorted(Counter(f.rule for f in found).items()))
    metrics: dict[str, Any] = {"files": len(scope.sources), "findings": by_rule}
    if written is not None:
        metrics["written"] = len(written)
    if not found:
        return emit(f"{len(scope.sources)} ファイルに違反なし", [], metrics)
    kind = "invalid" if "syntax" in by_rule else "check_failed"
    return emit(f"違反 {len(found)} 件", [f.as_item() for f in found], metrics, kind)
