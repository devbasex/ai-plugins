"""サブコマンドの表から各パッケージの `main` を呼ぶ。

サブコマンドを足す子 Issue は、`COMMANDS` へ 1 行（サブコマンドの名前 → `main(argv: list[str]) -> int` を持つモジュール）を足す。
モジュールは呼ぶときだけ読む（ほかのサブコマンドの依存を起動のたびに読まない）。

サブコマンドを呼ぶ前に、状態の保存先を 1 回用意する（`identity.prepare_state_dir`。サブコマンドの書き手が `format.json` の無い
保存先を作り、後の用意がそれを旧い保存先と見なして退避するのを防ぐ）。旧い保存先を退避したら、元と先を 1 件 1 行で
標準エラーへ書く。用意に失敗したらサブコマンドを呼ばず、`stopped` の結果 JSON で止める（種類は `IdentityError.kind`。旧いラッパーが
動いていれば `store_in_use`、退避先が既にあれば `precondition`、ほかは `config_broken`）。
使い方の表示（サブコマンドなし・`--help`）と知らないサブコマンドは保存先に触れない。

結果の形の明示 `--output-format=json|text`（`--output-format json` も可）は、サブコマンドの名前より前に置いたものだけをエントリポイントで
1 回読み、`result.choose_format` へ渡す（#4003）。サブコマンドの後ろは読まない（`ndf relay claude` などは後ろの引数を子の
`claude` へそのまま渡し、`claude` も同じ名前の引数を持つ）。明示が無ければ、標準出力が端末のときは文、ほかは結果 JSON で出す。

どのサブコマンドも結果 JSON を 1 行出して終わる（`NO_RESULT_COMMANDS` を除く）。内部データの読み書きの例外は `stopped`（種類は `InternalError.kind`）、ほかの
サブコマンドが捕まえない例外は `stopped`（種類 `unknown`）、使い方の誤り
（argparse の終了コード 2 の `SystemExit`）は `stopped`（種類 `invalid`）の結果 JSON をここで出す（#3394）。

サブコマンドを子プロセスで打つ所は `ndf_command` で argv を組む。`bin/start.py` が `PYTHONPATH` を呼び出し元の値へ
戻すので（#4029）、子へは自分の `src` を argv で渡して子の `sys.path` にだけ入れる（環境変数は呼び出し元の値のまま）。
子は `-I` で起動し、起動時の import の探索に呼び出し元の `PYTHONPATH` を使わない（その道の `sitecustomize` が同じ名前の
パッケージを先に `sys.modules` へ入れない）。環境変数は消さないので、子の下のステップは呼び出し元の値をそのまま受ける。
子の標準出力・標準エラーは UTF-8 に決め（`CHILD_ENCODING`）、読む側も `child_encoding` で UTF-8 と分かる。ロケールの
文字コードは呼び出し元のまま（`-I` が無視する `PYTHONUTF8` は `-X utf8=` で写す）なので、子の下のステップの出力は従来どおり
ロケールで読む。`os.execv` で自分を置き換えるとき（`ndf answer work`）は、呼び出し元が置き換える前の文字コードで読むので、
今の標準出力・標準エラーの文字コードを写す（`ndf_command` の `keep_streams`）。
"""

from __future__ import annotations

import importlib
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from ndf import contracts, identity
from ndf.contracts import internal, result

log = logging.getLogger(__name__)

# argparse が使い方の誤りで終わるときの終了コード
ARGPARSE_USAGE_EXIT = 2
# 結果 JSON の tool の名前（サブコマンドへ渡す前に止まったとき）
TOOL = "cli"

# このプラグインの Python のパッケージの名前（`python -m` で打つ名前）
PACKAGE = __package__.partition(".")[0]
# このパッケージの保存先（`src`。子プロセスの import の道）
SRC = Path(__file__).resolve().parents[1]
# `ndf_command` の子の標準出力・標準エラーの文字コード。子の出力をパイプで読む側はこれで読む（`child_encoding`）
CHILD_ENCODING = "utf-8"
# `ndf_command` の子の標準出力・標準エラーの文字コードと誤りの扱い（既定。`keep_streams` なら呼び出し元の値を写す）
_CHILD_STREAMS = ((CHILD_ENCODING, "surrogateescape"), (CHILD_ENCODING, "backslashreplace"))


