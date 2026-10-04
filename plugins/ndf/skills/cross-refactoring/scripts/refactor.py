#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""cross-refactoring の状態管理 CLI。

`<work>/.cross_refactoring/cross-refactoring-rf<ID>-state.json` の初期化・読み書きと、
5 つの手順（提案 → 改修計画 → テスト追加 → 実装 → 検証/修正）の取り込みと判定を
1 つの CLI に集約する（#933）。

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
import container_reach  # noqa: E402
import deps  # noqa: E402

# `refactor_lib` の呼び名の表の読み取り（md）と報告の表（mdtable）が外部パッケージを使う。
# **`refactor_lib` の import より前に呼ぶ**（呼び名の表は取り込みの時点で読む）。
deps.require("md", "mdtable")

# 分割したモジュールは同じディレクトリの `refactor_lib/` にある。**自身の
# ディレクトリを探索先へ入れる。** `uv run --script` で起動したときの現在地は、
# スクリプトの位置と揃わない。
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

# **呼び名の表は `refactoring` が持つ**（#444）。読めないと兆候と手法の名前が決まらず、
# 重複排除が効かない。取り込みの時点で読むため、ここで捕まえて理由だけを出す
# （トレースバックを見せても、直す手がかりにならない）。
try:
    pass  # noqa: E402
except Exception as _exc:  # 取り込みそのものが失敗した
    print(f"ERROR: {_exc}", file=sys.stderr)
    sys.exit(4)

from refactor_lib.commands.assess import DEFAULT_MAX_LINES, cmd_assess  # noqa: E402
from refactor_lib.commands.converge import (  # noqa: E402
    cmd_merge_fix,
    cmd_verify,
)
from refactor_lib.commands.final_fix import cmd_merge_final_fix  # noqa: E402
from refactor_lib.commands.gate import cmd_final_gate  # noqa: E402
from refactor_lib.commands.implement import (  # noqa: E402
    cmd_merge_implement,
    cmd_merge_tests,
)
from refactor_lib.commands.measure import cmd_measure  # noqa: E402
from refactor_lib.commands.plan import cmd_merge_plan  # noqa: E402
from refactor_lib.commands.plan_comment import cmd_plan_comment  # noqa: E402
from refactor_lib.commands.propose import cmd_merge_proposals  # noqa: E402
from refactor_lib.commands.report import (  # noqa: E402
    cmd_finalize,
    cmd_report,
    cmd_status,
)
from refactor_lib.commands.phases import PHASE_NAMES, cmd_start_phase  # noqa: E402
from refactor_lib.commands.setup import cmd_init, runtime_list  # noqa: E402
from refactor_lib.measure import summary_extra  # noqa: E402

import run_metrics  # noqa: E402
import statefile  # noqa: E402


def _write_run_summary(path: pathlib.Path, state: dict) -> None:
    """状態を保存するたびに実行の要約を書き直す（#662 の決定 5）。"""
    run_metrics.after_save(path, state, "cross-refactoring", summary_extra)


# **読み込んだ時点で登録する。** 副コマンドはすべて `statefile.save` を通るため、
# 入口で 1 度登録すれば取り込み（`commands/`）へ呼び出しを足さずに済む。
statefile.register_after_save(_write_run_summary)
from refactor_lib.vocabulary import (  # noqa: E402
    DEFAULT_BUDGET_MINUTES,
    DEFAULT_SEVERITY_THRESHOLD,
    SEVERITY_ORDER,
)

# **状態ファイルに載る引数の既定は `None` にする**（#727 の決定 13）。既定値を引数に
# 持たせると、再開で「渡さなかった」と「既定値を渡した」を区別できない。新規の
# 初期化が `commands/setup.py` の `NEW_RUN_DEFAULTS` で置き換える。


# ---------------- main ----------------


