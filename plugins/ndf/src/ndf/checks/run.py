"""検査 1 本のエントリポイント（サブコマンド `check`）。見るファイルを集め、構文木を 1 回ずつ作り、すべての規則を当てる。

    ndf check [--only <規則>]... [--write-spec] [--ratchet-base <ref>]

`--only` があれば、その規則の指摘だけを違反にする。`--write-spec` があれば、規約の
生成物（コントラクトのページと引数のスキーマ。`spec_docs`）を書き、減った件数へラチェットの基準を下げてから検査する。
`--ratchet-base` があれば、ラチェットの基準が、その commit の基準より上がっていないかも見る（CI はマージ先の先頭を渡す）。件数が
基準を超えた規則は件数だけを出す（指摘の全件は `--only` に規則を渡したときに出す）。
結果 JSON（結果のコントラクト `ndf.contracts.result`）を標準出力の最後の 1 行に出す。違反は停止理由 check_failed、構文の誤りは\ninvalid、読めない（規則のデータ・git）は config_broken（止まる前の指摘は items に残す）で、終了コードは種類の終了コード（1・2・2）である。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ndf import cli, contracts, identity
from ndf.checks import (
    budget,
    child_deps,
    contracts_split,
    copy_dup,
    experimental,
    facts,
    internal_names,
    invariants,
    limits_readers,
    lint,
    ratchet,
    suppressions,
    smells,
    spec_docs,
    structure,
    toolchain,
    words,
)
from ndf.checks.model import SCRIPT_PREFIXES, CheckError, Finding, Scope, Source, contained, load_rules, walk
from ndf.contracts import ContractError, causes, result

# 結果 JSON の tool の名前はサブコマンドの名前（SSOT は `cli.COMMANDS`）
TOOL = cli.tool_of(__name__)

# 規則の関数: 見る範囲と規則のデータを受け、指摘を返す
Rule = Callable[[Scope, dict[str, Any]], list[Finding]]

# 文字のファイルの拡張子。Skill が配る JavaScript（`skills/release/assets/slides/build-deck.js` など）も既定で動くコードとして見る。
# TypeScript はビルドしてから動くため、そのまま配る形に無い
TEXT_SUFFIXES = frozenset({".py", ".md", ".json", ".sh", ".toml", ".yml", ".yaml", ".txt", ".js", ".mjs", ".cjs"})
# リポジトリの設定（`Scope.settings`）を読む規則: 規則の名前 → 規則。`--only` に名前が無ければ当てない（設定を読まない）。
# 設定を読まない規則だけを選んだ検査は、編集中の設定のファイルが壊れていても止まらない
SETTINGS_RULES: dict[str, Rule] = {toolchain.RULE: toolchain.check}
# コントラクトのうち要るファイルだけを自分で読み、`--only` に名前が無ければ当てない規則: 規則の名前 → 規則。
# ほかの規則はコントラクトの表（停止理由の表など）が壊れていると止まるため、その前に当てる
CONTRACT_RULES: dict[str, Rule] = {contracts_split.RULE: contracts_split.check}
# 検査が集めたプラグインのファイルを読み、`--only` に名前が無ければ当てない規則: 規則の名前 → 規則
SELECTED_RULES: dict[str, Rule] = {
    experimental.RULE: experimental.check,
    limits_readers.RULE: limits_readers.check,
    internal_names.RULE: internal_names.check,
}
# 規則の一部だけを当てる名前。全体の規則が同じ違反を出すので、`--only` に名前があるときだけ当てる
PARTIAL_RULES: dict[str, Rule] = {budget.JOBS_RULE: budget.check_jobs}
# 全体の検査では `spec_docs.check` が同じ名前の違反を出し、単独では要る入力（項目の表・対応表・`children.json`）だけを読む規則
SPEC_RULES: dict[str, Rule] = {spec_docs.REVIEW_ITEMS: spec_docs.check_review_items}
# ラチェットを当てる規則（件数が基準と同じなら違反にしない）: 規則の名前 → 規則。ほかの規則は 0 件を保つ。`--only` に名前が
# 無ければ当てない（基準を読まず、下げない）
RATCHETED: dict[str, Rule] = {copy_dup.RULE: copy_dup.check}
# 件数が基準を超えた規則の理由に添える、箇所の見方
LISTING = "指摘の全件は --only {rule} で出る（変更の前のコードの出力と比べる）"


class Halted(CheckError):
    """途中の規則が止まった検査。`found` は止まる前の指摘（`CheckError` の文と合わせて結果に残す）。"""

    def __init__(self, reason: str, found: list[Finding]) -> None:
        super().__init__(reason)
        self.found = found


def collect(plugin_root: Path, repo_root: Path) -> list[Source]:
    """プラグインの下の文字のファイル（`bin/`・`hooks/` の拡張子の無いスクリプトを含む）を、パスの昇順で返す。

    取得した他人のリポジトリを検査する経路があるため、解決した先がリポジトリの外のもの（外を指すシンボリックリンク）は
    集めない。どの規則も本文を読む前に（構文の確かめ `unparsable` を含む）外のファイルを見る範囲から除く。
    """
    root = repo_root.resolve()
    found = []
    for path in sorted(walk(plugin_root)):
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


def _gather(parts: Iterable[list[Finding]], only: list[str] | None) -> list[Finding]:
    """規則ごとの指摘の並びから、`only` の規則の指摘（無ければすべて）をパスと行の順で返す。途中で止まれば `Halted`。"""
    found: list[Finding] = []
    try:
        for part in parts:
            found += [f for f in part if not only or f.rule in only]
    except CheckError as exc:
        raise Halted(str(exc), sorted(found, key=_order)) from exc
    return sorted(found, key=_order)


def _require_own_contracts(scope: Scope, rules: dict[str, Any]) -> None:
    """コントラクトのファイルがプラグインの外を指せば `CheckError`。

    事実の表・スキーマ・規約を読む規則はスキーマ違反の値を指摘に載せるので、どの規則が列挙・読み取りをするより前に 1 回だけ確かめる。
    """
    try:
        contracts.require_tree_inside(scope.plugin_root / rules["facts"]["contracts_dir"], scope.plugin_root)
    except ContractError as exc:
        raise CheckError(str(exc)) from None


def _chosen(table: dict[str, Rule], scope: Scope, rules: dict[str, Any], only: list[str] | None) -> Iterator[list[Finding]]:
    """`table` の規則のうち `only` に名前があるもの（`only` が無ければすべて）を 1 つずつ当てる。"""
    for name, rule in table.items():
        if not only or name in only:
            yield rule(scope, rules)


def _git_objects(out: bytes) -> list[Optional[bytes]]:
    """`git cat-file --batch` の出力の、求めたものごとの中身（無いものは None）。"""
    found: list[Optional[bytes]] = []
    while out:
        head, _, out = out.partition(b"\n")
        if head.endswith((b" missing", b" ambiguous")):
            found.append(None)
            continue
        size = int(head.rpartition(b" ")[2])
        found.append(out[:size])
        out = out[size + 1 :]
    return found


def _ratchet_base(scope: Scope, path: Path, ref: Optional[str]) -> dict[str, ratchet.Baseline]:
    """commit `ref` のラチェットの基準。`ref` が無いか基準のファイルを持たなければ空、`ref` を読めなければ `CheckError`。"""
    if ref is None:
        return {}
    where = f"{ref}:{scope.rel(path)}"
    objects = _git_objects(_git_stdout(scope.repo_root, "cat-file", "--batch", stdin=f"{ref}^{{commit}}\n{where}\n"))
    if len(objects) != 2 or objects[0] is None:
        raise CheckError(f"ラチェットの変更の前の commit を読めない: {ref}")
    return ratchet.baselines_of(objects[1], where) if objects[1] is not None else {}


def ratcheted(
    scope: Scope, found: dict[str, list[Finding]], listed: bool, written: list[str] | None, base_ref: Optional[str]
) -> list[Finding]:
    """ラチェットを当てる規則ごとの指摘 `found` を基準と比べた結果。`written` があれば、減った件数へ基準を下げ、パスを足す。

    件数が基準と同じ規則の指摘は外す。増えた規則は理由と件数だけにし、`listed` なら指摘もすべて残す。
    """
    path = contained(scope.plugin_root, ratchet.RATCHET_REL)
    counts = {rule: len(hits) for rule, hits in found.items()}
    limits = ratchet.baseline(path)
    if written is not None and ratchet.below(counts, limits):
        commit = _git_stdout(scope.repo_root, "rev-parse", "HEAD").decode().strip()
        limits = ratchet.lower(path, counts, limits, commit, datetime.now(timezone.utc).date().isoformat())
        written.append(ratchet.RATCHET_REL)
    why = ratchet.verdicts(counts, limits, _ratchet_base(scope, path, base_ref))
    over = {rule: f"{why[rule]}。{LISTING.format(rule=rule)}" for rule in why if counts[rule] > limits[rule].max}
    kept = [f for rule in over for f in found[rule]] if listed else []
    return kept + [Finding(rule, scope.rel(path), 1, text) for rule, text in {**why, **over}.items()]


def _parts(
    scope: Scope,
    rules: dict[str, Any],
    only: list[str] | None,
    written: list[str] | None,
    metrics: dict[str, Any] | None,
    base_ref: Optional[str],
) -> Iterator[list[Finding]]:
    """見る範囲に規則を 1 つずつ当て、規則ごとの指摘を返す。`written` があれば規約の生成物を書き、パスを足す。

    `CONTRACT_RULES`・`SELECTED_RULES`・`SETTINGS_RULES`・`RATCHETED` の規則と規則 budget は、`only` が無いか `only` に名前があるときだけ当てる。
    規則 budget を当てたときは、件数を違反の有無にかかわらず `metrics` の `budget` へ入れる。
    """
    broken = unparsable(scope)
    yield broken
    yield from _chosen(CONTRACT_RULES, scope, rules, only)
    yield smell_findings(scope, rules)
    _require_own_contracts(scope, rules)
    # 規則ごとに返す（後の規則が止まっても、先に当てた規則の指摘を残す）
    yield structure.check(scope, rules)
    yield structure.cycles(scope)
    yield facts.check(scope, rules)
    yield words.check(scope, rules)
    yield invariants.check(scope, rules)
    yield child_deps.apply(scope, rules)
    yield spec_docs.check(scope, rules, written is not None, written)
    # 構文の誤りで読めないファイルがあれば、件数が足りないので基準と比べない
    counted = {} if broken else {name: rule(scope, rules) for name, rule in RATCHETED.items() if not only or name in only}
    if counted:
        yield ratcheted(scope, counted, bool(only), written, base_ref)
    yield from _chosen({**SELECTED_RULES, **SETTINGS_RULES}, scope, rules, only)
    if not only or budget.RULE in only:
        counts = budget.measure(scope)
        yield budget.check(scope, rules, counts)
        if metrics is not None:
            metrics[budget.RULE] = counts.as_metrics()
    if only:
        yield from _chosen(PARTIAL_RULES, scope, rules, only)


def check(
    scope: Scope,
    rules: dict[str, Any],
    only: list[str] | None = None,
    written: list[str] | None = None,
    metrics: dict[str, Any] | None = None,
    base_ref: Optional[str] = None,
) -> list[Finding]:
    """見る範囲に規則（`_parts`）を当て、`only` の規則の指摘（無ければすべて）を返す。途中の規則が止まれば `Halted`。"""
    return _gather(_parts(scope, rules, only, written, metrics, base_ref), only)


def select(
    scope: Scope,
    rules: dict[str, Any],
    only: list[str] | None,
    written: list[str] | None = None,
    metrics: dict[str, Any] | None = None,
    base_ref: Optional[str] = None,
) -> list[Finding]:
    """`only` の規則の指摘だけを返す（無ければすべての規則）。計測値は `metrics` へ入れる（`check` と同じ）。途中の規則が止まれば `Halted`。

    `only` がほかの規則なしで当てられる規則（`CONTRACT_RULES`・`SETTINGS_RULES`・`PARTIAL_RULES`・`SPEC_RULES`・`SELECTED_RULES`）だけ
    なら、ほかの規則を当てない。そのうち集めたファイルを読まない規則（`SELECTED_RULES` のほか）だけなら、ファイルも集めない
    （ワークフローか項目の表だけを変えた Pull Request の `--only` を、その規則の分の読み取りで終える）。
    """
    alone = {**CONTRACT_RULES, **SETTINGS_RULES, **PARTIAL_RULES, **SPEC_RULES, **SELECTED_RULES}
    if only and written is None:
        named = set(only)
        if named <= alone.keys():
            if named & SELECTED_RULES.keys():
                scope.sources = collect(scope.plugin_root, scope.repo_root)
                unparsable(scope)
            return _gather(_chosen(alone, scope, rules, only), only)
    scope.sources = collect(scope.plugin_root, scope.repo_root)
    return check(scope, rules, only, written, metrics, base_ref)


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

    `kind` が無ければ合格（ok）、あれば停止理由の種類を `cause` に持つ stopped にする。停止理由の表が読めないか `kind` を持たなければ、
    表で確かめずに指摘（`items`）を持つ config_broken を出す（表が壊れたことの指摘を失わない）。
    """
    if kind is None:
        return result.emit(result.result(TOOL, result.OK, summary, items=items, metrics=metrics))
    try:
        causes.row(kind)  # 表に種類が無いことも、表の誤り（`ContractError`）として下で受ける
        return result.emit(result.stopped(TOOL, kind, summary, items=items, metrics=metrics))
    except ContractError as exc:
        # 表の誤りは出力の前に投げられるので、標準出力へ 2 行出ることは無い
        return result.emit_unchecked(TOOL, "config_broken", f"{summary}（コントラクトのファイルが読めない: {exc}）", items)


