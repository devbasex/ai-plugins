"""`ndf route test` の本体: 手元のテストのコマンドをスロット（`ndf test-slot run`）で打ち、落ちを `test_failed` の結果にする。

サブコマンドの使い方は `ndf.core.route` の説明にある。CI の落ちたテストだけを打つ（`--failed-from`）ときは、打った場所の
設定（`.ndf/project.json`）の `test.suites` から範囲のテストのコマンドを組む（`ndf.testing.scoped`）。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Optional, Union

from ndf import cli, contracts, identity
from ndf.contracts import causes, exit_codes, result
from ndf.contracts.exit_codes import OK
from ndf.core.plan import steps
from ndf.testing import declared
from ndf.testing import scoped

# 結果 JSON の tool はサブコマンド `route` の名前（このパッケージ）
TOOL = cli.tool_of(__package__)
# 手元のテストを打つスロットのエントリポイント（`ndf route test` が後ろにコマンドを 1 つ足す）
TEST_SLOT = (identity.FIXED_WORDS["entrypoint"], "test-slot", "run", "--")
# これ以上の終了コードはシグナルで止まった子（シェルの 128 + シグナルの番号）。テストの落ちと読まない
SIGNAL_EXIT_BASE = 128


class LocalTestError(contracts.ContractError):
    """CI の結果や設定を読めない（`invalid`）。"""


def _invalid(detail: str) -> int:
    return result.emit(result.stopped(TOOL, "invalid", detail))


def read_result(path: Union[str, Path], label: str, error: type[Exception] = LocalTestError) -> Optional[dict[str, Any]]:
    """結果ファイルの最後の結果 JSON（無ければ None）。読めなければ `error`（『{label} {path} を読めない: ...』）。"""
    try:
        return steps.last_json(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise error(f"{label} {path} を読めない: {exc.strerror or exc}") from None


def _metrics_of(found: Mapping[str, Any]) -> Mapping[str, Any]:
    """結果 JSON の `metrics`。無いか形が違えば空。"""
    metrics = found.get("metrics")
    return metrics if isinstance(metrics, Mapping) else {}


def failed_tests_in(found: Mapping[str, Any]) -> list[str]:
    """結果 JSON の落ちたテスト（`metrics.failed_tests`）。無いか形が違えば空。"""
    metrics = _metrics_of(found)
    tests = metrics.get("failed_tests") if isinstance(metrics.get("failed_tests"), list) else []
    return [str(t) for t in tests]


def _read_ci_result(path: Path) -> tuple[list[str], dict[str, Any]]:
    """CI の結果 JSON の落ちたテストの node id（`metrics.failed_tests`）と、結果 JSON そのもの。

    読めない・結果 JSON でなければ `LocalTestError`。
    """
    found = read_result(path, "CI の結果")
    if found is None:
        raise LocalTestError(f"CI の結果 {path} は結果 JSON でない")
    return failed_tests_in(found), found


def _listed(reads: Sequence[Any], render: Callable[[Mapping[str, Any]], str], sep: str) -> str:
    """読み取りの記録の Mapping の要素だけを `render` して `sep` で繋ぐ。無ければ『読んだチェックが無い』。"""
    parts = [render(r) for r in reads if isinstance(r, Mapping)]
    return sep.join(parts) if parts else "読んだチェックが無い"


def _runner_label(read: Mapping[str, Any]) -> str:
    runner = read.get("runner")
    label = runner if isinstance(runner, str) and runner else "当たる suite が無い"
    return f"{label}（チェック {read.get('check')}）"


def _unread_runners(reads: Sequence[Any]) -> str:
    """読み取りの記録から、ログを読めなかった suite の `runner` とチェックの名前を並べる。"""
    return _listed(reads, _runner_label, "、")


def _check_reasons(reads: Sequence[Any]) -> str:
    """読み取りの記録から、チェックごとの読めなかった理由を並べる。"""
    return _listed(reads, lambda r: f"{r.get('check')}: {r.get('reason')}", "; ")


def unread_detail(found: Mapping[str, Any], path: Path) -> str:
    """落ちたテストが空の CI の結果から、停止理由の文を組む（読み取りの記録は `ndf ci watch` が書く）。

    停止理由が `test_failed` でなければ今の文、`test_failed` なら設定のキーか読めなかった理由を出す。
    `test_failed` で読み取りの記録（`metrics.failed_test_reads`）が無ければ `ndf ci watch` の結果でないとして `LocalTestError`。
    """
    if causes.kind_of(found.get("cause")) != "test_failed":
        return f"CI の結果 {path} に落ちたテストが無い（全体テストは打たずに止まる）"
    metrics = _metrics_of(found)
    reads = metrics.get("failed_test_reads")
    if not isinstance(reads, list):
        raise LocalTestError(f"CI の結果 {path} に読み取りの記録（metrics.failed_test_reads）が無い（ndf ci watch の結果でない）")
    glob = metrics.get("junit_artifacts")
    if not isinstance(glob, str) or not glob:
        return (
            "落ちたテストを読めない。JUnit の成果物を上げ、設定ファイルの test.ci.junit_artifacts に名前の glob を書く"
            f"（ログを読めなかった suite の runner: {_unread_runners(reads)}）"
        )
    return f"JUnit の成果物（{glob}）とログのどちらからも落ちたテストを読めない: {_check_reasons(reads)}"


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
    """スロット（test-slot）自身が止まった結果か、シグナルで止まった子か（テストの落ちと読まず、終了コードをそのまま返す）。"""
    return own is not None or code >= SIGNAL_EXIT_BASE


def _write_result(path: Optional[str], found: Mapping[str, Any]) -> None:
    if path is None:
        return
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(found, ensure_ascii=False) + "\n", encoding="utf-8")


def _declared_suites(cwd: Path) -> Any:
    """打った場所から根へ探した設定の `test.suites`（設定かキーが無ければ空か None。形の確かめは `scoped.scoped_runs` が持つ）。読めなければ `LocalTestError`。"""
    try:
        read = declared.read_ndf_settings(cwd)
    except declared.NdfSettingsError as exc:
        raise LocalTestError(f"設定 {exc.path} を読めない: {exc.cause}") from None
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
    nodes, found = _read_ci_result(ci_result)
    files = scoped.files_of(nodes)
    if not files:
        raise _NotRunnable(unread_detail(found, ci_result), nodes)
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
            return _invalid("--failed-from はコマンドを取らない（打つコマンドは設定の scope_command から組む）")
        return _cmd_failed_from(a)
    if a.command is None:
        return _invalid("打つテストのコマンドが無い（-- <コマンド> か --failed-from を渡す）")
    code, own = _run_passing([*TEST_SLOT, a.command])
    # スロット（test-slot）自身が止まった結果は出したまま返す。シグナルで止まった子はテストの落ちと読まない
    if code == exit_codes.code_of("ok") or _passed_through(code, own):
        return code
    metrics = {"exit_code": code, "failed_tests": []}
    found = result.stopped(TOOL, "test_failed", f"テストのコマンドが終了コード {code} で落ちた", metrics=metrics)
    _write_result(a.result, found)
    return result.emit(found)
