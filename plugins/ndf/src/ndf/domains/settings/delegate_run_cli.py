"""サブコマンド `ndf delegate-run`: 手順書の外部 AI への委譲を、安全装置の引数を入れて起動する（#1959）。

    ndf delegate-run --runtime codex|kiro|agy [--use delegate|review] -- <CLI のコマンド>

CLI のコマンドは、そのランタイムの実行ファイルと決まった語（ランタイムの表の `delegate_prefix`。codex は `exec`）で始め、
そのすぐ後に NDF の引数を入れる位置 `{args}` を単独の引数として 1 つだけ持つ。`{args}` には安全装置の引数
（`launch_settings.safety_args`）と、作業ディレクトリを今のディレクトリにする引数（codex は `-C <今のディレクトリ>`）を入れる。外す引数なら起動の直前に承認の対象を読み直してから、子の環境に
`NDF_AI_CHILD=1` を足してこのプロセスを CLI に置き換える（`exec`）。標準入力・標準出力・終了コードは CLI のものになる。

コマンドの始まりが違う・安全装置か作業ディレクトリの引数を自分で持つ・起動できない状況・読み直しの食い違いは、
CLI を起動せず、理由を標準エラーだけに書いて停止理由 `precondition` の終了コードで終える。結果 JSON は出さない
（標準出力は CLI に渡すため。`cli.NO_RESULT_COMMANDS`）。安全装置の引数だけを返すコマンドは作らない（取得の後の取り消しを
確かめられないため。設計書の「安全装置を外す指定」）。

    ndf delegate-run --runtime <ランタイム> --usage-limit --stdout <ファイル> --stderr <ファイル>

は CLI を起動せず、終わった起動の出力が利用上限を告げているかを、NDF の監視と同じ条件（`agents.launch_usage_limit`）で
判定する。手順書の回収が、AI に正規表現を読ませずに監視と同じ結論を得るための口である。
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
# 作業ディレクトリを指す引数の名前の列（先頭の名前で NDF が今のディレクトリを入れる）
WORKDIR_COLUMN = "workdir_options"
# 実行ファイルのすぐ後に置く語の列（そのすぐ後が `{args}` の位置）
PREFIX_COLUMN = "delegate_prefix"
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


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=f"ndf {TOOL}", description="外部 AI への委譲を、安全装置の引数を入れて起動する")
    p.add_argument(
        "--runtime",
        required=True,
        choices=[r["id"] for r in facts.rows(RUNTIMES)],
        help=f"起動するランタイム（起動は {'・'.join(_delegates())}。--usage-limit はすべて）",
    )
    p.add_argument("--use", choices=ls.USES, default="delegate", help="用途（読むだけの委譲は review）")
    p.add_argument(
        "--usage-limit",
        action="store_true",
        help="CLI を起動せず、終わった起動の出力が利用上限を告げているかを NDF の監視と同じ条件で判定する。"
        "指定する場合は --stdout と --stderr の両方が必須で、起動コマンドは指定できない",
    )
    p.add_argument(
        "--stdout", type=Path, help="判定する起動の標準出力のファイル。--usage-limit を指定する場合は必須で、それ以外では指定できない"
    )
    p.add_argument(
        "--stderr", type=Path, help="判定する起動の標準エラー出力のファイル。--usage-limit を指定する場合は必須で、それ以外では指定できない"
    )
    heads = "、".join(f"`{' '.join(_prefix(r, facts.row(RUNTIMES, r)))}`" for r in _delegates())
    p.add_argument("command", nargs=argparse.REMAINDER, help=f"`--` の後に起動コマンドを指定する。{PLACE} は {heads} の直後に置く")
    return p


def judge_usage_limit(a: argparse.Namespace) -> int:
    """`--usage-limit`: 起動の出力が利用上限を告げていれば、判定の根拠の行を標準出力へ書き、停止理由 `usage_limit` の
    終了コードで終える。告げていなければ何も書かず 0。条件は監視と同じ `agents.launch_usage_limit` で、回収する AI に
    正規表現を読ませない（AI の読み方で成否が分かれないようにする）。

    ログのファイルが無ければ判定せず `precondition` で止める。監視の `tail` は起動の前の無いファイルを空として読むが、
    終わった起動のログが無いのはパスの取り違えで、空として読むと利用上限で止まった起動を成功として回収させる。"""
    if a.stdout is None or a.stderr is None or a.command:
        raise _fail("--usage-limit は --stdout と --stderr の両方を取り、起動コマンドを取らない")
    missing = [str(p) for p in (a.stdout, a.stderr) if not p.is_file()]
    if missing:
        raise _fail(f"判定するログのファイルが無い（{'・'.join(missing)}）。起動のときに逃がした先のパスを渡す")
    hit = agents.launch_usage_limit(a.runtime, a.stdout, a.stderr)
    if hit is None:
        return 0
    print(hit)
    return causes.exit_code("usage_limit")


def _launch(a: argparse.Namespace) -> None:
    if a.runtime not in _delegates() or a.stdout is not None or a.stderr is not None:
        raise _fail(f"起動できるランタイムは {'・'.join(_delegates())}。--stdout と --stderr は --usage-limit だけで使う")
    command = a.command[1:] if a.command[:1] == [OPTIONS_END] else a.command
    cwd = Path.cwd()
    build_command(a.runtime, command, [])  # 使い方の誤り（安全装置の引数の競合を含む）は、承認の案内より先に示す
    safety = ls.safety_args(a.runtime, a.use, cwd)
    argv = build_command(a.runtime, command, [*safety.args, *_workdir_args(a.runtime, cwd)])
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
        if a.usage_limit:
            return judge_usage_limit(a)
        _launch(a)
    except SystemExit as exc:
        if exc.code in (0, None):
            raise
        return causes.exit_code("precondition")  # 使い方は argparse が標準エラーへ書いた
    except internal.InternalError as exc:
        print(f"ndf {TOOL}: 起動しなかった: {exc}", file=sys.stderr)
        return causes.exit_code(exc.kind)
    return 0  # exec は戻らない（テストで差し替えたときだけ）
