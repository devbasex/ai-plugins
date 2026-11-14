"""殻: 足したテストが全体の実行に収集されるかを確かめる（#620）。

テストを足す工程は、足したテストが今のコードで通ることに加え、全体の実行（suite の `command`）が収集するファイルかを確かめる。
pytest の `python_files` に当たらない補助のモジュールへ足したテストは、範囲テストで通っても全体の実行に入らない。

suite の宣言（`.ndf/project.json` の `test.suites[]`）の `collect_command` が、全体の実行が収集するテストを 1 行ずつ出す
（pytest なら `pytest --collect-only -q`）。pytest は引数に渡したファイルを `python_files` に依らず収集するため、テンプレートに
`{paths}` を書くのは、渡したファイルを名前の規則で落とすランナーに限る。宣言の無い suite のファイルは判定できない
（`undecided`）。終了コードは 0 と 5（pytest の「収集が 0 件」）を受け、ほかは `CollectError` で止める。
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ndf.domains.crossrefactor import tests_decide
from ndf.domains.crossrefactor.diff_decide import in_scope

COLLECT_KEY = "collect_command"
# 収集の終了コードのうち、判定に使ってよいもの（0: 収集した、5: pytest の「収集が 0 件」）
COLLECT_OK = frozenset({0, 5})


class CollectError(Exception):
    """収集の確かめを起動できない・終了コードが違う・上限を超えた。"""

    def __init__(self, suite: str, command: str, detail: str) -> None:
        super().__init__(f"suite {suite} の収集の確かめが失敗（{command}）: {detail}")
        self.suite = suite


def _suite_of(path: str, suites: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    for suite in suites:
        paths = suite["paths"] if "paths" in suite else ["."]
        if isinstance(paths, list) and in_scope(path, tuple(str(p) for p in paths)):
            return suite
    return None


def _group(paths: Sequence[str], suites: Sequence[Mapping[str, Any]]) -> tuple[dict[str, list[str]], dict[str, Mapping[str, Any]]]:
    by_suite: dict[str, list[str]] = {}
    decl: dict[str, Mapping[str, Any]] = {}
    for path in paths:
        suite = _suite_of(path, suites)
        if suite is not None and isinstance(suite.get(COLLECT_KEY), str):
            name = str(suite["name"])
            by_suite.setdefault(name, []).append(path)
            decl[name] = suite
    return by_suite, decl


def _collect(work: Path, name: str, command: str, timeout: float) -> frozenset[str]:
    try:
        proc = subprocess.run(command, shell=True, cwd=work, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise CollectError(name, command, f"{timeout:g} 秒の上限を超えた") from exc
    except OSError as exc:
        raise CollectError(name, command, f"起動できない: {exc}") from exc
    if proc.returncode not in COLLECT_OK:
        raise CollectError(name, command, f"終了コード {proc.returncode}: {proc.stderr.strip()[-400:]}")
    return tests_decide.collected_files(proc.stdout)


def collected(work: Path, paths: Sequence[str], suites: Sequence[Mapping[str, Any]], timeout: float) -> tests_decide.CollectVerdict:
    """足したテストのファイル（作業場所からの相対パス）を、収集される・されない・判定できないに分ける。"""
    by_suite, decl = _group(paths, suites)
    got: dict[str, frozenset[str] | None] = {}
    for name, files in by_suite.items():
        template = str(decl[name][COLLECT_KEY])
        command = tests_decide.fill_paths(template, files) if tests_decide.PATHS_SLOT in template else template
        found = _collect(work, name, command, timeout)
        got.update({f: found for f in files})
    return tests_decide.collect_verdict(paths, got)
