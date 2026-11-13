"""副命令 `contracts`: 契約の表の検査と、シェルから表を引く口と、結果 JSON の出力。

ndf contracts check                          契約の表をすべて確かめ、結果 JSON を出す
ndf contracts fact-ids <表> [<列>=<値>...]    条件に合う行の id を改行区切りで出す
ndf contracts fact-value <表> <id> <列>       1 つの値を出す（配列は 1 行に 1 要素）
ndf contracts emit --tool T --status S --summary X [--cause KIND DETAIL] ...
                                             結果 JSON を確かめて 1 行で出す（シェルと worker が使う）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ndf import contracts
from ndf.contracts import causes, facts, limits, result

TOOL = "contracts"


def check_all() -> list[str]:
    """契約の表（事実の表・止まった理由の表・上限の宣言・スキーマ）の誤りを並べる。空なら通る。"""
    errs: list[str] = []
    for name in ("result", "causes", "limits", "release-verification"):
        try:
            contracts.schema_errors({}, contracts.schema_path(name))
        except contracts.ContractError as exc:
            errs.append(str(exc))
    try:
        policy_names = set(limits.table())
    except contracts.ContractError as exc:
        errs.append(str(exc))
        policy_names = set()
    for name in facts.names():
        try:
            errs += facts.ref_errors(name, policy_names)
        except contracts.ContractError as exc:
            errs.append(str(exc))
    try:
        causes.table()
    except contracts.ContractError as exc:
        errs.append(str(exc))
    return errs


def _cmd_check(_: argparse.Namespace) -> int:
    errs = check_all()
    if errs:
        detail = f"契約の表に誤りが {len(errs)} 件ある"
        items = [{"kind": "violation", "reason": e} for e in errs]
        return result.emit(result.stopped(TOOL, "invalid", detail, items=items))
    return result.emit(result.result(TOOL, "ok", "契約の表はすべて形に合う", metrics={"fact_tables": len(facts.names())}))


def _print_value(value: Any) -> None:
    for v in value if isinstance(value, list) else [value]:
        print(json.dumps(v) if isinstance(v, bool) else v)


def _cmd_fact_ids(a: argparse.Namespace) -> int:
    for i in facts.ids(a.table, **facts.parse_where(a.where)):
        print(i)
    return 0


def _cmd_fact_value(a: argparse.Namespace) -> int:
    row = facts.row(a.table, a.id)
    if a.column != "id" and a.column not in facts.table(a.table).columns:
        raise facts.FactsError(f"事実の表 {a.table} に列 {a.column} が無い")
    if a.column not in row:
        raise facts.FactsError(f"事実の表 {a.table} の行 {a.id} は列 {a.column} を持たない")
    _print_value(row[a.column])
    return 0


def _json_arg(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"JSON として読めない: {exc}") from None


def _cmd_emit(a: argparse.Namespace) -> int:
    cause = None
    if a.cause:
        cause = {"kind": a.cause[0], "detail": a.cause[1]}
        if a.cause_instance is not None:
            cause["instance"] = a.cause_instance
        if a.retry_after is not None:
            cause["retry_after"] = a.retry_after
    obj = result.result(
        a.tool,
        a.status,
        a.summary,
        items=a.item,
        metrics=a.metrics,
        cause=cause,
        routes={k: (None if v == "-" else v) for k, v in a.route} or None,
        gaps=[{"kind": k, "name": n, "reason": r} for k, n, r in a.gap],
        next=a.next,
        presentation_path=a.presentation_path,
    )
    code = a.code if a.code is not None else result.code_for(obj)
    if a.out is not None and code is not None and not result.errors(obj, code):
        Path(a.out).write_text(json.dumps(obj, ensure_ascii=False) + "\n", encoding="utf-8")
    return result.emit(obj, code)


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="ndf contracts", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="契約の表をすべて確かめる").set_defaults(run=_cmd_check)
    p = sub.add_parser("fact-ids", help="条件に合う行の id")
    p.add_argument("table")
    p.add_argument("where", nargs="*", metavar="列=値")
    p.set_defaults(run=_cmd_fact_ids)
    p = sub.add_parser("fact-value", help="1 つの値")
    p.add_argument("table")
    p.add_argument("id")
    p.add_argument("column")
    p.set_defaults(run=_cmd_fact_value)
    p = sub.add_parser("emit", help="結果 JSON を確かめて出す")
    p.add_argument("--tool", required=True)
    p.add_argument("--status", required=True, choices=("ok", "gate", "stopped"))
    p.add_argument("--summary", required=True)
    p.add_argument("--cause", nargs=2, metavar=("KIND", "DETAIL"))
    p.add_argument("--cause-instance")
    p.add_argument("--retry-after", type=float)
    p.add_argument("--route", nargs=2, action="append", default=[], metavar=("ANSWER", "CMD"), help="CMD が - なら何も打たない")
    p.add_argument("--gap", nargs=3, action="append", default=[], metavar=("KIND", "NAME", "REASON"))
    p.add_argument("--item", type=_json_arg, action="append", default=[], help="items の 1 要素（JSON のオブジェクト）")
    p.add_argument("--metrics", type=_json_arg, help="metrics（JSON のオブジェクト）")
    p.add_argument("--next")
    p.add_argument("--presentation-path")
    p.add_argument("--code", type=int, help="終了コード（省けば status と cause.kind から決める）")
    p.add_argument("--out", help="結果を 1 行で書くファイル（形が合うときだけ書く）")
    p.set_defaults(run=_cmd_emit)
    return ap


def main(argv: list[str]) -> int:
    a = parser().parse_args(argv)
    try:
        return a.run(a)
    except (contracts.ContractError, ValueError) as exc:
        print(f"{TOOL}: {exc}", file=sys.stderr)
        return causes.exit_code("invalid")
