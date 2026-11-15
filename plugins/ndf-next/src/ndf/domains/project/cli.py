"""副命令 `ndf project`: プロジェクトの宣言（`.ndf/project.json`）の判定とスキーマ（#3569。旧い `project-decl.py` の移し先）。

    ndf project check [--force] [--root <ディレクトリ>]   # 宣言が新しいか
    ndf project schema [--out <ファイル>]                 # 宣言の JSON Schema を出す

`check` の終了コード: 0 = 新しい / 3 = 無い・古い（`--force` なら新しくても 3。種類 `not_applicable`）/ 2 = 壊れている
（種類 `invalid`）/ 1 = 判定できない（git の外。種類 `unknown`）。LLM も GitHub も呼ばず、ファイルを書かない。
結果は 1 行の結果 JSON（形は contracts/schemas/io/project.result.schema.json）。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ndf import cli, contracts
from ndf.contracts import result
from ndf.domains.project import check
from ndf.domains.relay import signals

TOOL = cli.tool_of(__name__)


def _check(args: argparse.Namespace) -> int:
    try:
        got = check.judge(Path(args.root).resolve(), args.force)
    except check.CheckError as exc:
        return result.emit(result.stopped(TOOL, "unknown", f"判定できない: {exc}"))
    items = [{"kind": got.kind, "reason": r} for r in got.reasons]
    if got.kind == check.FRESH:
        return result.emit(result.result(TOOL, result.OK, "宣言は新しい", items=items, metrics={"inputs": got.inputs}))
    if got.kind == check.BROKEN:
        return result.emit(result.stopped(TOOL, "invalid", "；".join(got.reasons), items=items))
    label = "無い" if got.kind == check.MISSING else "古い"
    return result.emit(result.stopped(TOOL, "not_applicable", "；".join(got.reasons), summary=f"宣言が{label}", items=items))


def _schema(args: argparse.Namespace) -> int:
    text = json.dumps(contracts.load_json(check.SCHEMA), ensure_ascii=False, indent=2) + "\n"
    if not args.out:
        print(text, end="")
        return result.emit(result.result(TOOL, result.OK, "宣言の JSON Schema を出した"))
    try:
        signals.replace_text(Path(args.out), text)
    except OSError as exc:
        return result.emit(result.stopped(TOOL, "unknown", f"--out を書けない: {exc}"))
    return result.emit(result.result(TOOL, result.OK, f"宣言の JSON Schema を書いた: {args.out}", items=[{"kind": "schema", "reason": args.out}]))


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=f"ndf {TOOL}", description=__doc__.splitlines()[0] if __doc__ else None)
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check", help="宣言が新しいかを判定する（0 = 新しい / 3 = 無い・古い / 2 = 壊れている / 1 = 判定できない）")
    c.add_argument("--force", action="store_true", help="新しくても古いと判定する（手動の再解析）")
    c.add_argument("--root", default=".", help="リポジトリの作業場所")
    c.set_defaults(handler=_check)
    s = sub.add_parser("schema", help="宣言の JSON Schema を出す")
    s.add_argument("--out", help="書く先。省けば標準出力")
    s.set_defaults(handler=_schema)
    return p


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    return args.handler(args)