def add_init_parser(sub: argparse._SubParsersAction) -> None:
    """`init` を登録する。"""
    init = sub.add_parser("init", help="Step 0 — ホスト確定 / 参加者の確定 / 作業ディレクトリ root / 状態初期化・再開")
    init.add_argument("pr", type=int)
    init.add_argument("--scope", nargs="+", required=True, help="対象範囲。提案が無制限に広がらないよう必須にしている")
    init.add_argument(
        "--host", choices=list(assignment.HOST_RUNTIMES), default=None, help="ホストの明示指定。未指定時は環境変数から推定する"
    )
    # **参加者は既定に足し引きして決める**（#727 の決定 4）。既定は codex / kiro と
    # ホストで、使う側を並べる形にしないのは、ホストが変わるたびに書き直さずに済むため。
    init.add_argument(
        "--exclude",
        action="append",
        type=runtime_list,
        default=None,
        help="参加者から外す者（ホストも外せる）。カンマ区切り・繰り返し可。再開で none を渡すと空へ戻す",
    )
    init.add_argument(
        "--include",
        action="append",
        type=runtime_list,
        default=None,
        help="参加者に足す者（例: agy）。カンマ区切り・繰り返し可。再開で none を渡すと空へ戻す",
    )
    init.add_argument(
        "--require-all",
        dest="require_all",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="確認を通らない者が 1 者でもいれば中断する。既定は外して続ける",
    )
    # **所要の上限を時間で渡す**（#933 の決定 4）。目安の上限で、実行中の CLI は止めない。
    # 1 以上の整数でなければ `init` が終了コード 4 で止める（argparse の 2 にしない）。
    init.add_argument(
        "--budget-minutes",
        default=None,
        metavar="N",
        help=f"想定最大時間（分）。この時間に収まる改修計画を 1 回だけ実行する (default: {DEFAULT_BUDGET_MINUTES})",
    )
    init.add_argument(
        "--implementer",
        default=None,
        choices=list(assignment.ALL_RUNTIMES),
        help="改修計画・テスト追加・実装・修正を通す 1 者。参加者の中から選ぶ。既定はホスト（参加者にいれば）→ 参加者の先頭",
    )
    # 廃止した引数（決定 5・決定 24）。**受け取って知らせ、値は使わない。** 次の版で外す。
    # 修正の回数とテスト 1 回の上限は、想定最大時間から逆算する（決定 24）。
    for name in ("--max-test-rounds", "--max-outer-rounds", "--max-items-per-round", "--max-fix-rounds", "--test-timeout"):
        init.add_argument(name, default=None, help=argparse.SUPPRESS)
    init.add_argument(
        "--ci-check",
        default=None,
        metavar="NAME",
        help="最終ゲートで手元のテストの代わりに見るチェックの名前。"
        "**指定すると手元のテストは実行しない**（排他）。"
        "指定が無ければ手元のテストで判定する",
    )
    init.add_argument(
        "--severity-threshold",
        default=None,
        choices=[s for s in SEVERITY_ORDER if s != "unknown"],
        help=f"この重要度未満は採用しない (default: {DEFAULT_SEVERITY_THRESHOLD})",
    )
    init.add_argument("--model", action="append", metavar="RUNTIME=MODEL", help="ランタイムごとのモデル指定。繰り返し指定できる")
    init.add_argument(
        "--sync-command",
        default=None,
        help="生成物を同期するコマンド。**push の直前**に進行側が実行し、"
        "差分があれば進行側のコミットとして積む。"
        "同期を実装担当にさせると範囲外の変更になるため分離している",
    )
    # **既定は Pull Request のコメント 1 件である**（#436 決定 6）。改修計画は
    # 実行の記録であって、リポジトリの知識ではない。ファイルにすると差分に混ざり、
    # URL がブランチの後片付けで切れる。
    init.add_argument(
        "--plan-file",
        default=None,
        help="改修計画をファイルへ書き出すパス（対象リポジトリからの"
        "相対）。**指定したときだけファイルになる。**"
        "既定は対象の Pull Request のコメント 1 件で、"
        "公開のたびに同じコメントを編集する。"
        "空文字を渡すと記録しない",
    )
    # **テストの走らせ方は宣言（`.ndf/project.json` の `test`）の戦略で決める**（#1334）。引数は宣言より先に効き、
    # `{paths}` を含めば範囲テストの雛形、含まなければそのまま走らせるコマンドとして受ける。文字列の中身は解析しない。
    init.add_argument(
        "--baseline-test",
        default=None,
        metavar="CMD",
        help="全体のテスト。{paths} を含めば範囲テストの雛形（全体は宣言の command、無ければテストは {paths} を . にしたもの、"
        "静的解析は {paths} を --scope で埋めたもの）、含まなければ全体テストのコマンドとしてそのまま走らせる（戦略は round-only）。"
        "省けば宣言の test を読む",
    )
    init.add_argument(
        "--test-kind",
        choices=("test", "lint"),
        default="test",
        help="--baseline-test と --round-test の雛形の種別（test = テスト / lint = 静的解析。整形の検査を含む）。既定は test",
    )
    init.add_argument(
        "--round-test",
        default=None,
        metavar="CMD",
        help="ラウンドのテスト。{paths} を含めば範囲テストの雛形、"
        "含まなければ項目ごとにそのまま走らせる（戦略は round-only）。宣言に test が無いときの逃げ道",
    )
    # **起動のされ方は引数で受け取る**（#436 決定 7）。環境変数や記録の読み取りは、
    # 起動元が違っても同じ値になりうる。呼ぶ側が明示すれば判定が 1 か所で済む。
    init.add_argument(
        "--workflow-step",
        action="store_true",
        default=None,
        help="`development-workflow` の 1 工程として起動したことを伝える。最終ゲートの `cross-review` を省き、全体のテストで判定する",
    )
    # **提案の前に指標を測る**（#1319）。言語ごとのツールの置き換えは `.ndf/code-metrics.json`。
    init.add_argument(
        "--code-metrics",
        dest="code_metrics",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="提案の前に対象範囲の指標（循環的複雑度・行数・重複）を測る。--no-code-metrics で測らない (default: 測る)",
    )
    init.add_argument("--worktree-root", default=None)
    init.set_defaults(func=cmd_init)


