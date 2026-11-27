"""サブコマンド `ndf models`: 起動のモデルと effort を選ぶ（#2944）。

    ndf models pick --layer <層> --name <作業の名前> [--runtime claude] [--unit <単位>] [--retry <理由>] [--args]

`pick` は `adapters.model_choice.launch_choice` の値を返す。既定は、値・出所・割り付け ID と CLI に渡す引数を結果 JSON の
items に載せる。`--args` を付けると、CLI に渡す引数を 1 行に 1 つずつ標準出力へ出す（引用符もエスケープも付けない）。`--args` の
失敗は標準出力に何も出さず、理由を標準エラーに書いて停止理由の終了コード（`precondition` など）で終える。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ndf import cli
from ndf.adapters import model_choice
from ndf.contracts import causes, internal, result

TOOL = cli.tool_of(__name__)
# 1 行 1 引数で出すフラグ
ARGS_FLAG = "--args"


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=f"ndf {TOOL}", description="起動のモデルと effort を選ぶ")
    sub = p.add_subparsers(dest="command", required=True)
    pick = sub.add_parser("pick", help="層と作業の名前から、起動のモデルと effort を選ぶ")
    pick.add_argument("--layer", required=True, help="起動の層（worker・skill・judge・seat）")
    pick.add_argument("--name", required=True, help="作業の名前（worker の作業の名前・Skill の名前など）")
    pick.add_argument("--runtime", default="claude", help="ランタイム（claude か codex）")
    pick.add_argument("--unit", help="単位の ID（省けば割り付けない）")
    pick.add_argument("--retry", help="同じ失敗への 2 回目以降の起動の理由（省けば引き上げない）")
    pick.add_argument(ARGS_FLAG, action="store_true", help="CLI に渡す引数を 1 行に 1 つずつ出す（省けば結果 JSON）")
    return p


def _pick_command(a: argparse.Namespace) -> int:
    try:
        got = model_choice.launch_choice(a.layer, a.name, a.runtime, cwd=Path.cwd(), unit=a.unit, retry=a.retry)
    except internal.InternalError as exc:
        if not a.args:
            raise
        print(f"ndf {TOOL} pick: {exc}", file=sys.stderr)
        return causes.exit_code(exc.kind)
    args = got.cli_args()
    if a.args:
        sys.stdout.write("".join(f"{word}\n" for word in args))
        return 0
    item = {**got.row_fields(), "args": args}
    if got.stage is not None:
        item["stage"] = got.stage
    return result.emit(result.result(TOOL, result.OK, f"{a.layer} {a.name} の起動の値", items=[item]))


def main(argv: list[str]) -> int:
    try:
        a = _parser().parse_args(argv)
    except SystemExit as exc:
        if exc.code in (0, None) or ARGS_FLAG not in argv:
            raise
        return causes.exit_code("invalid")  # --args の形は結果 JSON を出さない。使い方は argparse が標準エラーへ書いた
    return _pick_command(a)
