"""`ndf refactor` の副命令。

    ndf refactor touched --base <起点> --scope <置き場>... [--head HEAD] [--symbols <ファイル>:<名前>...] [--work <dir>]
    ndf refactor tests --changed <ファイル>... [--work <dir>]
    ndf refactor collected --paths <ファイル>... [--timeout <秒>] [--work <dir>]

- `touched`: 課題が触る関数（#622）と、着手前の範囲テストへ渡す範囲の配下のテストのファイル（#2096）。`--symbols` を
  渡せば差分から引かずにそれを使う
- `tests`: 項目が触るファイルに対応する範囲テストのファイル（#3049）。対応を引けないソースは `unmapped` に載る
- `collected`: 足したテストのファイルが全体の実行に収集されるか（#620）。収集されないファイルがあれば `test_failed` で止まる

結果 JSON を標準出力の最後の 1 行に出す。止まった理由は `ndf.contracts.causes` の種類で返す。
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ndf import cli, identity
from ndf.contracts import causes, limits, result
from ndf.domains.crossrefactor import collect, scope, symbols_decide
from ndf.testing import limits as test_limits

TOOL = cli.tool_of(__name__)
# 収集の確かめの上限（秒）の宣言
COLLECT_POLICY = "cross-refactoring.collect-check"


def _touched(a: argparse.Namespace) -> dict[str, Any]:
    work = Path(a.work)
    touched = symbols_decide.explicit(a.symbols) if a.symbols else scope.touched(work, a.base, a.head, a.scope)
    files = scope.scope_test_files(work, a.scope)
    items = [{"kind": "touched", **touched.as_json()}, {"kind": "scope_tests", "files": files}]
    n = sum(len(s) for s in touched.symbols.values())
    summary = f"触る関数 {n} 件・ファイル単位 {len(touched.whole_files)} 件・範囲テストのファイル {len(files)} 件"
    metrics = {"symbols": n, "whole_files": len(touched.whole_files), "scope_tests": len(files)}
    return result.result(TOOL, "ok", summary, items, metrics)


def _tests(a: argparse.Namespace) -> dict[str, Any]:
    picked = scope.tests_for_change(Path(a.work), a.changed)
    summary = f"範囲テストのファイル {len(picked.files)} 件・対応の無いソース {len(picked.unmapped)} 件"
    metrics = {"files": len(picked.files), "unmapped": len(picked.unmapped), "unreadable": len(picked.unreadable)}
    return result.result(TOOL, "ok", summary, [{"kind": "tests", **picked.as_json()}], metrics)


def _collected(a: argparse.Namespace) -> dict[str, Any]:
    work = Path(a.work)
    timeout = a.timeout if a.timeout is not None else limits.seconds(COLLECT_POLICY)
    got = collect.collected(work, a.paths, test_limits.load_suites(work), timeout)
    items = [{"kind": "collect", "collected": list(got.collected), "uncollected": list(got.uncollected), "undecided": list(got.undecided)}]
    metrics = {"collected": len(got.collected), "uncollected": len(got.uncollected), "undecided": len(got.undecided)}
    if got.uncollected:
        detail = f"全体の実行に収集されないテストのファイル: {', '.join(got.uncollected)}"
        return result.stopped(TOOL, "test_failed", detail, items=items, metrics=metrics)
    summary = f"収集される {len(got.collected)} 件・判定できない {len(got.undecided)} 件"
    return result.result(TOOL, "ok", summary, items, metrics)


HANDLERS: dict[str, Callable[[argparse.Namespace], dict[str, Any]]] = {"touched": _touched, "tests": _tests, "collected": _collected}


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=f"{identity.FIXED_WORDS['entrypoint']} {TOOL}", description="cross-refactoring の範囲とテストの選び方")
    sub = p.add_subparsers(dest="action", required=True)
    s = sub.add_parser("touched", help="課題が触る関数と、着手前の範囲テストのファイル")
    s.add_argument("--base", required=True)
    s.add_argument("--head", default="HEAD")
    s.add_argument("--scope", nargs="+", required=True)
    s.add_argument("--symbols", nargs="*", default=[])
    t = sub.add_parser("tests", help="項目が触るファイルに対応する範囲テストのファイル")
    t.add_argument("--changed", nargs="+", required=True)
    c = sub.add_parser("collected", help="足したテストが全体の実行に収集されるか")
    c.add_argument("--paths", nargs="+", required=True)
    c.add_argument("--timeout", type=float)
    for each in (s, t, c):
        each.add_argument("--work", default=".")
    return p


def main(argv: list[str]) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return causes.exit_code("invalid") if exc.code else 0
    try:
        return result.emit(HANDLERS[args.action](args))
    except scope.ScopeError as exc:
        return result.emit(result.stopped(TOOL, "invalid", str(exc)))
    except collect.CollectError as exc:
        return result.emit(result.stopped(TOOL, "precondition", str(exc)))
    except ValueError as exc:
        return result.emit(result.stopped(TOOL, "invalid", str(exc)))