def add_id_commands(sub: argparse._SubParsersAction) -> None:
    """実行の番号 `id` だけを受け取る副コマンドを登録する。"""
    for name, func, help_ in (
        ("measure", cmd_measure, "提案の前に指標を 1 回だけ測り、指標のファイルを書く（失敗しても 0）"),
        ("merge-proposals", cmd_merge_proposals, "提案の統合・語彙としきい値・候補の切り出し（30 組 × 組の中 3 件）"),
        ("merge-plan", cmd_merge_plan, "改修計画の取り込み・等級と同じ変更か（Jev）・見積り・件数・締め切り"),
        ("merge-tests", cmd_merge_tests, "テストの追加の取り込み（test_failed / not_done）"),
        ("merge-implement", cmd_merge_implement, "実装の取り込み（1 項目 = 1 コミット / not_done）"),
        ("verify", cmd_verify, "項目ごとの範囲テストと危険フラグ。VERIFY=done|fix"),
        ("merge-fix", cmd_merge_fix, "修正の取り込み"),
        ("final-gate", cmd_final_gate, "最終ゲート。--ci-check があれば継続的統合、無ければ全体のテスト"),
        ("merge-final-fix", cmd_merge_final_fix, "最終ゲートの修正結果の取り込み"),
        ("status", cmd_status, "現在の状態を人が読む形で出す"),
    ):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("id", type=int)
        sp.set_defaults(func=func)

    sp = sub.add_parser("start-phase", help="手順の開始を記録し、上限（PHASE_TIMEOUT）を返す")
    sp.add_argument("id", type=int)
    sp.add_argument("phase", choices=list(PHASE_NAMES))
    sp.set_defaults(func=cmd_start_phase)

    sp = sub.add_parser(
        "plan-comment",
        help="リファクタリング計画のコメントを状態ファイルから書き直す。0 = 書いた・対象なし / 1 = 投稿に失敗 / 4 = 状態ファイルが無い・origin を取り込めない",
    )
    sp.add_argument("id", type=int, help="実行の ID（対象の Pull Request の番号）")
    sp.add_argument(
        "--scan-reverts",
        action="store_true",
        help="origin の head ブランチの `This reverts commit` を読み、プランの外で取り消した項目を取り消しにしてから書き直す",
    )
    sp.set_defaults(func=cmd_plan_comment)

    sp = sub.add_parser("finalize", help="最終ゲートが通った実行だけ配分の履歴へ 1 行追記する（失敗しても 0）")
    sp.add_argument("id", type=int)
    sp.add_argument("--review-status", default=None, help="単独起動のとき、cross-review の最終ステータス（approved だけが追記の条件）")
    sp.set_defaults(func=cmd_finalize)


def add_assess_parser(sub: argparse._SubParsersAction) -> None:
    """`assess` を登録する。"""
    ap = sub.add_parser(
        "assess", help="構造改善を飛ばしてよいかを差分から判定する。終了コード 0 = 通す / 3 = 飛ばしてよい / 2 = 判定できない"
    )
    ap.add_argument("--base", required=True, help="起点の ref。`<base>...HEAD` の差分を見る")
    ap.add_argument(
        "--max-lines",
        type=int,
        default=DEFAULT_MAX_LINES,
        help=f"本番コードの変更行（追加 + 削除）がこれ以下なら飛ばしてよい (default: {DEFAULT_MAX_LINES})",
    )
    ap.set_defaults(func=cmd_assess)


def add_report_parser(sub: argparse._SubParsersAction) -> None:
    """`report` を登録する。"""
    rp = sub.add_parser("report", help="完了報告 — 手順別の所要・項目・見送りの理由別・全体のテスト・Jev")
    rp.add_argument("id", type=int)
    rp.add_argument("--metrics", action="store_true", help="種類別の件数と所要（配分の履歴へ書く値）も出す")
    rp.set_defaults(func=cmd_report)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    add_init_parser(sub)
    add_id_commands(sub)
    add_assess_parser(sub)
    add_report_parser(sub)

    args = p.parse_args()
    try:
        args.func(args)
    except container_reach.Unreachable as e:
        # コンテナで走る suite が worktree を見ていない。走らせると結果はメインディレクトリのコードのもので、
        # 既存失敗にも通ったことにもしない（#1337）
        from refactor_lib import die

        die(f"コンテナで走るテストへ作業ディレクトリが届かないため、走らせずに止めます: {e.reason}")


if __name__ == "__main__":
    main()
