"""範囲のテスト: 宣言の suite の `scope_command` の `{paths}` へファイルを入れて、範囲だけを打つコマンドを組む。

- 宣言（`.ndf/project.json` の `test.suites[]`）のうち、`scope_command` に `{paths}` を持つ suite が範囲を打てる
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
class ScopedRun:
    """範囲のテストの 1 回: suite の名前・打つコマンド・当てたファイル。"""

    suite: str
    command: str
    files: tuple[str, ...]


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


def _scope_paths(raw: Mapping[str, Any]) -> tuple[str, ...]:
    paths = raw.get("paths")
    return tuple(str(p) for p in paths) if isinstance(paths, list) else (".",)


def scoped_runs(suites: Any, files: Sequence[str]) -> tuple[list[ScopedRun], list[str]]:
    """`files` を、範囲を打てるテストの suite（宣言の順）へ当てたコマンドと、どの suite にも当たらないファイル。

    1 つのファイルは、当たる最初の suite だけへ入れる（同じテストを 2 度打たない）。当たるファイルの無い suite は打たない。
    """
    rest = list(files)
    runs: list[ScopedRun] = []
    for raw in suites if isinstance(suites, list) else []:
        template = raw.get(SCOPE_KEY) if isinstance(raw, dict) else None
        if not isinstance(template, str) or PATHS_SLOT not in template or not weight.is_test_suite(raw):
            continue
        scope = _scope_paths(raw)
        taken = [f for f in rest if in_scope(f, scope)]
        if taken:
            runs.append(ScopedRun(str(raw["name"]), fill_paths(template, taken), tuple(taken)))
            rest = [f for f in rest if f not in taken]
    return runs, rest
