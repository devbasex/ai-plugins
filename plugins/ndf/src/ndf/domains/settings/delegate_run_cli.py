"""サブコマンド `ndf delegate-run`: 手順書の外部 AI への委譲を、安全装置の引数を入れて起動する（#1959）。

    ndf delegate-run --runtime codex|kiro|agy [--use delegate|review] -- <CLI のコマンド>

CLI のコマンドは、そのランタイムの実行ファイルと決まった語（ランタイムの表の `delegate_prefix`。codex は `exec`）で始め、
そのすぐ後に NDF の引数を入れる位置 `{args}` を単独の引数として 1 つだけ持つ。`{args}` には、codex なら選んだモデルと
effort の引数（`model_choice.launch_choice` の層 `seat`・作業 `delegate`。#2944）、安全装置の引数（`launch_settings.safety_args`）、
作業ディレクトリを今のディレクトリにする引数（codex は `-C <今のディレクトリ>`）をこの順に入れる。コマンドが `--model` でモデルを、
`-c model_reasoning_effort=<値>` で effort を明示すれば、その値を起動の引数として値の選択に渡し（下位の環境変数を読まない）、その側の引数は足さない。kiro と agy は値を選ばない
（モデルは手順書が `--model` で書く）。codex は起動の設定（`.ndf/launch.json`）を 1 回だけ読み、値の選択と安全装置の判定に渡す。外す引数なら起動の直前に承認の対象を読み直してから、子の環境に
`NDF_AI_CHILD=1` を足してこのプロセスを CLI に置き換える（`exec`）。標準入力・標準出力・終了コードは CLI のものになる。

コマンドの始まりが違う・安全装置か作業ディレクトリの引数を自分で持つ・値の選択肢の誤り・起動できない状況・読み直しの食い違いは、
CLI を起動せず、理由を標準エラーだけに書いて停止理由 `precondition` の終了コードで終える。結果 JSON は出さない
（標準出力は CLI に渡すため。`cli.NO_RESULT_COMMANDS`）。安全装置の引数だけを返すコマンドは作らない（取得の後の取り消しを
確かめられないため。設計書の「安全装置を外す指定」）。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Optional

from ndf import cli
from ndf.adapters import agents, launch_settings as ls, model_choice
from ndf.contracts import causes, facts, internal

TOOL = cli.tool_of(__name__)
RUNTIMES = ls.RUNTIMES
# NDF の引数を入れる位置
PLACE = "{args}"
# 安全装置の引数の名前を集める列（表 runtimes。別名の列を足す）
SAFETY_COLUMNS = (*ls.SANDBOXED.values(), ls.UNSANDBOXED, "safety_aliases")
# 作業ディレクトリを指す引数の名前の列（先頭の名前で NDF が今のディレクトリを入れる）
WORKDIR_COLUMN = "workdir_options"
# 実行ファイルのすぐ後に置く語の列（そのすぐ後が `{args}` の位置）
PREFIX_COLUMN = "delegate_prefix"
# 引数の終わり（これより後の語は CLI のオプションとして読まれない）
OPTIONS_END = "--"
# モデルと effort を選ぶランタイムと、値を引く層と作業の名前（表 launch-experiments の工程 delegate）
CHOOSES = ("codex",)
CHOICE_LAUNCH = ("seat", "delegate")


def _fail(detail: str) -> internal.InternalError:
    return internal.InternalError("precondition", detail)


def _delegates() -> list[str]:
    """委譲に使えるランタイム（安全装置の列を持つ行）。"""
    return [r["id"] for r in facts.rows(RUNTIMES) if any(c in r for c in SAFETY_COLUMNS)]


def _option_names(row: dict) -> set[str]:
    """行が持つ安全装置の引数の名前（`--name=value` は `--name`）。"""
    return {w.partition("=")[0] for c in SAFETY_COLUMNS if c in row for w in row[c] if w.startswith("-")}


def _options(command: Sequence[str]) -> list[str]:
    """CLI のオプションとして読まれる語（`--` より前）。"""
    words = list(command)
    return words[: words.index(OPTIONS_END)] if OPTIONS_END in words else words


def _names(word: str, names: set[str]) -> bool:
    """語 `word` が名前 `names` のどれかの引数か（`--name`・`--name=値`・短い名前に値を続けた `-s値`）。"""
    head = word.partition("=")[0]
    return head in names or any(len(n) == 2 and n[1] != "-" and word.startswith(n) for n in names)


def _prefix(runtime: str, row: dict) -> list[str]:
    """コマンドの先頭に置く語（実行ファイルと、ランタイムの表の `delegate_prefix`）。"""
    return [agents.executable(runtime), *(row[PREFIX_COLUMN] if PREFIX_COLUMN in row else [])]


def build_command(runtime: str, command: Sequence[str], args: Sequence[str]) -> list[str]:
    """コマンドが実行ファイルと `delegate_prefix` の語で始まってそのすぐ後に `{args}` を持ち、ほかに `{args}` を持たず、安全装置の
    引数と作業ディレクトリの引数を自分で持たないことを確かめ、`{args}` を `args` に広げる。満たさなければ `precondition`。

    `{args}` の位置を NDF が決めるのは、安全装置の引数が CLI のオプションとして読まれることを保証するため（別のオプションの値や
    プロンプトの位置に置かれると、安全装置が効かないまま起動する）。"""
    row = facts.row(RUNTIMES, runtime)
    prefix = _prefix(runtime, row)
    words = list(command)
    at = len(prefix)
    head = [Path(words[0]).name, *words[1:at]] if words else []
    if head != prefix or words[at : at + 1] != [PLACE]:
        raise _fail(f"コマンドは {' '.join(prefix)} {PLACE} で始める（{PLACE} は安全装置の引数がオプションとして読まれる位置）")
    if sum(PLACE in w for w in words) != 1:
        raise _fail(f"コマンドは NDF の引数を入れる位置 {PLACE} を単独の引数として 1 つだけ持つ")
    names = _option_names(row) | _workdir_names(row)
    given = [w for w in _options(words) if _names(w, names)]
    if given:
        raise _fail(
            f"安全装置と作業ディレクトリの引数（{'・'.join(sorted(names))}）は NDF が入れる。コマンドから外す（持っていた語: {' '.join(given)}）"
        )
    return [*words[:at], *args, *words[at + 1 :]]


def _workdir_names(row: dict) -> set[str]:
    return set(row[WORKDIR_COLUMN]) if WORKDIR_COLUMN in row else set()


def _workdir_args(runtime: str, cwd: Path) -> list[str]:
    """作業ディレクトリを今のディレクトリにする引数（ランタイムの表の `workdir_options` の先頭の名前）。無いランタイムは空。"""
    row = facts.row(RUNTIMES, runtime)
    return [row[WORKDIR_COLUMN][0], str(cwd)] if WORKDIR_COLUMN in row else []


def _given_value(runtime: str, command: Sequence[str], key: str) -> Optional[str]:
    """コマンドが明示した値（`key` は `model` か `effort`）。無ければ None。

    引数の名前はランタイムの表の `<key>_options`、値の形は `choice_args` の形（codex の effort は `model_reasoning_effort=<値>`）。
    `--name 値`・`--name=値`・`-n値` を読み、形に合わない値（`-c` の別の設定など）は数えない。複数あれば CLI と同じく最後。"""
    row = facts.row(RUNTIMES, runtime)
    column = f"{key}_options"
    names = set(row[column]) if column in row else set()
    words = row[model_choice.ARGS_COLUMN] if model_choice.ARGS_COLUMN in row else []
    forms = [f for f in words[1::2] if f"{{{key}}}" in f]
    if not names or not forms:
        return None
    shape = re.compile(re.escape(forms[0]).replace(re.escape(f"{{{key}}}"), "(.+)"))
    options = _options(command)
    found = None
    for i, w in enumerate(options):
        if w in names:
            value = options[i + 1] if i + 1 < len(options) else None
        elif _names(w, names):
            value = w.partition("=")[2] if w.startswith("--") else w[2:]
        else:
            continue
        hit = shape.fullmatch(value) if value is not None else None
        found = hit.group(1) if hit is not None else found
    return found


def _choice_args(runtime: str, command: Sequence[str], cwd: Path, settings: ls.Settings) -> list[str]:
    """選んだモデルと effort の引数。コマンドが明示した値は起動の引数として選択に渡し（設計書の「値の優先」の順 1。下位の環境変数を
    確かめない）、その側の引数は足さない（コマンドがすでに持つ）。"""
    model, effort = _given_value(runtime, command, "model"), _given_value(runtime, command, "effort")
    choice = model_choice.launch_choice(*CHOICE_LAUNCH, runtime, cwd=cwd, settings=settings, argument=(model, effort))
    return replace(
        choice, model=None if model is not None else choice.model, effort=None if effort is not None else choice.effort
    ).cli_args()


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=f"ndf {TOOL}", description="外部 AI への委譲を、安全装置の引数を入れて起動する")
    p.add_argument("--runtime", required=True, choices=_delegates(), help="起動するランタイム")
    p.add_argument("--use", choices=ls.USES, default="delegate", help="用途（読むだけの委譲は review）")
    heads = "、".join(f"`{' '.join(_prefix(r, facts.row(RUNTIMES, r)))}`" for r in _delegates())
    p.add_argument("command", nargs=argparse.REMAINDER, help=f"`--` の後に起動コマンドを指定する。{PLACE} は {heads} の直後に置く")
    return p


def _launch(a: argparse.Namespace) -> None:
    command = a.command[1:] if a.command[:1] == [OPTIONS_END] else a.command
    cwd = Path.cwd()
    build_command(a.runtime, command, [])  # 使い方の誤り（安全装置の引数の競合を含む）は、承認の案内より先に示す
    choice: list[str] = []
    settings = None
    if a.runtime in CHOOSES:
        settings = ls.load(ls.launch_root(cwd))
        choice = _choice_args(a.runtime, command, cwd, settings)
    safety = ls.safety_args(a.runtime, a.use, cwd, settings=settings)
    argv = build_command(a.runtime, command, [*choice, *safety.args, *_workdir_args(a.runtime, cwd)])
    why = safety.verify() if safety.verify is not None else None
    if why is not None:
        raise _fail(why)
    env = {**os.environ, **agents.ai_child_env()}
    try:
        os.execvpe(argv[0], argv, env)
    except OSError as exc:
        raise _fail(f"{argv[0]} を起動できない（{type(exc).__name__}: {exc.strerror}）") from None


def main(argv: list[str]) -> int:
    try:
        a = _parser().parse_args(argv)
        _launch(a)
    except SystemExit as exc:
        if exc.code in (0, None):
            raise
        return causes.exit_code("precondition")  # 使い方は argparse が標準エラーへ書いた
    except internal.InternalError as exc:
        print(f"ndf {TOOL}: 起動しなかった: {exc}", file=sys.stderr)
        return causes.exit_code(exc.kind)
    return 0  # exec は戻らない（テストで差し替えたときだけ）
