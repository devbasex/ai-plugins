"""検査のプランが cross-refactoring へ渡す `--scope` の範囲（#1484）。

範囲は次の順に足し、重複と、他の項目のディレクトリの中に入る項目を除く。

1. 差分のコードのファイルそのもの（消したファイルは除く。ディレクトリへ広げない）。コードかは領域の表 `languages` の
   `code` が真の言語の拡張子で決め、文書（`.md` など）は入れない
2. 宣言のテストの置き場所（`.` は数えない）
3. 2 が無いとき、変更したコードのファイルの近くのテストの置き場所（ファイルのディレクトリから根へ向かって最初に見つかる、
   表 `test-files` の置き場の名前のディレクトリ。テストの置き場所の中のファイルは探さない）
4. 明示した範囲。置き換えずに足す
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import PurePosixPath

from ndf.contracts import facts
from ndf.domains.crossrefactor.tests_decide import TABLE as TEST_TABLE

LANGUAGES = "languages"
# 宣言のテストの置き場所のうち、範囲に数えないもの（リポジトリ全体）
WHOLE = frozenset({".", "", "./"})


def _code_suffixes() -> tuple[str, ...]:
    exts = [e for r in facts.rows(LANGUAGES, code=True) for e in r["extensions"]]
    return tuple(sorted(exts, key=len, reverse=True))


def is_code(path: str) -> bool:
    """`path` がコードのファイルか（拡張子が表 `languages` の `code` の言語のもの）。"""
    name = PurePosixPath(path).name
    return any(name.endswith(e) for e in _code_suffixes())


def test_dir_names() -> frozenset[str]:
    """テストの置き場所の名前（表 `test-files` の置き場の行）。"""
    return frozenset(str(r["pattern"]) for r in facts.rows(TEST_TABLE, where="dir"))


def in_test_dir(path: str) -> bool:
    return any(part in test_dir_names() for part in PurePosixPath(path).parent.parts)


def near_candidates(path: str) -> list[str]:
    """`path` の近くのテストの置き場所の候補（近い順）。テストの置き場所の中のファイルは候補を持たない。"""
    if in_test_dir(path):
        return []
    names = sorted(test_dir_names())
    return [str(d / n) if str(d) != "." else n for d in PurePosixPath(path).parents for n in names]


def _covered(item: str, others: Sequence[str]) -> bool:
    p = PurePosixPath(item)
    return any(o != item and PurePosixPath(o) in p.parents for o in others)


def compose(files: Iterable[str], declared: Sequence[str], near: Mapping[str, str | None], explicit: Sequence[str]) -> list[str]:
    """範囲を組む。`files` は差分の在るファイル、`near` はファイル → 見つかった近くのテストの置き場所（無ければ None）。"""
    code = [f for f in files if is_code(f)]
    tests = [t.rstrip("/") for t in declared if t.strip() not in WHOLE]
    if not tests:
        tests = [d for f in code if (d := near[f] if f in near else None) is not None]
    items = [*code, *tests, *explicit]
    uniq = list(dict.fromkeys(i.rstrip("/") or i for i in items if i))
    return [i for i in uniq if not _covered(i, uniq)]
