#!/usr/bin/env python3
"""refactor-scope.py: 検査のプランが cross-refactoring へ渡す `--scope` の範囲を組んで出す（#1484）。

    refactor-scope.py --pr N [--tests PATH...] [--scope PATH...] [--root DIR]

範囲は次の順に足し、重複と、他の項目のディレクトリの中に入る項目を除いて空白区切りで 1 行に出す。

1. PR の差分のファイルそのもの（消したファイルは除く。ディレクトリへ広げない）。実装で新設したファイルも入る
2. 宣言のテストの置き場所（`--tests`。`.` は数えない）
3. 2 が無いとき、変更したファイルの近くのテストの置き場所（ファイルのディレクトリから根へ向かって最初に見つかる
   `tests` か `test` のディレクトリ。テストの置き場所の中のファイルは探さない）
4. 明示した範囲（`--scope`）。置き換えずに足す

ファイルの一覧は REST から取る（`gh pr diff` は差分が 20000 行を超えると 406 で拒み、範囲が空になる）。
一覧を取れなければ何も出さずに終了コード 2 で終える（範囲を黙って広げも空にもしない）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import gh_rest  # noqa: E402

TEST_DIRS = ("tests", "test")
EXIT_UNREADABLE = 2


def diff_files(pr: str, root: Path) -> list[str]:
    """PR の差分のファイル（消したファイルを除く）。読めなければ RuntimeError（文は gh の stderr）。"""
    r = gh_rest.pr_files(int(pr), cwd=str(root))
    if r.returncode != 0:
        raise RuntimeError(r.stderr or f"gh が終了コード {r.returncode} で終えた")
    return [f["path"] for f in json.loads(r.stdout) if f.get("status") != "removed"]


def _in_test_dir(path: PurePosixPath) -> bool:
    return any(part in TEST_DIRS for part in path.parent.parts)


def near_tests(files: list[str], root: Path) -> list[str]:
    """変更したファイルごとに、ディレクトリから根へ向かって最初に見つかるテストの置き場所。"""
    found: list[str] = []
    for f in files:
        p = PurePosixPath(f)
        if _in_test_dir(p):
            continue
        for d in [*p.parents]:
            hit = next((d / t for t in TEST_DIRS if (root / d / t).is_dir()), None)
            if hit is not None:
                found.append(str(hit))
                break
    return found


def _covered(item: str, others: list[str]) -> bool:
    p = PurePosixPath(item)
    return any(o != item and PurePosixPath(o) in p.parents for o in others)


def build_scope(files: list[str], tests: list[str], scope: list[str], root: Path) -> list[str]:
    """`--scope` に渡す範囲（順序を保ち、重複と他の項目のディレクトリの中の項目を除く）。"""
    declared = [t.rstrip("/") for t in tests if t.strip() not in (".", "", "./")]
    items = [*files, *(declared or near_tests(files, root)), *scope]
    uniq = list(dict.fromkeys(i.rstrip("/") or i for i in items if i))
    return [i for i in uniq if not _covered(i, uniq)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pr", required=True, help="検査する Pull Request の番号")
    ap.add_argument("--tests", nargs="*", default=[], metavar="PATH", help="宣言のテストの置き場所")
    ap.add_argument("--scope", nargs="*", default=[], metavar="PATH", help="明示した構造改善の範囲（足す）")
    ap.add_argument("--root", default=".", help="リポジトリの根（既定 .）")
    a = ap.parse_args(argv)
    root = Path(a.root)
    try:
        files = diff_files(a.pr, root)
    except (OSError, ValueError, RuntimeError) as e:
        detail = str(e)
        print(f"PR #{a.pr} の差分のファイルを取れない: {detail.strip()}", file=sys.stderr)
        return EXIT_UNREADABLE
    print(" ".join(build_scope(files, a.tests, a.scope, root)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
