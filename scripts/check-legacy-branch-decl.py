#!/usr/bin/env python3
"""check-legacy-branch-decl.py: 旧いブランチ戦略の宣言のキーが、削除の一覧のパスに残っていないかを照らす（#3672 の受け入れ条件 21・I8）。

    python3 scripts/check-legacy-branch-decl.py [--root DIR] [--data FILE]

削除の一覧（`scripts/legacy-branch-decl.json`）の各行は `id`（`D<数>`）・`path`・`words` を持つ。行のパスのファイルに、
語が宣言のキーの形（`"語"`・`'語'`・`.語`・`` `語` ``）で 1 件でも残れば落ちる。パスのファイルが無い行は通す（ファイルごと消した行）。

終了コード: 0 = 残りなし / 1 = 残りあり / 2 = 一覧かファイルを読めない。標準ライブラリだけで書く。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "scripts" / "legacy-branch-decl.json"
PREFIX = "[check-legacy-branch-decl]"


class DataError(Exception):
    """一覧かパスのファイルを読めない。"""


def load_rows(path: Path) -> list[dict]:
    """削除の一覧のデータを読む。各行は `{"id": "D<数>", "path", "words"}`。"""
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))["rows"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise DataError(f"{path}: 一覧を読めない（{e}）") from e
    for r in rows if isinstance(rows, list) else [None]:
        if not (
            isinstance(r, dict)
            and re.fullmatch(r"D\d+[a-d]?(-\d+)?", str(r.get("id")))
            and isinstance(r.get("path"), str)
            and isinstance(r.get("words"), list)
            and r["words"]
        ):
            raise DataError(f"{path}: 形が違う行: {r!r}")
    return rows


def word_pattern(word: str) -> re.Pattern[str]:
    """宣言のキーとして読む・書く形だけに当たる正規表現（変数名の一部には当てない）。"""
    w = re.escape(word)
    return re.compile(rf'"{w}"|\'{w}\'|(?<![\w])\.{w}\b|`{w}`')


def scan_rows(root: Path, rows: list[dict]) -> list[dict]:
    """各行のパスに消す語が残る箇所を並べる。各要素は `{"id", "path", "line", "word"}`。"""
    hits = []
    for r in rows:
        p = root / r["path"]
        if not p.is_file():
            continue
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as e:
            raise DataError(f"{p}: 読めない（{e}）") from e
        pats = [(w, word_pattern(w)) for w in r["words"]]
        for n, line in enumerate(lines, 1):
            for w, pat in pats:
                if pat.search(line):
                    hits.append({"id": r["id"], "path": r["path"], "line": n, "word": w})
    return hits


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--data", default=str(DATA))
    a = ap.parse_args(argv)
    try:
        hits = scan_rows(Path(a.root), load_rows(Path(a.data)))
    except DataError as e:
        print(f"{PREFIX} {e}", file=sys.stderr)
        return 2
    for h in hits:
        print(f"{PREFIX} {h['id']} {h['path']}:{h['line']}: 旧い宣言のキー `{h['word']}` が残っている")
    if hits:
        print(f"{PREFIX} {len(hits)} 件。新しいセクション branch_strategy を読む部品（lib/branch_strategy.py）へ替える")
        return 1
    print(f"{PREFIX} 残りなし")
    return 0


if __name__ == "__main__":
    sys.exit(main())
