#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""cross-review state.json 操作 CLI。

`<worktree>/.cross_review/cross-review-pr<PR>-state.json` の初期化 / 読み書きと、
ループ判定（round 開始 / 収束 / 振動 / PR ローテーション要否 / fix 結果マージ /
deferred nit レポート）を 1 つの CLI に集約する。

すべての出力は人間可読 + KEY=VALUE 形式（eval / read で取り回し可能）。
"""
# 副命令の本体は隣の `review_lib/` が持ち、ここは副命令の引数の解析と `main` だけを持つ（#1142 の C2）。
# docstring は `--help` の説明に出るため、ここに書く。
from __future__ import annotations

import argparse
import pathlib
import sys

# `review_lib` はこのスクリプトの隣にある。テストと drive.py は `spec_from_file_location` で読むため、
# スクリプトの置き場所は `sys.path` に自動では入らない。
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from review_lib import participants as participants_mod  # noqa: E402
from review_lib.commands import (  # noqa: E402
    collect_critiques, init, judge, loop, merge_fix, read_result, report, start_round, verify_findings)
import assignment  # noqa: E402  `review_lib` がライブラリの置き場所を `sys.path` に足した後に読む


def _runtime_or_none(value: str) -> str:
    """`--only` の型。4 つの名前か `none`（決定 15: 再開で指定を外す予約語）。"""
    if value == participants_mod.NONE_WORD or value in assignment.ALL_RUNTIMES:
        return value
    raise argparse.ArgumentTypeError(
        f"{'/'.join(assignment.ALL_RUNTIMES)} か {participants_mod.NONE_WORD} を指定してください: {value}")


def _runtime_list(value: str) -> list[str]:
    """`--exclude` / `--include` の型。カンマ区切りの 4 つの名前、または `none`。

    `none` は `["none"]` のまま返し、`_normalize_participant_args` が空の一覧へ直す。
    """
    names = [n.strip() for n in value.split(",") if n.strip()]
    for n in names:
        _runtime_or_none(n)
    if not names:
        raise argparse.ArgumentTypeError("名前を 1 つ以上指定してください")
    return names


def _seat_arg(value: str) -> str:
    """席の名前の型（`read-result` の担当）。形は `assignment.SEAT_PATTERN`。"""
    try:
        assignment.seat_runtime(value)
    except assignment.AssignmentError as e:
        raise argparse.ArgumentTypeError(str(e))
    return value


_Subparsers = argparse._SubParsersAction


def _add_init_parser(sub: _Subparsers) -> None:
    sp = sub.add_parser("init", help="Step 0 — state 初期化 or 再開")
    sp.add_argument("pr", type=int)
    sp.add_argument("--max-rounds", type=int, default=None)
    sp.add_argument("--rotate-after", type=int, default=None)
    sp.add_argument(
        "--only", type=_runtime_or_none, default=None,
        help="1 者だけで回す。席の埋め合わせを行わない。none で指定を外す")
    sp.add_argument(
        "--exclude", action="append", type=_runtime_list, default=None,
        help="母集合から外す者。カンマ区切り・繰り返し可。再開で `none` を渡すと空へ戻す")
    sp.add_argument(
        "--include", action="append", type=_runtime_list, default=None,
        help="母集合に足す者（ホストも足せる）。カンマ区切り・繰り返し可。`none` で空へ戻す")
    sp.add_argument(
        "--require-all", dest="require_all",
        action=argparse.BooleanOptionalAction, default=None,
        help="確認を通らない者が 1 者でもいれば失敗する（従来の関門）。既定は外して続ける")
    sp.add_argument(
        "--host", choices=list(assignment.HOST_RUNTIMES), default=None,
        help="この収束ループを起動している CLI。省略時は環境変数から推定する")
    sp.add_argument("--worktree", default=None)
    sp.add_argument(
        "--verify-command", action="append", default=None,
        help="実行検証で実行してよいコマンド。繰り返し指定できる。"
             "渡されなければ実行検証を行わない")
    sp.add_argument(
        "--verify-exit-code", action="append", type=int, default=None,
        help="再現とみなす終了コード（既定 1）。繰り返し指定できる")
    sp.add_argument(
        "--focus",
        default=None,
        help="追加レビュー観点。例: ドキュメントとコードの整合性を重点的に確認",
    )
    sp.add_argument(
        "--extra-instructions-file",
        default=None,
        help="追加レビュー観点を記載した UTF-8 テキストファイル",
    )
    sp.set_defaults(func=init.cmd_init)


def _add_start_round_parser(sub: _Subparsers) -> None:
    sp = sub.add_parser(
        "start-round",
        help="Step 1 — round 開始判定 (1=上限到達/5=後始末の未了/8=同期できない)",
    )
    sp.add_argument("pr", type=int)
    sp.set_defaults(func=start_round.cmd_start_round)


def _add_read_result_parser(sub: _Subparsers) -> None:
    sp = sub.add_parser("read-result", help="Step 2.4 — review result を state にマージ")
    sp.add_argument("pr", type=int)
    sp.add_argument("agent", type=_seat_arg)
    sp.add_argument("--file", default=None)
    sp.set_defaults(func=read_result.cmd_read_result)


def _add_unresolved_threads_parser(sub: _Subparsers) -> None:
    sp = sub.add_parser(
        "unresolved-threads",
        help="PR 上の未解決の指摘を数える (0=数えられた/1=取得できなかった)",
    )
    sp.add_argument("pr", type=int)
    sp.set_defaults(func=report.cmd_unresolved_threads)


def _add_flush_parser(sub: _Subparsers) -> None:
    sp = sub.add_parser("flush", help="待ち行列に積んだ投稿を流す (常に 0)")
    sp.add_argument("pr", type=int)
    sp.set_defaults(func=judge.cmd_flush)


def _add_judge_parser(sub: _Subparsers) -> None:
    sp = sub.add_parser(
        "judge",
        help="Step 3 — intent ベース pass 判定 "
             "(0=approved/2=continue/7=起動し直し/8=待ち行列に残あり)",
    )
    sp.add_argument("pr", type=int)
    sp.set_defaults(func=judge.cmd_judge)


def _add_check_oscillation_parser(sub: _Subparsers) -> None:
    sp = sub.add_parser(
        "check-oscillation",
        help="Step 4 — 同じ箇所を指す指摘の割合を計算 (2=続行/4=振動で中断)",
    )
    sp.add_argument("pr", type=int)
    sp.set_defaults(func=loop.cmd_check_oscillation)


def _add_verify_findings_parser(sub: _Subparsers) -> None:
    sp = sub.add_parser(
        "verify-findings",
        help="Step 2.5 前段 — 重複の統合（1 段目）と実行検証（#156）")
    sp.add_argument("pr", type=int)
    sp.set_defaults(func=verify_findings.cmd_verify_findings)


def _add_collect_critiques_parser(sub: _Subparsers) -> None:
    sp = sub.add_parser(
        "collect-critiques",
        help="Step 2.5 後段 — 反証の結果を指摘へ結び、申告の重複を束ねる（#156）")
    sp.add_argument("pr", type=int)
    sp.set_defaults(func=collect_critiques.cmd_collect_critiques)


def _add_merge_fix_parser(sub: _Subparsers) -> None:
    sp = sub.add_parser("merge-fix", help="Step 5 post — fix 戻り値マージ + CI 分類")
    sp.add_argument("pr", type=int)
    sp.add_argument("--file", default=None)
    sp.set_defaults(func=merge_fix.cmd_merge_fix)


def _add_should_rotate_parser(sub: _Subparsers) -> None:
    sp = sub.add_parser("should-rotate", help="Step 6 — rotate 要否 (0=rotate/2=keep)")
    sp.add_argument("pr", type=int)
    sp.set_defaults(func=loop.cmd_should_rotate)


def _add_set_current_pr_parser(sub: _Subparsers) -> None:
    sp = sub.add_parser("set-current-pr", help="rotation 後の current_pr 更新")
    sp.add_argument(
        "--head-branch",
        default=None,
        help="巻き直しで作られた新しい枝名（rotate-pr.sh の NEW_BRANCH）",
    )
    sp.add_argument("pr", type=int, help="state file の元 PR")
    sp.add_argument("new_pr", type=int)
    sp.set_defaults(func=loop.cmd_set_current_pr)


def _add_verify_sweep_parser(sub: _Subparsers) -> None:
    sp = sub.add_parser(
        "verify-sweep",
        help="Step 7.5 後段 — 最終スイープ後の未解決の指摘を検証 (0=残なし/6=残あり)",
    )
    sp.add_argument("pr", type=int)
    sp.add_argument("--file", default=None)
    sp.set_defaults(func=report.cmd_verify_sweep)


def _add_report_parser(sub: _Subparsers) -> None:
    sp = sub.add_parser("report", help="Step 8 — deferred nit + サマリ表示")
    sp.add_argument("pr", type=int)
    sp.set_defaults(func=report.cmd_report)


# 副コマンドの登録関数。**`--help` の一覧はこの順で出る**ため、並びを変えない。
_SUBCOMMAND_REGISTRARS = (
    _add_init_parser,
    _add_start_round_parser,
    _add_read_result_parser,
    _add_unresolved_threads_parser,
    _add_flush_parser,
    _add_judge_parser,
    _add_check_oscillation_parser,
    _add_verify_findings_parser,
    _add_collect_critiques_parser,
    _add_merge_fix_parser,
    _add_should_rotate_parser,
    _add_set_current_pr_parser,
    _add_verify_sweep_parser,
    _add_report_parser,
)


def build_parser() -> argparse.ArgumentParser:
    """副コマンドの引数を組み立てる。**テストが選択肢をチェックできるように分ける。**

    実機で `kiro` の結果が `invalid choice` で弾かれた。担当が 4 つの名前を取りうる
    以上、副コマンドの引数も同じ母集合を持たなければ、結果を残した担当が「結果なし」
    として扱われる。
    """
    # 副コマンドの説明は各登録関数の `help` だけが持つ。**モジュールの docstring へ写さない。**
    # 2 か所へ書くと片方だけが実装から離れる。振動の検知の基準は実装が 3 つの一致へ
    # 変わった後も、docstring 側が古い基準を出し続けていた（#329）。
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    for register in _SUBCOMMAND_REGISTRARS:
        register(sub)
    return p


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
