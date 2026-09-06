#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""cross-refactoring の状態管理 CLI。

`<work>/.cross_refactoring/cross-refactoring-rf<ID>-state.json` の初期化・読み書きと、
二段の収束判定（提案ラウンドの繰り返しの中にレビュー収束の繰り返しが入る）を
1 つの CLI に集約する。

サブコマンドの一覧と役割は `--help` が持つ。ここには写さない（片方だけが古くなるため）。

終了コードは呼び出し側の bash が分岐に使う。各サブコマンドの docstring を参照。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import signal
import subprocess
import sys
import time
import types
from typing import Any, Callable, Iterable, Optional

# 共通層はプラグインルート直下にある。**`.resolve()` を通す。** Kiro CLI は
# `.kiro/skills/<名前>` を symlink にするため、解かずに `parents[]` を数えると
# `.kiro` で止まってプラグインルートへ届かない。
sys.path.insert(
    0,
    str(pathlib.Path(__file__).resolve().parents[3] / "scripts" / "lib"),
)

import assignment  # noqa: E402
import auth  # noqa: E402
import metrics as metrics_lib  # noqa: E402
import models as models_lib  # noqa: E402
import statefile  # noqa: E402

# 分割したモジュールは同じディレクトリの `refactor_lib/` にある。**自身の
# ディレクトリを探索先へ入れる。** `uv run --script` で起動したときの現在地は、
# スクリプトの位置と揃わない。
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from refactor_lib import ABORT, die, info  # noqa: E402,F401
from refactor_lib import vocabulary as _vocabulary  # noqa: E402
from refactor_lib import rounds as _rounds  # noqa: E402
from refactor_lib import paths as _paths  # noqa: E402
from refactor_lib import plan as _plan  # noqa: E402
from refactor_lib import gitfacts as _gitfacts  # noqa: E402
from refactor_lib import proposals as _proposals  # noqa: E402
from refactor_lib import verify as _verify  # noqa: E402
from refactor_lib.commands import report as _cmd_report  # noqa: E402
from refactor_lib.commands import setup as _cmd_setup  # noqa: E402
from refactor_lib.commands import apply as _cmd_apply  # noqa: E402
from refactor_lib.commands import review as _cmd_review  # noqa: E402

# **入口は全モジュールの名前を自分の名前空間へ取り込む。** 呼び出し側と手順書は
# `refactor.py` を指し、テストは `refactor.<名前>` を参照する。分割してもその形を
# 変えない。
_LIB_MODULES: tuple[types.ModuleType, ...] = (
    _vocabulary,
    _rounds,
    _paths,
    _plan,
    _gitfacts,
    _proposals,
    _verify,
    _cmd_report,
    _cmd_setup,
    _cmd_apply,
    _cmd_review,
)


def _reexport() -> None:
    """各モジュールの名前を入口へ取り込む。モジュール自身は取り込まない。"""
    for mod in _LIB_MODULES:
        for name, value in vars(mod).items():
            if name.startswith("__") or isinstance(value, types.ModuleType):
                continue
            globals()[name] = value


_reexport()


class _Entry(types.ModuleType):
    """`refactor.<名前>` の差し替えを、定義元のモジュールへも伝える。

    再エクスポートは値の写しであるため、入口だけを差し替えても、定義元を見て
    いる呼び出し側は元の値を使い続ける。**差し替えが片側にしか効かない状態を
    作らない。**
    """

    def __setattr__(self, name: str, value: Any) -> None:
        super().__setattr__(name, value)
        for mod in _LIB_MODULES:
            if name in vars(mod):
                setattr(mod, name, value)


sys.modules[__name__].__class__ = _Entry


# ---------------- main ----------------

