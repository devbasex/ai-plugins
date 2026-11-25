"""`ndf document-rebuild` のサブコマンド（#4337）。

    ndf document-rebuild run --base <ブランチ> --remote <名前> --issue <番号>... --kind <design|plan|manuscript>...
                             --state-dir <保存先> --out <ファイル>

- `run`: 起点（リモート追跡ブランチ、無ければ手元のベースブランチ）から `HEAD` までの差分と課題の番号から組み直す文書を
  選び、`ndf worker run` を 1 回打って作業場所で組み直させ、worker の結果を確かめる。対象が 0 件なら worker を起動しない

    ndf document-rebuild gate --gate <design|release> [--pr <番号>] [--material <ファイル>] [--reviewed <コミット>]
                              [--no-doc-fix] --state-dir <保存先> --out <ファイル>

- `gate`: 承認ゲートの 1 つ前に打つ（#4446）。決まった形の質問・承認資料・前に読ませた後で変わった文書の部分を読み手役に
  読ませ、質問を止める hook が見る記録へ残す。PR の題と本文は書き換えない

結果 JSON を `--out` と標準出力の最後の 1 行へ同じ形で書く。形が違えば両方とも `stopped`（invalid）に差し替える。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from jsonschema import Draft202012Validator

from ndf import cli, contracts, identity
from ndf.contracts import causes, result
from ndf.core import route
from ndf.core.plan import files
from ndf.adapters import github
from ndf.domains.docrebuild import drive, gate, select_decide
from ndf.domains.records import reading
from ndf.progress import invoke

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
    g = sub.add_parser("gate", help="承認ゲートの前に、質問・承認資料・変わった文書を読み手役に読ませて記録する")
    g.add_argument("--gate", required=True, choices=(gate.DESIGN, gate.RELEASE), help="承認ゲートの種類")
    g.add_argument("--pr", type=int, help="承認する PR の番号（省けば、設計は作業場所のブランチの PR、リリースは工程の入力の pr）")
    g.add_argument("--material", help="リリースの承認資料のファイル（ndf release-steps materials の出力。--gate release だけ）")
    g.add_argument("--reviewed", help="設計のレビューを済ませた再レビュー待ちのコミット")
    g.add_argument("--no-doc-fix", action="store_true", help="文書を直させず、残った文として承認資料に書かせる")
    g.add_argument("--state-dir", required=True, help="起動指示・承認資料の作業用のコピー・worker の結果ファイルの保存先")
    g.add_argument("--out", required=True, help="結果 JSON を書くファイル")
    return p


def adopt(obj: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """結果を共通の結果スキーマと専用の結果スキーマで確かめ、書き出す結果と終了コードを決める。"""
    code = result.code_for(obj)
    common = result.errors(obj, code)
    own = sorted(contracts.broken_rule(e) for e in _own_validator().iter_errors(obj))
    if not common and not own and code is not None:
        return obj, code
    for line in own:
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


def _gate_args(a: argparse.Namespace) -> gate.GateArgs:
    cwd = Path.cwd()
    if a.material is not None and a.gate != gate.RELEASE:
        raise gate.Precondition("--material は --gate release だけで受ける")
    raw = invoke.stage_inputs(os.environ)
    return gate.GateArgs(
        a.gate, a.pr, (cwd / a.material).resolve() if a.material else None, a.reviewed, a.no_doc_fix,
        (cwd / a.state_dir).resolve(), cwd, raw if raw is not None else {},
    )  # fmt: skip


def _github_of(repo: str) -> github.GitHub:
    return github.GitHub(repo, github.client())


def _ci_watch(number: int, cwd: Path) -> dict[str, Any]:
    """`ndf ci watch` を、設計のプランの CI のステップと同じ設定の値（必須のチェック・待ちの秒の既定）で打つ。"""
    project = invoke._project(cwd)
    argv = [
        str(identity.entrypoint(identity.load().root)),
        "ci",
        "watch",
        "--pr",
        str(number),
        f"--budget={route.params()['ci_budget'].default}",
    ]
    required = invoke._project_value("required", project)
    argv += [f"--required={r}" for r in (required if isinstance(required, list) else [])]
    done = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=False)
    got = result.last_line(done.stdout)
    return (
        got if got is not None else result.stopped(TOOL, "check_failed", f"ndf ci watch の結果 JSON が無い（終了コード {done.returncode}）")
    )


def _form(cwd: Path) -> Optional[str]:
    try:
        return reading.release_form(cwd)
    except reading.FormMissing as exc:
        raise gate.Precondition(str(exc)) from None


def _act(a: argparse.Namespace) -> dict[str, Any]:
    if a.action == "run":
        return drive.drive(_run_args(a))
    try:
        args = _gate_args(a)
    except (gate.Stop, json.JSONDecodeError) as exc:
        return result.stopped(TOOL, "precondition", str(exc))
    return gate.drive_gate(args, hub_of=_github_of, ci_watch=_ci_watch, form_of=_form)


def main(argv: list[str]) -> int:
    try:
        a = _parser().parse_args(argv)
    except SystemExit as exc:
        return causes.exit_code("invalid") if exc.code else 0
    try:
        got = _act(a)
    except Exception as exc:  # noqa: BLE001 外部コマンド: worker の起動の想定外の失敗も結果 JSON にして --out と標準出力へ書く
        got = result.stopped(TOOL, "unknown", f"想定外の例外: {type(exc).__name__}: {exc}")
    out, code = adopt(got)
    try:
        files.write_json(Path(a.out), out)
    except OSError as exc:
        return result.emit(result.stopped(TOOL, "precondition", f"結果を {a.out} へ書けない: {exc.strerror or exc}"))
    return result.emit(out, code)
