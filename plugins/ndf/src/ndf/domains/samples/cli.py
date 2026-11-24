"""サブコマンド `ndf samples`: テストの見本を本物の公開の記述と突き合わせる（#3687）。NDF 自身の開発のサブコマンドである。

    ndf samples verify [--peer <相手>]

結果は 1 行の結果 JSON（形は contracts/schemas/io/samples.result.schema.json）。取った記述の本文・見本の値・見本の側の
辞書のキーの名前は出さない。

終了コード: 0 = 合う / 1 = 食い違いがある（種類 `samples_mismatch`。取れない見本があってもこちら）/ 75 = 食い違いは無いが
取れない見本がある（`infra_wait`）/ 3 = 要求の数が上限に収まらない（`precondition`）/ 2 = 記述が OpenAPI 3.1 でない
（`config_broken`）。
"""

from __future__ import annotations

import argparse

from ndf import cli
from ndf.contracts import result
from ndf.domains.samples import verify

TOOL = cli.tool_of(__name__)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=f"ndf {TOOL}", description="テストの見本を本物の公開の記述と突き合わせる")
    sub = p.add_subparsers(dest="action", required=True)
    ver = sub.add_parser("verify", help="見本の定義（contracts/live-samples/）を、相手が公開する記述と突き合わせる")
    ver.add_argument("--peer", help="この相手の見本だけを突き合わせる")
    return p


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    try:
        got = verify.verify(args.peer)
    except verify.VerifyError as exc:
        return result.emit(result.stopped(TOOL, exc.kind, str(exc)))
    items = [o._asdict() for o in got.outcomes]
    metrics = {"samples": got.samples, "calls": got.calls}
    mismatched = {o.sample for o in got.outcomes if o.kind is verify.Kind.MISMATCH}
    missed = {o.sample for o in got.outcomes if o.kind is verify.Kind.UNREACHABLE}
    if mismatched:
        note = f"（ほかに本物を取れない見本が {len(missed)} 件）" if missed else ""
        detail = f"本物の記述と食い違う見本が {len(mismatched)} 件ある{note}"
        return result.emit(result.stopped(TOOL, "samples_mismatch", detail, items=items, metrics=metrics))
    if missed:
        detail = f"本物の記述を取れない見本が {len(missed)} 件ある"
        return result.emit(result.stopped(TOOL, "infra_wait", detail, items=items, metrics=metrics))
    return result.emit(result.result(TOOL, result.OK, f"見本 {got.samples} 件が本物の記述と合う", items=items, metrics=metrics))