def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    init = sub.add_parser(
        "init",
        help="Step 0 — ホスト確定 / 母集合の確定 / 作業ディレクトリ root / 状態初期化")
    init.add_argument("pr", type=int)
    init.add_argument("--scope", nargs="+", required=True,
                      help="対象範囲。提案が無制限に広がらないよう必須にしている")
    init.add_argument("--host", choices=list(assignment.HOST_RUNTIMES), default=None,
                      help="ホストの明示指定。未指定時は環境変数から推定する")
    # 既定は輪番の 1 周（適用の母集合の大きさ）。3 のままだと `ALL_RUNTIMES` の
    # 先頭にある claude の順番（ラウンド 4）へ届かない。
    init.add_argument("--max-outer-rounds", type=int,
                      default=len(assignment.ALL_RUNTIMES))
    init.add_argument("--max-fix-rounds", type=int, default=3)
    init.add_argument("--max-items-per-round", type=int, default=5)
    init.add_argument("--severity-threshold", default=DEFAULT_SEVERITY_THRESHOLD,
                      choices=[s for s in SEVERITY_ORDER if s != "unknown"])
    init.add_argument("--model", action="append", metavar="RUNTIME=MODEL",
                      help="ランタイムごとのモデル指定。繰り返し指定できる")
    init.add_argument("--test-timeout", type=int, default=DEFAULT_TEST_TIMEOUT,
                      help="テスト 1 回あたりの上限秒数。超えたら失敗として扱う "
                           f"(default: {DEFAULT_TEST_TIMEOUT})")
    init.add_argument("--sync-command", default=None,
                      help="生成物を同期するコマンド。**push の直前**に進行側が実行し、"
                           "差分があれば進行側のコミットとして積む。"
                           "同期を実装担当にさせると範囲外の変更になるため分離している")
    init.add_argument("--plan-file", default=None,
                      help="改修計画を書き出すパス（対象リポジトリからの相対）。"
                           "提案の理由と手順は状態ファイルにしか残らず、差分から"
                           "除外されるため、公開の直前に進行側が書き出す。"
                           f"既定は {DEFAULT_PLAN_DIR}/refactoring-plan-rf<PR>.md。"
                           "空文字を渡すと記録しない")
    init.add_argument("--baseline-test", required=True,
                      help="着手前と各コミットで実行するテストコマンド。"
                           "振る舞い不変を示す手段が無い書き換えは構造改善ではないため必須")
    init.add_argument("--worktree-root", default=None)
    init.set_defaults(func=cmd_init)

    for name, func, help_ in (
        ("start-round", cmd_start_round,
         "Step 2 — 提案ラウンドを開く。実装担当とレビュー担当を返す"),
        ("merge-proposals", cmd_merge_proposals,
         "Step 3 — 提案の語彙検証・重複排除・優先度付け・採否"),
        ("advance", cmd_advance, "Step 7 — 提案ラウンドの収束判定"),
        ("status", cmd_status, "現在の状態を人が読む形で出す"),
    ):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("id", type=int)
        sp.set_defaults(func=func)

    for name, func, help_ in (
        ("next-apply-round", cmd_next_apply_round,
         "Step 4 — 次の適用ラウンド（群）を開く。実装担当と対象の項目を返す"),
        ("verify-round", cmd_verify_round,
         "Step 5 — 適用ラウンドの結果をテストで検証する"),
        ("should-abandon", cmd_should_abandon,
         "Step 6 — この適用ラウンドの修正上限の到達判定"),
        ("merge-fix", cmd_merge_fix, "Step 6 — 修正結果の取り込み"),
    ):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("id", type=int)
        sp.add_argument("round", type=int)
        sp.set_defaults(func=func)

    # コミットを取り消しうる 2 つは、実行前に何が消えるかを確かめられるようにする。
    for name, func, help_ in (
        ("merge-apply", cmd_merge_apply,
         "Step 4 — 適用ラウンドの検証（差分予算 / トレーラー / 範囲 / 1 コミット）"),
        ("abandon-items", cmd_abandon_items,
         "Step 6 — テストが通らなかった適用ラウンドを取り消す"),
    ):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("id", type=int)
        sp.add_argument("round", type=int)
        sp.add_argument("--dry-run", action="store_true",
                        help="取り消すコミットを表示するだけで実行しない")
        sp.set_defaults(func=func)

    rp = sub.add_parser(
        "report", help="Step 8 — ラウンド表・項目表・見送り・指標")
    rp.add_argument("id", type=int)
    rp.add_argument("--metrics", action="store_true",
                    help="ランタイムとモデルの組で指標を集計する")
    rp.set_defaults(func=cmd_report)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
