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
from typing import Any, Callable, Iterable, Optional

from . import die
from .gitfacts import is_test_path

# テストの置き場所とみなすディレクトリの名前。**言語をまたいで使われるものだけ**を
# 並べる。増やすほど「テストの置き場所がある」と誤って判定して関門が素通りする。
TEST_PATH_SEGMENTS: tuple[str, ...] = (
    "test", "tests", "spec", "specs", "__tests__", "testing",
)

# テストのファイル名の形。`--scope` にファイルを直接並べる運用のために見る。
TEST_NAME_PATTERNS: tuple[str, ...] = (
    "test_*", "*_test.*", "*.test.*", "*_spec.*", "*.spec.*",
)


# `--round-test` のうち、次の語を値として取ると分かっているオプション（#880）。
# `--opt=value` の形は 1 語に値を含むため並べない。**知らないオプションの直後の語は
# 起点に数える**（`round_test_roots`）。ここに無い値付きオプションは関門を止める側に
# 倒れるだけで、範囲の外を素通りさせない。
VALUE_OPTIONS: frozenset[str] = frozenset({
    # uv run
    "--project", "--directory", "--with", "--with-editable", "--with-requirements",
    "--python", "-p", "--package", "--extra", "--group", "--only-group",
    "--env-file", "--index", "--default-index", "--index-url", "--extra-index-url",
    "--find-links", "-f", "--cache-dir", "--config-file",
    # pytest
    "--rootdir", "-c", "--confcutdir", "--basetemp", "-k", "-m", "-n",
    "--numprocesses", "--dist", "-o", "--override-ini", "--tb", "--ignore",
    "--ignore-glob", "--deselect", "--junit-xml", "--junitxml", "--log-file",
    "--cov", "--cov-report", "--cov-config", "--maxfail", "--durations",
    "-W", "--pythonwarnings", "--import-mode", "--capture", "-r",
})


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
    return _scope_roots(
        command, work,
        lambda word, previous, normalized: (pathlib.Path(work) / word).is_dir(),
    )


def covered_by_roots(location: str, roots: list[str]) -> bool:
    """テストの置き場所が探索範囲の中にあるか。**限定が無ければ全て入る。**"""
    if not roots:
        return True
    normalized = os.path.normpath(str(location))
    return any(
        normalized == root or normalized.startswith(root + "/") for root in roots
    )


def round_test_roots(command: str, work: str) -> list[str]:
    """`--round-test` の実行集合の起点を返す（#880）。空なら全体を走らせるとみなす。

    `baseline_search_roots` と違い、**テストの置き場所に当たる実在するファイルも起点に
    数える。** 範囲のテストは 1 ファイルを名指しすることがあり、それを限定なしと読むと
    範囲の置き場所を走らせないコマンドが関門を通る。

    数えない語は 3 つある。先頭の語（プログラム名）と `-` で始まる語、値を取ると
    分かっているオプションの直後の語（`--project .` の `.`）、作業ディレクトリの根
    （`.`）である。テストの置き場所でないファイル（`bash scripts/run-scope-tests.sh` の
    ラッパー）も数えない。**ラッパーの中身は解析しない。** 範囲の外を走らせても、
    最終ゲートの全体テストが見る。

    **値を取るかが分からないオプションの直後の語は起点に数える。** 値とみなして
    消すと、`pytest --verbose tests/unit` の唯一の対象が消えて起点が空になり、全体を
    覆うとみなして範囲の外だけを走らせるコマンドが関門を通る。数えすぎたときは
    関門が止まる側に倒れ、利用者が 1 度直せば済む。
    """
    def accept(word: str, previous: str, normalized: str) -> bool:
        if previous in VALUE_OPTIONS:
            return False
        if normalized == ".":
            return False
        target = pathlib.Path(work) / word
        return target.is_dir() or (target.is_file() and is_test_path(normalized))

    return _scope_roots(command, work, accept)


