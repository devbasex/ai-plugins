"""`ndf document-rebuild` のサブコマンド（#4337）。

    ndf document-rebuild run --base <ブランチ> --remote <名前> --issue <番号>... --kind <design|plan|manuscript>...
                             --state-dir <保存先> --out <ファイル>

- `run`: 起点（リモート追跡ブランチ、無ければ手元のベースブランチ）から `HEAD` までの差分と課題の番号から組み直す文書を
  選び、`ndf worker run` を 1 回打って作業場所で組み直させ、worker の結果を確かめる。対象が 0 件なら worker を起動しない

結果 JSON を `--out` と標準出力の最後の 1 行へ同じ形で書く。形が違えば両方とも `stopped`（invalid）に差し替える。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from ndf import cli, contracts, identity
from ndf.contracts import causes, result
from ndf.core.plan import files
from ndf.domains.docrebuild import drive, select_decide

TOOL = cli.tool_of(__name__)
RESULT_SCHEMA = drive.RESULT_SCHEMA
ENTRY = result.ENTRY


@contracts.cached_reader
def _own_validator() -> Draft202012Validator:
    """専用の結果スキーマから、共通の結果スキーマへの `$ref`（`allOf`）を外した検証器（共通の側は `result.errors` が確かめる）。"""
    schema = contracts.load_schema(RESULT_SCHEMA)
    return Draft202012Validator({k: v for k, v in schema.items() if k != "allOf"})


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=f"{identity.FIXED_WORDS['entrypoint']} {TOOL}", description="差分と課題の番号から文書を選んで組み直す")
    sub = p.add_subparsers(dest="action", required=True)
    r = sub.add_parser("run", help="組み直す文書を選び、worker を 1 回起動して結果を確かめる")
    r.add_argument("--base", required=True, help="ベースブランチの名前（スプリントの PR のマージ先）")
    r.add_argument("--remote", required=True, help="起点の参照を探すリモートの名前")
    r.add_argument("--issue", type=int, action="append", required=True, help="工程が扱う課題の番号（1 回以上）")
    r.add_argument("--kind", choices=select_decide.KINDS, action="append", required=True, help="選ぶ文書の種類（1 回以上）")
    r.add_argument("--state-dir", required=True, help="起動指示と worker の結果ファイルの保存先")
    r.add_argument("--out", required=True, help="結果 JSON を書くファイル")
    return p


def adopt(obj: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """結果を共通の結果スキーマと専用の結果スキーマで確かめ、書き出す結果と終了コードを決める。"""
    code = result.code_for(obj)
    common = result.errors(obj, code)
    own = sorted(contracts.broken_rule(e) for e in _own_validator().iter_errors(obj))
    if not common and not own and code is not None:
        return obj, code
    # 共通の検査だけで止まったときも、何を直すかが分かるように、値を含まない文を全件書く
    lines = [*result.safe_errors(obj, code), *own, *([result.NO_CODE] if code is None else [])]
    for line in dict.fromkeys(lines):
        print(f"結果の形の誤り: {line}", file=sys.stderr)
    count = len(common) + len(own) + (code is None)
    return result.stopped(ENTRY, "invalid", f"結果 JSON の形が違う（{count} 件）"), causes.exit_code("invalid")


def _run_args(a: argparse.Namespace) -> drive.RunArgs:
    cwd = Path.cwd()
    return drive.RunArgs(
        a.base,
        a.remote,
        tuple(dict.fromkeys(a.issue)),
        tuple(dict.fromkeys(a.kind)),
        (cwd / a.state_dir).resolve(),
        cwd,
    )


def main(argv: list[str]) -> int:
    try:
        a = _parser().parse_args(argv)
    except SystemExit as exc:
        return causes.exit_code("invalid") if exc.code else 0
    try:
        got = drive.drive(_run_args(a))
    except Exception as exc:  # noqa: BLE001 外部コマンド: worker の起動の想定外の失敗も結果 JSON にして --out と標準出力へ書く
        got = result.stopped(TOOL, "unknown", f"想定外の例外: {type(exc).__name__}: {exc}")
    out, code = adopt(got)
    try:
        files.write_json(Path(a.out), out)
    except OSError as exc:
        return result.emit(result.stopped(TOOL, "precondition", f"結果を {a.out} へ書けない: {exc.strerror or exc}"))
    return result.emit(out, code)
