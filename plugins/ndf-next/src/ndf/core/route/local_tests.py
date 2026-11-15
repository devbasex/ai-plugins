"""`ndf route test` の本体: 手元のテストのコマンドを枠（`ndf test-slot run`）で打ち、落ちを `test_failed` の結果にする。

副命令の使い方は `ndf.core.route` の説明にある。CI の落ちたテストだけを打つ（`--failed-from`）ときは、打った場所の
宣言（`.ndf/project.json`）の `test.suites` から範囲のテストのコマンドを組む（`ndf.testing.scoped`）。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Optional, Union

from ndf import cli, contracts, identity
from ndf.contracts import exit_codes, result
from ndf.contracts.exit_codes import OK
from ndf.core.plan import steps
from ndf.testing import declared
from ndf.testing import scoped

# 結果 JSON の tool は副命令 `route` の名前（このパッケージ）
TOOL = cli.tool_of(__package__)
# 手元のテストを打つ枠のエントリポイント（`ndf route test` が後ろにコマンドを 1 つ足す）
TEST_SLOT = (identity.FIXED_WORDS["entrypoint"], "test-slot", "run", "--")
# これ以上の終了コードはシグナルで止まった子（シェルの 128 + シグナルの番号）。テストの落ちと読まない
SIGNAL_EXIT_BASE = 128


class LocalTestError(contracts.ContractError):
    """CI の結果や宣言を読めない（`invalid`）。"""


def _invalid(detail: str) -> int:
    return result.emit(result.stopped(TOOL, "invalid", detail))


def read_result(path: Union[str, Path], label: str, error: type[Exception] = LocalTestError) -> Optional[dict[str, Any]]:
    """結果ファイルの最後の結果 JSON（無ければ None）。読めなければ `error`（『{label} {path} を読めない: ...』）。"""
    try:
        return steps.last_json(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise error(f"{label} {path} を読めない: {exc.strerror or exc}") from None


def failed_tests_of(found: Mapping[str, Any]) -> list[str]:
    """結果 JSON の落ちたテスト（`metrics.failed_tests`）。無いか形が違えば空。"""
    metrics = found.get("metrics") if isinstance(found.get("metrics"), Mapping) else {}
    tests = metrics.get("failed_tests") if isinstance(metrics.get("failed_tests"), list) else []
    return [str(t) for t in tests]


def _failed_nodes(path: Path) -> list[str]:
    """CI の結果 JSON の落ちたテストの node id（`metrics.failed_tests`）。読めない・結果 JSON でなければ `LocalTestError`。"""
    found = read_result(path, "CI の結果")
    if found is None:
        raise LocalTestError(f"CI の結果 {path} は結果 JSON でない")
    return failed_tests_of(found)


def _run_passing(argv: Sequence[str]) -> tuple[int, Optional[dict[str, Any]]]:
    """`argv` を打ち、標準出力をそのまま流す。終了コードと、最後の行が結果 JSON ならそれを返す。"""
    with subprocess.Popen(list(argv), stdout=subprocess.PIPE, text=True) as proc:
        last = ""
        for line in proc.stdout if proc.stdout is not None else ():
            sys.stdout.write(line)
            sys.stdout.flush()
            if line.strip():
                last = line
    return proc.returncode, steps.last_json(last)


def _passed_through(code: int, own: Optional[dict[str, Any]]) -> bool:
    """枠（test-slot）自身が止まった結果か、シグナルで止まった子か（テストの落ちと読まず、終了コードをそのまま返す）。"""
    return own is not None or code >= SIGNAL_EXIT_BASE


def _write_result(path: Optional[str], found: Mapping[str, Any]) -> None:
    if path is None:
        return
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(found, ensure_ascii=False) + "\n", encoding="utf-8")


def _declared_suites(cwd: Path) -> Any:
    """打った場所から根へ探した宣言の `test.suites`（宣言か鍵が無ければ空か None。形の確かめは `scoped.scoped_runs` が持つ）。読めなければ `LocalTestError`。"""
    try:
        read = declared.read_declaration(cwd)
    except declared.DeclarationError as exc:
        raise LocalTestError(f"宣言 {exc.path} を読めない: {exc.cause}") from None
    if read is None:
        return []
    found = read[1]
    test = found.get("test") if isinstance(found, Mapping) else None
    return test.get("suites") if isinstance(test, Mapping) else None


class _NotRunnable(Exception):
    """落ちたテストを手元で範囲を絞って打てない（`precondition`）。"""

    def __init__(self, detail: str, nodes: Sequence[str]) -> None:
        super().__init__(detail)
        self.nodes = list(nodes)


def _scoped_plan(ci_result: Path, cwd: Path) -> tuple[list[str], list[scoped.ScopedRun]]:
    """CI の落ちたテストと、それを打つ範囲のテスト。打てなければ `_NotRunnable`、読めなければ `LocalTestError`。"""
    nodes = _failed_nodes(ci_result)
    files = scoped.files_of(nodes)
    if not files:
        raise _NotRunnable(f"CI の結果 {ci_result} に落ちたテストが無い（全体テストは打たずに止まる）", nodes)
    runs, rest = scoped.scoped_runs(_declared_suites(cwd), files)
    if rest:
        detail = f"範囲を打てるテストの suite（{scoped.SCOPE_KEY} に {scoped.PATHS_SLOT}）に当たらない: {', '.join(rest)}"
        raise _NotRunnable(detail, nodes)
    return nodes, runs


def _run_scoped(runs: Sequence[scoped.ScopedRun]) -> tuple[Optional[int], list[dict[str, Any]]]:
    """範囲のテストを順に打った合否の並び。枠自身かシグナルで止まれば、そこで止めた終了コードを先頭に返す。"""
    items: list[dict[str, Any]] = []
    for run in runs:
        code, own = _run_passing([*TEST_SLOT, run.command])
        if _passed_through(code, own):
            return code, items
        passed = code == exit_codes.code_of("ok")
        items.append({"kind": "test_run", "suite": run.suite, "command": run.command, "exit_code": code, "passed": passed})
    return None, items


def _failed_from_result(nodes: list[str], items: list[dict[str, Any]]) -> dict[str, Any]:
    """CI で落ちたテストを手元で打った合否の結果 JSON（1 本でも落ちれば `test_failed`）。"""
    failed = [i for i in items if not i["passed"]]
    metrics = {"failed_tests": nodes, "runs": len(items), "failed_runs": len(failed)}
    if failed:
        detail = f"CI で落ちたテストを手元で打ち、{len(items)} 本のうち {len(failed)} 本が落ちた: {failed[0]['command']}"
        return result.stopped(TOOL, "test_failed", detail, items=items, metrics=metrics)
    return result.result(TOOL, OK, f"CI で落ちたテストを手元で {len(items)} 本のコマンドで打ち、通った", items, metrics)


def _cmd_failed_from(a: argparse.Namespace) -> int:
    try:
        nodes, runs = _scoped_plan(Path(a.failed_from), Path.cwd())
    except LocalTestError as exc:
        return _invalid(str(exc))
    except _NotRunnable as exc:
        found = result.stopped(TOOL, "precondition", str(exc), metrics={"failed_tests": exc.nodes})
        _write_result(a.result, found)
        return result.emit(found)
    code, items = _run_scoped(runs)
    if code is not None:
        return code
    found = _failed_from_result(nodes, items)
    _write_result(a.result, found)
    return result.emit(found)


def cmd_test(a: argparse.Namespace) -> int:
    if a.failed_from is not None:
        if a.command is not None:
            return _invalid("--failed-from はコマンドを取らない（打つコマンドは宣言の scope_command から組む）")
        return _cmd_failed_from(a)
    if a.command is None:
        return _invalid("打つテストのコマンドが無い（-- <コマンド> か --failed-from を渡す）")
    code, own = _run_passing([*TEST_SLOT, a.command])
    # 枠（test-slot）自身が止まった結果は出したまま返す。シグナルで止まった子はテストの落ちと読まない
    if code == exit_codes.code_of("ok") or _passed_through(code, own):
        return code
    metrics = {"exit_code": code, "failed_tests": []}
    found = result.stopped(TOOL, "test_failed", f"テストのコマンドが終了コード {code} で落ちた", metrics=metrics)
    _write_result(a.result, found)
    return result.emit(found)
