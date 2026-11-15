"""副命令 `ndf gws`: gws（Google Workspace の CLI）の確認と Google Slides のサムネイルの保存（#3547）。

    ndf gws check [--install]                                   # gws の有無と認証の状態を確かめる
    ndf gws thumbnails <presentation_id> <out_dir> [--size S]   # 全ページのサムネイルを p<番号>.png で保存する

結果は 1 行の結果 JSON で出す（形は contracts/schemas/io/gws.result.schema.json）。引数の誤りは argparse が終了コード 2 で
止め、副命令の表の側（`ndf.cli`）が stopped（invalid）の結果 JSON を出す。
"""

from __future__ import annotations

import argparse
from collections.abc import Callable

from ndf import cli
from ndf.contracts import result
from ndf.domains.gws import GwsStop, Outcome, check, thumbnails

TOOL = cli.tool_of(__name__)


def _check(args: argparse.Namespace) -> Outcome:
    return check.run(TOOL, args.install)


def _thumbnails(args: argparse.Namespace) -> Outcome:
    return thumbnails.run(TOOL, args.presentation_id, args.out_dir, args.size)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=f"ndf {TOOL}", description="gws の確認と Google Slides のサムネイルの保存")
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check", help="gws の有無と認証の状態を確かめる")
    c.add_argument("--install", action="store_true", help="導入の同意の印。gws が無ければ npm で入れる")
    c.set_defaults(handler=_check)
    t = sub.add_parser("thumbnails", help="Google Slides の全ページのサムネイルを p<番号>.png で保存する")
    t.add_argument("presentation_id", help="プレゼンテーションの ID")
    t.add_argument("out_dir", help="出力のディレクトリ（無ければ作る）")
    t.add_argument("--size", default=thumbnails.DEFAULT_SIZE, choices=thumbnails.SIZES, help="サムネイルの大きさ")
    t.set_defaults(handler=_thumbnails)
    return p


def _emit_stop(stop: GwsStop) -> int:
    """止まる理由を stopped の結果 JSON にして出し、種類の終了コードを返す。"""
    return result.emit(result.stopped(TOOL, stop.kind, stop.detail, summary=stop.summary, items=stop.items, metrics=stop.metrics))


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    handler: Callable[[argparse.Namespace], Outcome] = args.handler
    try:
        out, code = handler(args)
    except GwsStop as stop:
        return _emit_stop(stop)
    return result.emit(out, code)
