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
import pathlib
import sys

# 共通層はプラグインルート直下にある。**`.resolve()` を通す。** Kiro CLI は
# `.kiro/skills/<名前>` を symlink にするため、解かずに `parents[]` を数えると
# `.kiro` で止まってプラグインルートへ届かない。
sys.path.insert(
    0,
    str(pathlib.Path(__file__).resolve().parents[3] / "scripts" / "lib"),
)

import assignment  # noqa: E402

# 分割したモジュールは同じディレクトリの `refactor_lib/` にある。**自身の
# ディレクトリを探索先へ入れる。** `uv run --script` で起動したときの現在地は、
# スクリプトの位置と揃わない。
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

# **呼び名の表は `refactoring` が持つ**（#444）。読めないと兆候と手法の名前が決まらず、
# 重複排除が効かない。取り込みの時点で読むため、ここで捕まえて理由だけを出す
# （トレースバックを見せても、直す手がかりにならない）。
try:
    from refactor_lib.vocabulary import VocabularyUnavailable  # noqa: E402
except Exception as _exc:                        # 取り込みそのものが失敗した
    print(f"ERROR: {_exc}", file=sys.stderr)
    sys.exit(4)

from refactor_lib.commands.apply import (  # noqa: E402
    cmd_merge_apply,
    cmd_merge_proposals,
    cmd_next_apply_round,
)
from refactor_lib.commands.converge import (  # noqa: E402
    cmd_abandon_items,
    cmd_merge_test_judgements,
    cmd_merge_fix,
    cmd_should_abandon,
    cmd_verify_round,
)
from refactor_lib.commands.gate import cmd_final_gate, cmd_merge_final_fix  # noqa: E402
from refactor_lib.commands.report import (  # noqa: E402
    cmd_advance,
    cmd_report,
    cmd_status,
)
from refactor_lib.commands.setup import cmd_init, cmd_start_round  # noqa: E402
from refactor_lib.vocabulary import (  # noqa: E402
    DEFAULT_MAX_TEST_ROUNDS,
    DEFAULT_SEVERITY_THRESHOLD,
    DEFAULT_TEST_TIMEOUT,
    SEVERITY_ORDER,
)


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
    # **切るのは提案の回数であって、適用できる件数ではない**（#436 決定 8）。
    # 適用ラウンドを分けたことで、1 回の提案で通せる件数は上限に縛られなくなった。
    # 取り消した項目は除外されるため、同じ提案が積み上がって回数を食うこともない。
    # **輪番の 1 周を根拠にしない。** 適用の担当は適用ラウンドごとに進むので、
    # 1 つの提案ラウンドが複数の群を持てば輪番は 1 周しうる。
    init.add_argument("--max-outer-rounds", type=int, default=3,
                      help="構造改善の提案ラウンドの上限")
    # テスト整備は母集合が増えない（対象のコードを変えないため、テストが薄い経路の
    # 集合は最初から確定している）。2 回目に出るのは 1 回目の挙げ漏らしだけである。
    init.add_argument("--max-test-rounds", type=int,
                      default=DEFAULT_MAX_TEST_ROUNDS,
                      help="テスト整備ラウンドの上限。到達したら採用が残っていても "
                           "構造改善の提案ラウンドへ進む "
                           f"(default: {DEFAULT_MAX_TEST_ROUNDS})")
    init.add_argument("--max-fix-rounds", type=int, default=3,
                      help="1 つの適用ラウンドあたりの修正ラウンドの上限")
    init.add_argument("--max-items-per-round", type=int, default=5,
                      help="1 つの提案ラウンド／テスト整備ラウンドの採用上限")
    init.add_argument("--ci-check", default=None, metavar="NAME",
                      help="最終ゲートで手元のテストの代わりに見る検査の名前。"
                           "**指定すると手元のテストは実行しない**（排他）。"
                           "指定が無ければ手元のテストで判定する")
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
    # **既定は Pull Request のコメント 1 件である**（#436 決定 6）。改修計画は
    # 実行の記録であって、リポジトリの知識ではない。ファイルにすると差分に混ざり、
    # URL がブランチの後片付けで切れる。
    init.add_argument("--plan-file", default=None,
                      help="改修計画をファイルへ書き出すパス（対象リポジトリからの"
                           "相対）。**指定したときだけファイルになる。**"
                           "既定は対象の Pull Request のコメント 1 件で、"
                           "ラウンドが進むたびに同じコメントを編集する。"
                           "空文字を渡すと記録しない")
    init.add_argument("--baseline-test", required=True,
                      help="着手前と各コミットで実行するテストコマンド。"
                           "振る舞い不変を示す手段が無い書き換えは構造改善ではないため必須")
    # **起動のされ方は引数で受け取る**（#436 決定 7）。環境変数や控えの読み取りは、
    # 起動元が違っても同じ値になりうる。呼ぶ側が明示すれば判定が 1 か所で済む。
    init.add_argument("--workflow-step", action="store_true",
                      help="`development-workflow` の 1 工程として起動したことを"
                           "伝える。Step 7 の `cross-review` を省き、"
                           "全体のテストで判定する")
    init.add_argument("--worktree-root", default=None)
    init.set_defaults(func=cmd_init)

    for name, func, help_ in (
        ("start-round", cmd_start_round,
         "Step 2 — 提案ラウンドを開く。実装担当とレビュー担当を返す"),
        ("merge-proposals", cmd_merge_proposals,
         "Step 3 — 提案の語彙検証・重複排除・優先度付け・採否"),
        ("advance", cmd_advance, "ラウンドの収束判定と、ラウンドの種類の切り替え"),
        ("final-gate", cmd_final_gate,
         "Step 7 — 最終ゲート。起動のされ方で cross-review と全体のテストが変わる"),
        ("merge-final-fix", cmd_merge_final_fix,
         "Step 7 — 最終ゲートの修正結果の取り込み。**適用ラウンドの `merge-fix` "
         "とは別である**（起点も担当も改善項目も持たない）"),
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
        ("merge-test-judgements", cmd_merge_test_judgements,
         "Step 5 — テストの差分の判定（段 2）の答えを取り込む"),
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