def _scope_roots(
    command: str, work: str, accept: Callable[[str, str, str], bool]
) -> list[str]:
    """コマンドの語のうち `accept(語, 直前の語, 正規化した語)` が真のものを返す。

    **先頭の語**（プログラム名）と `-` で始まる語、絶対パスは見ない。正規化した
    語を重複なく、現れた順に集める。語として読めないコマンドは空を返す。
    """
    try:
        words = shlex.split(str(command or ""))
    except ValueError:
        return []
    roots: list[str] = []
    previous = words[0] if words else ""
    for word in words[1:]:
        before, previous = previous, word
        if word.startswith("-") or os.path.isabs(word):
            continue
        normalized = os.path.normpath(word)
        if accept(word, before, normalized) and normalized not in roots:
            roots.append(normalized)
    return roots


def round_test_command(state: dict[str, Any]) -> str:
    """群と修正コミットの検証に使うコマンド（#880）。

    `round_test` を持たない状態ファイル（変更の前の実行）は `baseline_test` を返す。
    再開した実行の検証を、変更の前と同じにするためである。
    """
    command = (state.get("round_test") or {}).get("command")
    if command:
        return str(command)
    return str((state.get("baseline_test") or {}).get("command") or "")


def _example_program(baseline_test: str, roots: list[str]) -> str:
    """案内の例に使うプログラムの部分。起点があれば、最初の起点より前の語である。"""
    try:
        words = shlex.split(str(baseline_test or ""))
    except ValueError:
        return str(baseline_test or "")
    for index, word in enumerate(words):
        if roots and os.path.normpath(word) in roots:
            return " ".join(words[:index])
    while len(words) > 1 and words[-1].startswith("-"):
        words.pop()
    return " ".join(words)


def round_test_hint(
    round_test: Optional[str], baseline_test: str,
    scope: Iterable[str], work: str,
) -> Optional[str]:
    """`--round-test` を渡せば群ごとの検証が短くなるときに、案内の 1 行を返す（#880）。

    案内するのは、`--round-test` が無く、`--baseline-test` の探索の起点が無いか
    `--scope` のテストの置き場所より広いときである。**止めない。** 全体を走らせても
    検証として誤りではなく、時間が掛かるだけである。
    """
    if round_test:
        return None
    locations = test_locations(list(scope), work)
    if not locations:
        return None
    roots = baseline_search_roots(baseline_test, work)
    if roots and all(covered_by_roots(root, locations) for root in roots):
        return None
    example = f"{_example_program(baseline_test, roots)} {' '.join(locations)}".strip()
    return (
        "ℹ --baseline-test は --scope より広い範囲を走らせます。"
        "群ごとの検証を短くするには --round-test に範囲のテストを渡します"
        f"（例: {example}）"
    )


def scope_problem(
    scope: Iterable[str], command: str, work: str, round_test: bool = False
) -> Optional[str]:
    """関門に引っかかる理由を返す。問題が無ければ `None`。

    `round_test` が真なら、`command` を `--round-test` として読む（#880）。群の検証が
    走らせるのは範囲のテストであり、足したテストが入るべき実行集合はこちらである。
    """
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
    option = "--round-test" if round_test else "--baseline-test"
    roots = (round_test_roots if round_test else baseline_search_roots)(command, work)
    outside = [loc for loc in locations if not covered_by_roots(loc, roots)]
    if outside:
        return (
            f"--scope のテストの置き場所（{', '.join(outside)}）が "
            f"{option} の実行集合に入りません"
            f"（探索の起点: {', '.join(roots)}）。"
            "足したテストが一度も実行されず、検証の判定に効きません。"
            f"{option} の対象へ含めるか、--scope の置き場所を合わせてください"
        )
    return None


def require_scope_covers_tests(
    scope: Iterable[str], command: str, work: str, round_test: bool = False
) -> None:
    """関門を通す。通らなければ**中断する**（終了コード 4）。"""
    problem = scope_problem(scope, command, work, round_test=round_test)
    if problem:
        die(problem)
