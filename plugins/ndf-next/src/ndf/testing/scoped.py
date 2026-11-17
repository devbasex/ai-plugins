"""範囲のテスト: 設定の suite の `scope_command` の `{paths}` へファイルを入れて、範囲だけを打つコマンドを組む。

- 設定（`.ndf/project.json` の `test.suites[]`）のうち、`scope_command` に `{paths}` を持つ suite が範囲を打てる
- ファイルは suite の `paths`（ファイルかディレクトリの並び。省けば作業場所の全体）の中のものだけを当てる
- CI の落ちたテストの id（pytest の node id の `<ファイル>::<名前>` など）は、ファイルの部分へ戻して当てる

コマンドを打たない（組むだけ）。打つのは呼び出し元（`ndf route test`・cross-refactoring の検証）である。
"""

from __future__ import annotations

import shlex
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ndf.testing import weight

PATHS_SLOT = "{paths}"
SCOPE_KEY = "scope_command"
# テストの id のうち、ファイルと名前を分ける区切り（pytest の node id）
NODE_SEPARATOR = "::"


@dataclass(frozen=True)
class Suite:
    """範囲を打てる設定の suite 1 件: 名前・`scope_command`・`paths`（省けば `.`）・テストか（`kind` が `lint` でない）。"""

    name: str
    template: str
    paths: tuple[str, ...]
    test: bool


@dataclass(frozen=True)
class ScopedRun:
    """範囲のテストの 1 回: suite の名前・打つコマンド・当てたファイル。"""

    suite: str
    command: str
    files: tuple[str, ...]


def ci_only(test: Mapping[str, Any]) -> bool:
    """設定の `test` のセクションがテストを CI に寄せる（`ci.only` が真）か。"""
    ci = test.get("ci")
    return isinstance(ci, dict) and ci.get("only") is True


def in_scope(path: str, scope: Sequence[str]) -> bool:
    """`path` が範囲（ファイルかディレクトリの並び）の中か。`.` は作業場所の全体。"""
    for loc in scope:
        base = loc.rstrip("/")
        if base in ("", ".") or path == base or path.startswith(base + "/"):
            return True
    return False


def fill_paths(template: str, files: Sequence[str]) -> str:
    """テンプレートの `{paths}` へファイルを引用して入れる。`{paths}` の無いテンプレートは `ValueError`。"""
    if PATHS_SLOT not in template:
        raise ValueError(f"テンプレートに {PATHS_SLOT} が無い: {template}")
    return template.replace(PATHS_SLOT, " ".join(shlex.quote(f) for f in files))


def files_of(test_ids: Iterable[str]) -> list[str]:
    """テストの id をファイルへ戻す（`::` より前。重なりを除き、最初に出た順）。"""
    found = (t.split(NODE_SEPARATOR, 1)[0] for t in test_ids)
    return list(dict.fromkeys(f for f in found if f))


def suite_of(raw: Any) -> Suite | None:
    """設定の suite 1 件を読む。`scope_command` に `{paths}` を持たない suite と、dict でない要素は None。"""
    if not isinstance(raw, dict):
        return None
    template = raw.get(SCOPE_KEY)
    if not isinstance(template, str) or PATHS_SLOT not in template:
        return None
    paths = raw.get("paths")
    scope = tuple(str(p) for p in paths) if isinstance(paths, list) else (".",)
    return Suite(str(raw["name"]), template, scope, weight.is_test_suite(raw))


def scoped_runs(suites: Any, files: Sequence[str]) -> tuple[list[ScopedRun], list[str]]:
    """`files` を、範囲を打てるテストの suite（設定の順）へ当てたコマンドと、どの suite にも当たらないファイル。

    1 つのファイルは、当たる最初の suite だけへ入れる（同じテストを 2 度打たない）。当たるファイルの無い suite は打たない。
    """
    rest = list(files)
    runs: list[ScopedRun] = []
    for s in (suite_of(r) for r in (suites if isinstance(suites, list) else [])):
        if s is None or not s.test:
            continue
        taken = [f for f in rest if in_scope(f, s.paths)]
        if taken:
            runs.append(ScopedRun(s.name, fill_paths(s.template, taken), tuple(taken)))
            rest = [f for f in rest if f not in taken]
    return runs, rest
