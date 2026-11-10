"""構造改善を飛ばしてよいかを、差分から判定する（#494）。

`assess` を持つ。状態ファイルは読まない（`init` より前に呼ぶため）。
"""

from __future__ import annotations

import argparse
import sys

import diff_kinds
import proc

from .. import paths
from ..gitfacts import production_code_changes

DEFAULT_MAX_LINES = 10

PASS = 0
SKIP = 3
UNDECIDABLE = 2


def cmd_assess(args: argparse.Namespace) -> None:
    """`<base>...HEAD` の本番コードの差分から、構造改善を通すか飛ばしてよいかを出す。

    終了コード: 0 = 通す / 3 = 飛ばしてよい / 2 = 対象のリポジトリか `<base>` を解けない /
    69 = 依存が欠けた（`deps.require` が `refactor.py` の import の時点で止めた。判定していない）。
    **2 を飛ばしてよいと読まない。** 判定できないことは飛ばす理由にならない。

    差分が文書のファイルだけ（`lib/diff_kinds.py`。#2925）なら、行数を数えずに 3 を出す。差分の一覧か
    文書の分け方の宣言が読めなければ、文書だけと判定せず本番コードの行数の判定へ進む。

    退避の 3 条件（テストが無い・CLI が使えない・範囲を絞れない）は見ない。それらは
    `init` が止めて知らせる。
    """
    try:
        root = paths.target_repo_root()
    except proc.StepError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(UNDECIDABLE)
    kinds = diff_kinds.classify_root(root, diff_kinds.git_changed(root, f"{args.base}...HEAD"))
    if kinds.docs_only():
        print("判定: 飛ばしてよい")
        print(f"理由: 差分が文書のファイルだけ（文書 {len(kinds.docs)} ファイル）")
        sys.exit(SKIP)
    changes = production_code_changes(str(root), args.base)
    if changes is None:
        print(f"ERROR: 起点 {args.base} から HEAD までの差分を取れません", file=sys.stderr)
        sys.exit(UNDECIDABLE)
    total = sum(n for _, n in changes)
    if not changes:
        verdict, reason = SKIP, "本番コードの差分がありません"
    elif total <= args.max_lines:
        verdict = SKIP
        reason = f"本番コードの変更が {total} 行で、上限 {args.max_lines} 行以下です"
    else:
        verdict, reason = PASS, f"本番コードの変更が {total} 行です"
    counted = f"本番コード: {len(changes)} ファイル・{total} 行"
    if changes:
        counted += f"（{'、'.join(p for p, _ in changes)}）"
    print("判定: " + ("通す" if verdict == PASS else "飛ばしてよい"))
    print("理由: " + reason)
    print(counted)
    sys.exit(verdict)
