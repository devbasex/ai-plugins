"""殻: 足したテストが全体の実行に収集されるかを確かめる（#620）。

テストを足す工程は、足したテストが今のコードで通ることに加え、全体の実行（suite の `command`）が収集するファイルかを確かめる。
pytest の `python_files` に当たらない補助のモジュールへ足したテストは、範囲テストで通っても全体の実行に入らない。

suite の宣言（`.ndf/project.json` の `test.suites[]`）の `collect_command` が、全体の実行が収集するテストを 1 行ずつ出す
（pytest なら `pytest --collect-only -q`）。pytest は引数に渡したファイルを `python_files` に依らず収集するため、テンプレートに
`{paths}` を書くのは、渡したファイルを名前の規則で落とすランナーに限る。宣言の無い suite のファイルは判定できない
（`undecided`）。成功と読む終了コードは suite の `collect_exit_codes`（無ければ 0 だけ）で、ほかは `CollectError` で止める。
pytest の「収集が 0 件」（終了コード 5）を成功と読むかは、ランナーの事実なので suite の宣言に書く。
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ndf.domains.crossrefactor import tests_decide
from ndf.testing.scoped import in_scope
from ndf.testing import scoped
from ndf.testing import slots

COLLECT_KEY = "collect_command"
# 収集の確かめの上限（秒）の宣言
COLLECT_POLICY = "cross-refactoring.collect-check"
# 収集の確かめで成功と読む終了コードの宣言（suite の鍵）と、宣言の無い suite の既定
EXIT_CODES_KEY = "collect_exit_codes"
DEFAULT_EXIT_CODES = frozenset({0})


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


@dataclass
class _Group:
    """収集を確かめる 1 つの suite の宣言（同じ名前なら最後に当たった宣言）と、当たった対象ファイル（渡した順）。"""

    decl: Mapping[str, Any]
    files: list[str] = field(default_factory=list)


def _group(paths: Sequence[str], suites: Sequence[Mapping[str, Any]]) -> dict[str, _Group]:
    groups: dict[str, _Group] = {}
    for path in paths:
        suite = _suite_of(path, suites)
        if suite is not None and isinstance(suite.get(COLLECT_KEY), str):
            group = groups.setdefault(str(suite["name"]), _Group(suite))
            group.files.append(path)
            group.decl = suite
    return groups


def exit_codes_of(suite: Mapping[str, Any]) -> frozenset[int]:
    """suite の `collect_exit_codes` を成功と読む終了コードの集合にする。無いか並びでなければ `{0}`。真偽値は数えない。"""
    declared = suite.get(EXIT_CODES_KEY)
    if not isinstance(declared, list):
        return DEFAULT_EXIT_CODES
    codes = frozenset(c for c in declared if isinstance(c, int) and not isinstance(c, bool))
    return codes if codes else DEFAULT_EXIT_CODES


def _collect(work: Path, name: str, command: str, timeout: float, ok: frozenset[int]) -> frozenset[str]:
    try:
        proc = slots.run_command(command, work, timeout)
    except subprocess.TimeoutExpired as exc:
        raise CollectError(name, command, f"{timeout:g} 秒の上限を超えた") from exc
    except OSError as exc:
        raise CollectError(name, command, f"起動できない: {exc}") from exc
    if proc.returncode not in ok:
        raise CollectError(name, command, f"終了コード {proc.returncode}: {proc.stderr.strip()[-400:]}")
    return tests_decide.collected_files(proc.stdout)


def collected(work: Path, paths: Sequence[str], suites: Sequence[Mapping[str, Any]], timeout: float) -> tests_decide.CollectVerdict:
    """足したテストのファイル（作業場所からの相対パス）を、収集される・されない・判定できないに分ける。"""
    got: dict[str, frozenset[str] | None] = {}
    for name, group in _group(paths, suites).items():
        template = str(group.decl[COLLECT_KEY])
        command = scoped.fill_paths(template, group.files) if scoped.PATHS_SLOT in template else template
        found = _collect(work, name, command, timeout, exit_codes_of(group.decl))
        got.update({f: found for f in group.files})
    return tests_decide.collect_verdict(paths, got)