def _boot(streams: tuple[tuple[str, str], tuple[str, str]]) -> str:
    """子プロセスの起動の 1 行（標準出力・標準エラーを `streams` の文字コードと誤りの扱いに決め直し、第 1 引数の `src` を
    sys.path の頭へ入れ、残りの引数で `python -m ndf` と同じに走らせる）。ロケールの文字コードは変えない。"""
    (out_enc, out_err), (err_enc, err_err) = streams
    return (
        "import runpy, sys; "
        f"sys.stdout and sys.stdout.reconfigure(encoding={out_enc!r}, errors={out_err!r}); "
        f"sys.stderr and sys.stderr.reconfigure(encoding={err_enc!r}, errors={err_err!r}); "
        f"sys.path.insert(0, sys.argv.pop(1)); runpy.run_module({PACKAGE!r}, run_name='__main__', alter_sys=True)"
    )


# `-I` が無視する UTF-8 モードの指定（`PYTHONUTF8`）。呼び出し元の値を `-X utf8=` で子へ写す
_UTF8_MODE_ENV = "PYTHONUTF8"
_UTF8_MODES = ("0", "1")

# サブコマンドの名前 → モジュールのパス
COMMANDS: dict[str, str] = {
    "answer": "ndf.progress.gate",
    "check": "ndf.checks.run",
    "ci": "ndf.ci_cli",
    "contracts": "ndf.contracts.cli",
    "cross-review": "ndf.domains.crossreview.cli",
    "delegate-run": "ndf.domains.settings.delegate_run_cli",
    "document-rebuild": "ndf.domains.docrebuild.cli",
    "gws": "ndf.domains.gws.cli",
    "hooks": "ndf.domains.hooks.cli",
    "issues": "ndf.domains.backlog.cli",
    "lanes": "ndf.progress.plan.cli",
    "manifests": "ndf.domains.manifests.cli",
    "materials": "ndf.domains.materials.cli",
    "launch-args": "ndf.adapters.cli",
    "launch-config": "ndf.domains.settings.launch_config_cli",
    "loop": "ndf.domains.loop.cli",
    "notify": "ndf.adapters.notify",
    "plan": "ndf.core.plan.cli",
    "progress": "ndf.progress.gate.progress_cli",
    "project": "ndf.domains.project.cli",
    "records": "ndf.domains.records.cli",
    "refactor": "ndf.domains.crossrefactor.cli",
    "relay": "ndf.domains.relay.cli",
    "release-steps": "ndf.domains.release.cli",
    "route": "ndf.core.route",
    "samples": "ndf.domains.samples.cli",
    "settings": "ndf.domains.settings.cli",
    "statusline": "ndf.domains.statusline.cli",
    "test-slot": "ndf.testing.command",
    "training": "ndf.adapters.training",
    "worker": "ndf.core.worker",
    "worktree": "ndf.domains.worktree.cli",
}
# NDF 自身の開発のサブコマンド（使い方の表示に出さない。名前で呼べば動く。#3617 の決定 11）
DEVELOPER_COMMANDS = frozenset({"check", "manifests", "samples"})
# 結果 JSON を出さないサブコマンド（標準出力を置き換えた CLI に渡し、失敗は標準エラーだけに書く。#1959 の `ndf delegate-run`）
NO_RESULT_COMMANDS = frozenset({"delegate-run"})
# モジュールのパス → サブコマンドの名前（`tool_of` の引き。2 つの名前が同じモジュールを指すときは表の先の名前）
_TOOL_OF: dict[str, str] = {path: name for name, path in reversed(COMMANDS.items())}


