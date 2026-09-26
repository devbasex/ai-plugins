#!/usr/bin/env python3
"""エージェント向け指示書を適切に保つチェック（#554）。

指示書（既定では `AGENTS.md` / `CLAUDE.md` / `KIRO.md`）は、**根に置いたものをそのランタイムの
全セッションと全サブエージェントが毎回読む**。何を書いてもよい場所ではなく、毎回の読み込みの量を
そのまま増やす場所である。いま指示書を機械で見ているものが無いため、出た版の判断・参照先の
消えた即時読み込み・総量の増加は、起きても誰も止めない。

**何を見るかは外部の一次情報から導く。** 観点・強さ・出典は `data/instruction-criteria.json` が
持ち、ここには判定の手続きだけを置く（観点を 1 つ足すことと、手続きを変えることを別の変更にする）。

**判定の強さはリポジトリ側の宣言（`.ndf/instructions.json`）が決める。** 宣言が無くても
動くのは、リポジトリの性質によらず誤りである「参照先の無い即時読み込み」と、落とさずに
数える「読み込みの量」「指示の数」だけである。宣言を書くまで使えないチェックは、入れた利用者が
最初に外す。

**このチェックはどのファイルも書き換えず、課題も立てない。** 指摘へ扱いの目印（`直す` / `起票` /
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
import os
import sys
from pathlib import Path

# 隣の instructions_lib/ と lib/ を先に入れる（テストは spec_from_file_location で読み込むため、
# スクリプトのディレクトリが sys.path に入らない）
_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE / "lib")]
import refresh as refresh_lib  # noqa: E402

# 分けた名前をここから引けるように再エクスポートする（先頭が _ のものを含む）
from instructions_lib.model import (  # noqa: E402,F401
    SUPPORTED_CRITERIA_VERSIONS, KNOWN_CRITERIA, HEADING_RE, FENCE_RE, BULLET_RE, CheckError, Source,
    Finding, Target, ScopeRoot, Criteria, load_criteria,
)
from instructions_lib.declaration import (  # noqa: E402,F401
    DEFAULT_FILES, DEFAULT_IMPORT_SYNTAX, DEFAULT_IMPORT_DEPTH, DEFAULT_REVIEW_INTERVAL_DAYS,
    SUPPORTED_DECLARATION_VERSIONS, DECLARATION_RELATIVE_PATH, Declaration, _declaration_from,
    _populate_declaration, _apply_field_constraints, _validate_scopes, _validate_imports, _typed, _positive,
    _parse_declared_date, _validate_released,
)
from instructions_lib.collect import (  # noqa: E402,F401
    IMPORT_RE, _matches_file_pattern, _expand, collect_project, _build_scope_metadata, _enumerate_scope_files,
    _collect_scope_entry, collect_declared, display_path, mask_code, interprets_imports, references,
    _inside_root, resolve,
)
from instructions_lib.findings import (  # noqa: E402,F401
    import_findings, stale_allowance_findings, read_size, budget_findings, SENTENCE_END, count_instructions,
    count_findings,
)
from instructions_lib.versions import (  # noqa: E402,F401
    VERSION_RE, VERSION_AT_START, LEAD_RE, semver_key, base_triple, _read_changelog_lines, _read_tag_lines,
    released_versions, _valid_suffix, paragraph_starts, version_findings, _inline_pending_findings,
)
from instructions_lib.report import (  # noqa: E402,F401
    ACTION_FIX, ACTION_FILE, ACTION_REPORT, action_of, format_finding, issue_title, Measurements,
    ReportInput, report,
)

SCOPES = ("project", "user", "plugins")


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
    parser = _Parser(description="エージェント向け指示書を適切に保つチェック")
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


def resolve_latest(root: Path, decl: Declaration, criteria: Criteria) -> str | None:
    """`released` の宣言から最新の版を解く。**取れなければ止まる。**"""
    # **止まるなら、何も判定する前に止まる。** 版を取れないことは走査の前に分かる。
    if decl.released is None or not criteria.enabled("released-version-paragraph"):
        return None
    versions = released_versions(root, decl.released)
    if not versions:
        raise CheckError("released の宣言から版を 1 つも取れない")
    return max(versions, key=semver_key)


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


if __name__ == "__main__":
    sys.exit(main())
