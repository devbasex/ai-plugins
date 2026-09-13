"""`--scope` とテストの置き場所の関門（#436 決定 5）。

**案内だけでは同じ失敗を繰り返す。** 実測では 4 ラウンド続けて同じ理由で項目が
落ちた。**止めれば、利用者は 1 度だけ範囲を直せばよい。**

見るのは 2 つで、**1 つの関門でまとめて見る**。直す先はどちらも利用者が与える
引数であり、別々に止めると 2 度直すことになる。

| 見るもの | 見ない場合に起きること |
| --- | --- |
| `--scope` にテストの置き場所が含まれているか | テスト整備ラウンドが足すテストが範囲外になり、その項目は必ず失敗する |
| その置き場所が `--baseline-test` の実行集合に入るか | 足したテストが一度も実行されず、検証（Step 5）の判定に効かない |
"""
from __future__ import annotations

import fnmatch
import os
import pathlib
import shlex
from typing import Iterable, Optional

from . import die

# テストの置き場所とみなすディレクトリの名前。**言語をまたいで使われるものだけ**を
# 並べる。増やすほど「テストの置き場所がある」と誤って判定して関門が素通りする。
TEST_PATH_SEGMENTS: tuple[str, ...] = (
    "test", "tests", "spec", "specs", "__tests__", "testing",
)

# テストのファイル名の形。`--scope` にファイルを直接並べる運用のために見る。
TEST_NAME_PATTERNS: tuple[str, ...] = (
    "test_*", "*_test.*", "*.test.*", "*_spec.*", "*.spec.*",
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

    **走査は 1 段だけである。** 深く潜ると、無関係な階層のテストを根拠にして
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


def is_test_location(path: str, work: str) -> bool:
    """その `--scope` の 1 件がテストの置き場所かどうか。

    **名前で当たらないときだけ実体を見る**（#518-2）。名前だけの判定では、
    実体として `tests/` を持つ親ディレクトリを渡した実行が関門で止まっていた。
    """
    if _matches_by_name(path):
        return True
    return _child_test_location(path, work) is not None


def test_locations(scope: Iterable[str], work: str) -> list[str]:
    """`--scope` のうち、テストの置き場所とみなせるもの。

    **返すのは置き場所そのものである。** 実体の走査で当たったときは、渡された
    親ではなく当たった配下を返す。後段の `covered_by_roots` は
    `--baseline-test` が限定した起点で始まるかを見るため、親を返すと**関門を
    通した直後に同じ関門の別の判定が拒む**。
    """
    found: list[str] = []
    for item in scope:
        if _matches_by_name(item):
            found.append(item)
            continue
        child = _child_test_location(item, work)
        if child is not None:
            found.append(child)
    return found


def baseline_search_roots(command: str, work: str) -> list[str]:
    """`--baseline-test` が探索範囲を限定している語を返す。空なら限定なし。

    **限定とみなすのはディレクトリだけである。** ファイルを指す語は実行する
    スクリプトそのもの（`bash scripts/run-tests.sh`）であることが多く、探索範囲の
    宣言とは限らない。**先頭の語**（プログラム名）と `-` で始まる語も見ない。

    語として読めないコマンド（引用符が閉じていないなど）は限定なしとして扱う。
    ここは範囲の宣言を読むための補助であり、コマンドの妥当性を判定する場所ではない。
    """
    try:
        words = shlex.split(str(command or ""))
    except ValueError:
        return []
    roots: list[str] = []
    for word in words[1:]:
        if word.startswith("-") or os.path.isabs(word):
            continue
        if not (pathlib.Path(work) / word).is_dir():
            continue
        normalized = os.path.normpath(word)
        if normalized not in roots:
            roots.append(normalized)
    return roots


def covered_by_roots(location: str, roots: list[str]) -> bool:
    """テストの置き場所が探索範囲の中にあるか。**限定が無ければ全て入る。**"""
    if not roots:
        return True
    normalized = os.path.normpath(str(location))
    return any(
        normalized == root or normalized.startswith(root + "/") for root in roots
    )


def scope_problem(
    scope: Iterable[str], baseline_test: str, work: str
) -> Optional[str]:
    """関門に引っかかる理由を返す。問題が無ければ `None`。"""
    listed = list(scope)
    locations = test_locations(listed, work)
    if not locations:
        return (
            "--scope にテストの置き場所が含まれていません"
            f"（指定: {', '.join(listed) or '（なし）'}）。"
            "テスト整備ラウンドは現状固定テストを --scope の中へ足すため、"
            "含めないとその項目は必ず失敗します。"
            "例: --scope src/services tests/services"
        )
    roots = baseline_search_roots(baseline_test, work)
    outside = [loc for loc in locations if not covered_by_roots(loc, roots)]
    if outside:
        return (
            f"--scope のテストの置き場所（{', '.join(outside)}）が "
            f"--baseline-test の実行集合に入りません"
            f"（探索の起点: {', '.join(roots)}）。"
            "足したテストが一度も実行されず、検証の判定に効きません。"
            "--baseline-test の対象へ含めるか、--scope の置き場所を合わせてください"
        )
    return None


def require_scope_covers_tests(
    scope: Iterable[str], baseline_test: str, work: str
) -> None:
    """関門を通す。通らなければ**中断する**（終了コード 4）。"""
    problem = scope_problem(scope, baseline_test, work)
    if problem:
        die(problem)
