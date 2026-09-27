"""`--scope` とテストの置き場所の関門（#436 決定 5・#1334 決定 12）。

**案内だけでは同じ失敗を繰り返す。** 実測では 4 ラウンド続けて同じ理由で項目が落ちた。
**止めれば、利用者は 1 度だけ範囲を直せばよい。**

見るのは 1 つ、`--scope` にテストの置き場所が含まれているかである。含まれていないと、テスト整備ラウンドが足す
テストが範囲外になり、その項目は必ず失敗する。範囲テストは雛形の `{paths}` に置き場所が直に入るため、
コマンドの実行集合を読む判定は持たない（コマンドの語を読まない。#1334 I1）。
"""

from __future__ import annotations

import fnmatch
import os
import pathlib
import subprocess
from typing import Iterable, Optional

from . import die

# テストの置き場所とみなすディレクトリの名前。**言語をまたいで使われるものだけ**を
# 並べる。増やすほど「テストの置き場所がある」と誤って判定して関門が素通りする。
TEST_PATH_SEGMENTS: tuple[str, ...] = (
    "test",
    "tests",
    "spec",
    "specs",
    "__tests__",
    "testing",
)

# テストのファイル名の形。`--scope` にファイルを直接並べる運用と、テストを本体と同じ場所に置く構成
# （`src/**/*.spec.ts`）のために見る。
TEST_NAME_PATTERNS: tuple[str, ...] = (
    "test_*",
    "*_test.*",
    "*.test.*",
    "*_spec.*",
    "*.spec.*",
)


def _matches_by_name(path: str) -> bool:
    """名前だけで置き場所と読めるか。**実在は見ない。**

    `--scope` は提案の範囲の宣言であり、まだ存在しないディレクトリを指すことがある。
    """
    parts = [p.lower() for p in pathlib.PurePosixPath(str(path).strip()).parts]
    parts = [p for p in parts if p not in (".", "/")]
    if not parts:
        return False
    if any(p in TEST_PATH_SEGMENTS for p in parts):
        return True
    return any(fnmatch.fnmatch(parts[-1], pat) for pat in TEST_NAME_PATTERNS)


def _child_test_location(path: str, work: str) -> Optional[str]:
    """配下に実在するテストの置き場所を 1 つ返す。無ければ `None`。

    **走査は 1 階層だけである。** 深く潜ると、無関係な階層のテストを根拠にして
    関門が素通りする。返すのは当たった置き場所であり、渡された親ではない。
    """
    base = pathlib.Path(work) / str(path).strip()
    try:
        entries = sorted(entry.name for entry in base.iterdir() if entry.is_dir())
    except OSError:
        return None
    for name in entries:
        if name.lower() in TEST_PATH_SEGMENTS:
            return os.path.normpath(f"{str(path).strip()}/{name}")
    return None


def tracked_files_under(path: str, work: str) -> list[str]:
    """`path` の配下の追跡ファイル（`git ls-files`）。リポジトリでなければ空。"""
    try:
        p = subprocess.run(["git", "ls-files", "-z", "--", str(path).strip() or "."], cwd=work, capture_output=True)
    except OSError:
        return []
    if p.returncode != 0:
        return []
    return [f for f in p.stdout.decode("utf-8", "replace").split("\0") if f]


def test_files_under(path: str, work: str) -> list[str]:
    """`path` の配下の追跡ファイルのうち、名前がテストの形（`TEST_NAME_PATTERNS`）のもの。"""
    return [f for f in tracked_files_under(path, work) if any(fnmatch.fnmatch(pathlib.PurePosixPath(f).name, pat) for pat in TEST_NAME_PATTERNS)]


def is_test_location(path: str, work: str) -> bool:
    """その `--scope` の 1 件がテストの置き場所かどうか。

    名前で当たる → 配下の 1 階層にテストの名前のディレクトリがある → 配下の追跡ファイルにテストの名前の形が
    ある（テストを本体と同じ場所に置く構成。決定 12）の順に見る。
    """
    if _matches_by_name(path):
        return True
    if _child_test_location(path, work) is not None:
        return True
    return bool(test_files_under(path, work))


def test_locations(scope: Iterable[str], work: str) -> list[str]:
    """`--scope` のうち、テストの置き場所とみなせるもの。

    **返すのは置き場所そのものである。** 実体の走査で当たったときは、渡された親ではなく当たった配下を返す。
    本体と同じ場所にテストを置く構成では、その `--scope` の 1 件をそのまま返す。
    """
    found: list[str] = []
    for item in scope:
        if _matches_by_name(item):
            found.append(item)
            continue
        child = _child_test_location(item, work)
        if child is not None:
            found.append(child)
        elif test_files_under(item, work):
            found.append(os.path.normpath(str(item).strip()))
    return found


def covered_by_roots(location: str, roots: list[str]) -> bool:
    """テストの置き場所が範囲の中にあるか。**限定が無ければ全て入る。**"""
    if not roots:
        return True
    normalized = os.path.normpath(str(location))
    return any(normalized == root or normalized.startswith(root + "/") for root in roots)


def scope_problem(scope: Iterable[str], work: str) -> Optional[str]:
    """関門に引っかかる理由を返す。問題が無ければ `None`。"""
    listed = list(scope)
    if not test_locations(listed, work):
        return (
            "--scope にテストの置き場所が含まれていません"
            f"（指定: {', '.join(listed) or '（なし）'}）。"
            "テスト整備ラウンドは現状固定テストを --scope の中へ足すため、"
            "含めないとその項目は必ず失敗します。"
            "例: --scope src/services tests/services"
        )
    return None


def require_scope_covers_tests(scope: Iterable[str], work: str) -> None:
    """関門を通す。通らなければ**中断する**（終了コード 4）。"""
    problem = scope_problem(scope, work)
    if problem:
        die(problem)
