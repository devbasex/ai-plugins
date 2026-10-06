"""修正の取り込み（`merge-fix`）の入口。中身は `fix_intake.py` にある（検証も同じものを呼ぶ）。"""

from __future__ import annotations

import argparse

from .. import fix_intake


def cmd_merge_fix(args: argparse.Namespace) -> None:
    """修正の結果を取り込む。取り込んだ項目は `implemented` へ戻り、次の `verify` が見直す（`fix_intake.merge`）。"""
    fix_intake.merge(args.id)