def tool_of(module: str) -> str:
    """モジュールのパス（サブコマンドのモジュールの `__name__`）からサブコマンドの名前を求める。

    サブコマンドの結果 JSON の `tool` はこの名前にする（各サブコマンドは `TOOL = cli.tool_of(__name__)` で引き、名前を写さない）。
    表に無いモジュールは `LookupError`。
    """
    if module not in _TOOL_OF:
        raise LookupError(f"サブコマンドの表（cli.COMMANDS）に無いモジュール: {module}")
    return _TOOL_OF[module]


# 結果の形を明示する引数（サブコマンドの名前より前にだけ置ける）の使い方の書き方
FORMAT_USAGE = "[--output-format=json|text]"


def _usage(name: str) -> str:
    lines = [f"使い方: {name} {FORMAT_USAGE} <サブコマンド> [引数...]", "サブコマンド:"]
    lines += [f"  {cmd}" for cmd in sorted(COMMANDS) if cmd not in DEVELOPER_COMMANDS]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    fmt, args, bad = _output_format(args)
    result.choose_format(fmt)
    try:
        if bad is not None:
            return _stop(TOOL, "invalid", bad)
        return _run(args)
    except contracts.ContractError as exc:
        # コントラクトの表（停止理由・終了コード）そのものが読めないときは、表を使わずに config_broken を出す
        return result.emit_unchecked(TOOL, "config_broken", f"コントラクトのファイルが読めない: {exc}")
    finally:
        result.choose_format(None)


def _output_format(args: list[str]) -> tuple[str | None, list[str], str | None]:
    """サブコマンドの名前より前の `--output-format` を読む。明示の形（無ければ None）・残りの引数・誤りの文（無ければ None）。

    名前は `claude` の同じ名前の引数とそろえたが、NDF の引数で別の事実なので、`adapters.claude` の定数とは共有しない。"""
    option = "--output-format"
    fmt: str | None = None
    rest = list(args)
    while rest and rest[0].partition("=")[0] == option:
        head = rest.pop(0)
        value = head.partition("=")[2] if "=" in head else (rest.pop(0) if rest else "")
        if value not in result.FORMATS:
            return None, rest, f"{option} の値は {' か '.join(result.FORMATS)}（渡された値: {value or '無し'}）"
        fmt = value
    return fmt, rest, None


def _stop(tool: str, kind: str, detail: str, items: list[dict[str, str]] | None = None) -> int:
    """止まった結果 JSON を出し、その終了コードを返す。"""
    return result.emit(result.stopped(tool, kind, detail, items=items))


def _print_retired(items: list[dict[str, str]]) -> None:
    for item in items:
        print(identity.RETIRED_LINE.format(**item), file=sys.stderr)


def _unknown(ident: identity.PluginIdentity, name: str) -> int:
    print(f"知らないサブコマンド: {name}", file=sys.stderr)
    print(_usage(ident.name), file=sys.stderr)
    return _stop(TOOL, "invalid", f"知らないサブコマンド: {name}")


def _not_prepared(exc: Exception, kind: str, items: list[dict[str, str]]) -> int:
    """状態の保存先を用意できなかった結果を出す。退避の途中か後で失敗しても、それまでに退避した元と先を残す（標準エラーと items の両方）。"""
    _print_retired([item for item in items if "retired_from" in item])
    return _stop(TOOL, kind, f"状態の保存先を用意できない: {exc}", items=items)


def _prepare(ident: identity.PluginIdentity) -> int | None:
    """状態の保存先を用意し、退避した元と先を標準エラーへ書く。用意できなければ止まった結果の終了コード。"""
    try:
        ready = identity.prepare_state_dir(ident)
    except identity.IdentityError as exc:
        return _not_prepared(exc, exc.kind, exc.items())
    except OSError as exc:
        return _not_prepared(exc, "config_broken", [])
    _print_retired(ready.items())
    return None