def _print_findings(found: list[Finding]) -> None:
    for f in found:
        print(f"{f.rule}: {f.path}:{f.line}: {f.detail}", file=sys.stderr)


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
    measured: dict[str, Any] = {}
    try:
        ident = identity.load()
        rules = load_rules()
        repo_root = _repo_root(ident.root)
        scope = Scope(repo_root, ident.root.resolve(), ident.name)
        found = select(scope, rules, args.only, written, measured, args.ratchet_base)
    except Halted as exc:
        _print_findings(exc.found)
        return emit(str(exc), [f.as_item() for f in exc.found], {}, "config_broken")
    except (CheckError, identity.IdentityError) as exc:
        return emit(str(exc), [], {}, "config_broken")
    except ratchet.RatchetError as exc:
        return emit(str(exc), [], {}, "unknown")
    _print_findings(found)
    by_rule = dict(sorted(Counter(f.rule for f in found).items()))
    metrics: dict[str, Any] = {"files": len(scope.sources), "findings": by_rule, **measured}
    if written is not None:
        metrics["written"] = len(written)
    if not found:
        return emit(f"{len(scope.sources)} ファイルに違反なし", [], metrics)
    kind = "invalid" if "syntax" in by_rule else "check_failed"
    return emit(f"違反 {len(found)} 件", [f.as_item() for f in found], metrics, kind)
