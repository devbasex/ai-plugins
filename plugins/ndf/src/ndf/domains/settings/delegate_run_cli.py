"""サブコマンド `ndf delegate-run`: 手順書の外部 AI への委譲を、安全装置の引数を入れて起動する（#1959）。

    ndf delegate-run --runtime codex|kiro|agy [--use delegate|review] -- <CLI のコマンド>

CLI のコマンドの先頭はそのランタイムの実行ファイルで、NDF の引数を入れる位置 `{args}` を単独の引数として 1 つだけ持つ。
安全装置の引数（`launch_settings.safety_args`）を取り、外す引数なら起動の直前に承認の対象を読み直してから、子の環境に
`NDF_AI_CHILD=1` を足してこのプロセスを CLI に置き換える（`exec`）。標準入力・標準出力・終了コードは CLI のものになる。

コマンドが安全装置の引数を自分で持つ・今のディレクトリと違う作業ディレクトリを指す・起動できない状況・読み直しの食い違いは、
CLI を起動せず、理由を標準エラーだけに書いて停止理由 `precondition` の終了コードで終える。結果 JSON は出さない
（標準出力は CLI に渡すため。`cli.NO_RESULT_COMMANDS`）。安全装置の引数だけを返すコマンドは作らない（取得の後の取り消しを
確かめられないため。設計書の「安全装置を外す指定」）。
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from ndf import cli
from ndf.adapters import agents, launch_settings as ls
from ndf.contracts import causes, facts, internal

TOOL = cli.tool_of(__name__)
RUNTIMES = ls.RUNTIMES
# NDF の引数を入れる位置
PLACE = "{args}"
# 安全装置の引数の名前を集める列（表 runtimes。別名の列を足す）
SAFETY_COLUMNS = (*ls.SANDBOXED.values(), ls.UNSANDBOXED, "safety_aliases")
# 作業ディレクトリを指す引数の名前の列
WORKDIR_COLUMN = "workdir_options"
# 引数の終わり（これより後の語は CLI のオプションとして読まれない）
OPTIONS_END = "--"


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


def build_command(runtime: str, command: Sequence[str], args: Sequence[str]) -> list[str]:
    """先頭の語がランタイムの CLI で、`{args}` が単独の引数として 1 つだけあり、安全装置の引数を自分で持たないことを確かめ、
    `{args}` を `args` に広げる。満たさなければ `precondition`。"""
    row = facts.row(RUNTIMES, runtime)
    exe = agents.executable(runtime)
    if not command or Path(command[0]).name != exe:
        raise _fail(f"コマンドの先頭は {runtime} の CLI（{exe}）にする")
    if list(command).count(PLACE) != 1 or sum(PLACE in w for w in command) != 1:
        raise _fail(f"コマンドは NDF の引数を入れる位置 {PLACE} を単独の引数として 1 つだけ持つ")
    names = _option_names(row)
    given = [w for w in _options(command) if _names(w, names)]
    if given:
        raise _fail(f"安全装置の引数（{'・'.join(sorted(names))}）は NDF が入れる。コマンドから外す（持っていた語: {' '.join(given)}）")
    at = list(command).index(PLACE)
    return [*command[:at], *args, *command[at + 1 :]]


def _check_workdir(runtime: str, command: Sequence[str], cwd: Path) -> None:
    """作業ディレクトリを指す引数が今のディレクトリと違えば `precondition`（承認とサンドボックスは今のディレクトリで確かめる）。"""
    row = facts.row(RUNTIMES, runtime)
    names = set(row[WORKDIR_COLUMN]) if WORKDIR_COLUMN in row else set()
    words = _options(command)
    for i, word in enumerate(words):
        head, eq, value = word.partition("=")
        if head not in names:
            continue
        if not eq:
            value = words[i + 1] if i + 1 < len(words) else ""
        if (cwd / value).resolve() != cwd.resolve():
            raise _fail(
                f"作業ディレクトリの指定（{'・'.join(sorted(names))}）が今のディレクトリと違う（{value}）。"
                f'対象のディレクトリへ移ってから -C "$PWD" で打つ'
            )


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=f"ndf {TOOL}", description="外部 AI への委譲を、安全装置の引数を入れて起動する")
    p.add_argument("--runtime", required=True, choices=_delegates(), help="起動するランタイム")
    p.add_argument("--use", choices=ls.USES, default="delegate", help="用途（読むだけの委譲は review）")
    p.add_argument("command", nargs=argparse.REMAINDER, help=f"-- の後に CLI のコマンド（{PLACE} を 1 つ持つ）")
    return p


def _launch(a: argparse.Namespace) -> None:
    command = a.command[1:] if a.command[:1] == [OPTIONS_END] else a.command
    cwd = Path.cwd()
    _check_workdir(a.runtime, command, cwd)
    build_command(a.runtime, command, [])  # 使い方の誤り（安全装置の引数の競合を含む）は、承認の案内より先に示す
    safety = ls.safety_args(a.runtime, a.use, cwd)
    argv = build_command(a.runtime, command, safety.args)
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
