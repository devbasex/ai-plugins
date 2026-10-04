"""構造改善を飛ばしてよいかを、差分から判定する（#494）。

`assess` を持つ。状態ファイルは読まない（`init` より前に呼ぶため）。
"""

from __future__ import annotations

import argparse
import sys

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

    退避の 3 条件（テストが無い・CLI が使えない・範囲を絞れない）は見ない。それらは
    `init` が止めて知らせる。
    """
    try:
        root = paths.target_repo_root()
    except proc.StepError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(UNDECIDABLE)
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