def _run(args: list[str]) -> int:
    try:
        ident = identity.load()
    except identity.IdentityError as exc:
        return _stop(TOOL, "config_broken", str(exc))
    if not args or args[0] in ("-h", "--help"):
        print(_usage(ident.name))
        return 0
    module = COMMANDS.get(args[0])
    if module is None:
        return _unknown(ident, args[0])
    stopped = _prepare(ident)
    if stopped is not None:
        return stopped
    return _call(args[0], module, args[1:])


def _call(name: str, module: str, argv: list[str]) -> int:
    """サブコマンドの `main` を呼ぶ。結果 JSON を出さずに抜けた例外と使い方の誤りも、結果 JSON にして返す。"""
    try:
        return importlib.import_module(module).main(argv)
    except SystemExit as exc:
        if exc.code != ARGPARSE_USAGE_EXIT:
            raise
        return _stop(name, "invalid", f"{name} の引数の誤り（使い方は標準エラーに出した）")
    except contracts.ContractError:
        raise
    except internal.InternalError as exc:  # 内部データの形が違う・種類が無い・新しいバージョン（種類は例外が持つ）
        return _stop(name, exc.kind, str(exc))
    except Exception as exc:  # サブコマンドが捕まえない例外も結果 JSON（stopped/unknown）で終える
        log.exception("サブコマンドが捕まえない例外で止まった: %s", name)
        return _stop(name, "unknown", f"{name} が捕まえない例外で止まった: {type(exc).__name__}: {exc}")


def ndf_command(*args: str, keep_streams: bool = False) -> list[str]:
    """このプラグインのサブコマンドを子プロセスで打つ argv（同じ Python で `python -m ndf` と同じに走らせる）。

    自分の `src` は子の `sys.path` の頭へだけ入れ、環境変数（`PYTHONPATH`）には入れない（子孫のステップへ渡らない。#4029）。
    `-I` で作業ディレクトリと `PYTHON*` の環境変数（呼び出し元の `PYTHONPATH` とその道の `sitecustomize`）を import の道に
    入れない。環境変数そのものは子の `os.environ` に残る。

    `-I` は `PYTHONUTF8`・`PYTHONIOENCODING` も無視するので、子の標準出力・標準エラーは起動の 1 行で決める。既定は
    `CHILD_ENCODING`（ロケールや呼び出し元の指定で親子の文字コードが食い違わない。読む側は `child_encoding` で選ぶ）。
    `keep_streams` は今の標準出力・標準エラーの文字コードと誤りの扱いを写す（`os.execv` で自分を置き換えるとき。呼び出し元は
    置き換える前のプロセスの文字コードで読むため。`ndf answer work`）。ロケールの文字コードは変えず、`PYTHONUTF8` は
    `-X utf8=` で写す（子の下のステップの出力は、従来どおり呼び出し元のロケールで読む。#4029）。"""
    mode = os.environ.get(_UTF8_MODE_ENV)
    utf8 = ["-X", f"utf8={mode}"] if mode in _UTF8_MODES else []
    streams = _current_streams() if keep_streams else _CHILD_STREAMS
    return [sys.executable, "-I", *utf8, "-c", _boot(streams), str(SRC), *args]


def _current_streams() -> tuple[tuple[str, str], tuple[str, str]]:
    """今の標準出力・標準エラーの文字コードと誤りの扱い（無いストリームは `_CHILD_STREAMS` の値）。"""
    pairs = []
    for stream, fallback in zip((sys.stdout, sys.stderr), _CHILD_STREAMS):
        encoding, errors = getattr(stream, "encoding", None), getattr(stream, "errors", None)
        pairs.append((encoding, errors or fallback[1]) if encoding else fallback)
    return pairs[0], pairs[1]


def child_encoding(argv: list[str]) -> Optional[str]:
    """`argv` の出力を読む文字コード。`ndf_command` の子なら `CHILD_ENCODING`、ほかのコマンドは None（ロケールで読む）。"""
    head = ndf_command()
    return CHILD_ENCODING if argv[: len(head)] == head else None
