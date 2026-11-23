"""サブコマンド `contracts`: コントラクトの表の検査と、シェルから表を引く口と、結果 JSON の出力。

ndf contracts check                          コントラクトの表をすべて確かめ、結果 JSON を出す
ndf contracts fact-ids <表> [<列>=<値>...]    条件に合う行の id を改行区切りで出す
ndf contracts fact-value <表> <id> <列>       1 つの値を出す（配列は 1 行に 1 要素）
ndf contracts emit --tool T --status S --summary X [--cause KIND DETAIL] [--next JSON] [--route ANSWER JSON] ...
                                             結果 JSON を確かめて 1 行で出す（シェルと worker が使う）
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from ndf import cli, contracts, identity
from ndf.contracts import causes, facts, limits, result, version, worker_paths

# 結果 JSON の tool の名前はサブコマンドの名前（SSOT は `cli.COMMANDS`）
TOOL = cli.tool_of(__name__)


def _collect(errs: list[str], fn: Callable[[], Any]) -> Any:
    """`fn` を呼んで返り値を返す。`ContractError` は `errs` へ足して None を返す。"""
    try:
        return fn()
    except contracts.ContractError as exc:
        errs.append(str(exc))
        return None


def _collect_errors(errs: list[str], fn: Callable[[], list[str]]) -> None:
    """誤りの一覧を返す `fn` を `_collect` で呼び、返った誤りを `errs` へ足す。"""
    found = _collect(errs, fn)
    if found is not None:
        errs += found


def check_all() -> list[str]:
    """コントラクトの表（事実の表・停止理由の表・上限の設定・スキーマ・コントラクトのバージョン）の誤りを並べる。空なら通る。"""
    errs: list[str] = []
    for name in ("result", "causes", "limits", "release-verification", "route-chosen", "version", "project-workflow"):
        _collect(errs, lambda name=name: contracts.schema_errors({}, contracts.schema_path(name)))
    policies = _collect(errs, limits.table)
    policy_names = set(policies) if policies is not None else None
    for name in facts.names():
        _collect_errors(errs, lambda name=name: facts.ref_errors(name, policy_names))
    if _collect(errs, causes.table) is not None:
        _collect_errors(errs, version.errors)
    return errs


def _cmd_check(_: argparse.Namespace) -> int:
    errs = check_all()
    if errs:
        detail = f"コントラクトの表に誤りが {len(errs)} 件ある"
        items = [{"kind": "violation", "reason": e} for e in errs]
        # 表で確かめて出せなければ（停止理由の表・結果のスキーマ・終了コードの表のどれが壊れていても）、
        # 確かめずに出す。出力の前に投げるので、標準出力へ 2 行出ることは無い
        try:
            return result.emit(result.stopped(TOOL, "invalid", detail, items=items))
        except contracts.ContractError:
            return result.emit_unchecked(TOOL, "invalid", detail, items)
    return result.emit(result.result(TOOL, result.OK, "コントラクトの表はすべて形に合う", metrics={"fact_tables": len(facts.names())}))


def _print_value(value: Any) -> None:
    for v in value if isinstance(value, list) else [value]:
        print(json.dumps(v) if isinstance(v, bool) else v)


def _cmd_fact_ids(a: argparse.Namespace) -> int:
    for i in facts.ids(a.table, **facts.parse_where(a.where)):
        print(i)
    return 0


def _cmd_fact_value(a: argparse.Namespace) -> int:
    _print_value(facts.value(a.table, a.id, a.column))
    return 0


def _json_arg(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"JSON として読めない: {exc}") from None


def _json_object_arg(text: str) -> dict[str, Any]:
    value = _json_arg(text)
    if not isinstance(value, dict):
        raise argparse.ArgumentTypeError(f"JSON のオブジェクトでない: {type(value).__name__}")
    return value


def _route_value(text: str) -> dict[str, Any] | None:
    """`--route` の CMD。- なら何も打たない（null）。JSON のオブジェクトでなければ `ValueError`。"""
    if text == "-":
        return None
    try:
        return _json_object_arg(text)
    except argparse.ArgumentTypeError as exc:
        raise ValueError(f"--route の CMD: {exc}") from None


def _result_from_args(a: argparse.Namespace) -> dict[str, Any]:
    """`emit` の引数から結果を組む（確かめない）。"""
    cause = causes.build(a.cause[0], a.cause[1], instance=a.cause_instance, retry_after=a.retry_after) if a.cause else None
    return result.result(
        a.tool,
        a.status,
        a.summary,
        items=a.item,
        metrics=a.metrics,
        cause=cause,
        routes={k: _route_value(v) for k, v in a.route},
        gaps=[{"kind": k, "name": n, "reason": r} for k, n, r in a.gap],
        next=a.next,
        presentation_path=a.presentation_path,
        outputs=a.outputs,
        run_id=a.run_id,
    )


def _expected_result_dir(directory: Path) -> Optional[tuple[int, int]]:
    """`ndf worker run` が渡した結果の保存先（`identity.WORKER_RESULT_DIR_ENV`）が `directory` なら、その `(st_dev, st_ino)`。

    worker の外か、別の場所へ書くとき・値の形が違うときは None（突き合わせない）。
    """
    raw = os.environ.get(identity.WORKER_RESULT_DIR_ENV, "")
    dev, _, rest = raw.partition(":")
    ino, _, path = rest.partition(":")
    if not (dev.isdigit() and ino.isdigit() and path) or Path(path) != directory:
        return None
    return int(dev), int(ino)


def _cmd_emit(a: argparse.Namespace) -> int:
    obj = _result_from_args(a)
    if a.out is None:
        return result.emit(obj, a.code)
    # 確かめと整形は result.emit に 1 回だけ任せ、出せた行をファイルへ書いてから標準出力へ出す
    buffer = io.StringIO()
    code = result.emit(obj, a.code, stream=buffer)
    line = buffer.getvalue()
    if not line:
        return code
    out = Path(a.out).absolute()
    # リンクをたどらずに新しい inode として書く（worker の起動後に結果パスをリンクへ差し替えられても、指示先の外を書き換えない）。
    # `..` が打ち消す要素は open_workdir が確かめる
    try:
        with worker_paths.open_workdir(out.parent) as wd:
            expected = _expected_result_dir(wd.path)
            if expected is not None:
                worker_paths.check_dir_id(wd, expected)
            worker_paths.write_file(wd, out.name, line)
    except worker_paths.EntryUnsafe as exc:
        return result.emit(result.stopped(TOOL, "invalid", f"--out {out} に書けない: {exc}"))
    sys.stdout.write(line)
    return code


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="ndf contracts", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="コントラクトの表をすべて確かめる").set_defaults(run=_cmd_check)
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
    p.add_argument("--status", required=True, choices=result.STATUSES)
    p.add_argument("--summary", required=True)
    p.add_argument("--cause", nargs=2, metavar=("KIND", "DETAIL"))
    p.add_argument("--cause-instance")
    p.add_argument("--retry-after", type=float)
    p.add_argument(
        "--route",
        nargs=2,
        action="append",
        default=[],
        metavar=("ANSWER", "CMD"),
        help='CMD は {"argv": ["ndf", ...]} の JSON。- なら何も打たない',
    )
    p.add_argument("--gap", nargs=3, action="append", default=[], metavar=("KIND", "NAME", "REASON"))
    p.add_argument("--item", type=_json_arg, action="append", default=[], help="items の 1 要素（JSON のオブジェクト）")
    p.add_argument("--metrics", type=_json_object_arg, help="metrics（JSON のオブジェクト）")
    p.add_argument("--next", type=_json_object_arg, help='次に打つコマンド（{"argv": ["ndf", ...], "ask": [...]} の JSON）')
    p.add_argument("--outputs", type=_json_object_arg, help="工程どうしで渡す値（JSON のオブジェクト）")
    p.add_argument("--run-id")
    p.add_argument("--presentation-path")
    p.add_argument("--code", type=int, help="終了コード（省けば status と cause.kind から決める）")
    p.add_argument("--out", help="結果を 1 行で書くファイル（形が合うときだけ書く）")
    p.set_defaults(run=_cmd_emit)
    return ap


def main(argv: list[str]) -> int:
    a = parser().parse_args(argv)
    try:
        return a.run(a)
    except (contracts.ContractError, ValueError, OSError) as exc:
        print(f"{TOOL}: {exc}", file=sys.stderr)
        return causes.exit_code("invalid")
