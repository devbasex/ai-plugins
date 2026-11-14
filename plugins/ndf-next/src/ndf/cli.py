"""副命令の表から各パッケージの `main` を呼ぶ。

副命令を足す子 Issue は、`COMMANDS` へ 1 行（副命令の名前 → `main(argv: list[str]) -> int` を持つモジュール）を足す。
モジュールは呼ぶときだけ読む（ほかの副命令の依存を起動のたびに読まない）。

副命令を呼ぶ前に、状態の置き場を 1 回用意する（`identity.prepare_state_dir`。副命令の書き手が `format.json` の無い
置き場を作り、後の用意がそれを旧い置き場と見なして退避するのを防ぐ）。旧い置き場を退避したら、元と先を 1 件 1 行で
標準エラーへ書く。用意に失敗したら副命令を呼ばず、`stopped`（種類 `config_broken`）の結果 JSON で止める。
使い方の表示（副命令なし・`--help`）と知らない副命令は置き場に触れない。

どの副命令も結果 JSON を 1 行出して終わる。副命令が捕まえない例外は `stopped`（種類 `unknown`）、使い方の誤り
（argparse の終了コード 2 の `SystemExit`）は `stopped`（種類 `invalid`）の結果 JSON をここで出す（#3394）。
"""

from __future__ import annotations

import importlib
import logging
import sys

from ndf import contracts, identity
from ndf.contracts import result

log = logging.getLogger(__name__)

# argparse が使い方の誤りで終わるときの終了コード
ARGPARSE_USAGE_EXIT = 2
# 結果 JSON の tool の名前（副命令へ渡す前に止まったとき）
TOOL = "cli"

# 副命令の名前 → モジュールのパス
COMMANDS: dict[str, str] = {
    "check": "ndf.checks.run",
    "ci": "ndf.ci_cli",
    "contracts": "ndf.contracts.cli",
    "hooks": "ndf.domains.hooks.cli",
    "issues": "ndf.domains.backlog.cli",
    "lanes": "ndf.progress.plan",
    "launch-args": "ndf.adapters.cli",
    "notify": "ndf.adapters.notify",
    "plan": "ndf.core.plan.cli",
    "progress": "ndf.progress.command",
    "relay": "ndf.domains.relay.cli",
    "route": "ndf.core.route",
    "test-slot": "ndf.testing.command",
    "training": "ndf.adapters.training",
    "worker": "ndf.core.worker",
    "worktree": "ndf.domains.worktree.cli",
}
# モジュールのパス → 副命令の名前（`tool_of` の引き）
_TOOL_OF: dict[str, str] = {path: name for name, path in COMMANDS.items()}


def tool_of(module: str) -> str:
    """モジュールのパス（副命令のモジュールの `__name__`）から副命令の名前を求める。

    副命令の結果 JSON の `tool` はこの名前にする（各副命令は `TOOL = cli.tool_of(__name__)` で引き、名前を写さない）。
    表に無いモジュールは `LookupError`。
    """
    if module not in _TOOL_OF:
        raise LookupError(f"副命令の表（cli.COMMANDS）に無いモジュール: {module}")
    return _TOOL_OF[module]


def _usage(name: str) -> str:
    lines = [f"使い方: {name} <副命令> [引数...]", "副命令:"]
    lines += [f"  {cmd}" for cmd in sorted(COMMANDS)]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        return _run(args)
    except contracts.ContractError as exc:
        # 契約の表（止まった理由・終了コード）そのものが読めないときは、表を使わずに config_broken を出す
        return result.emit_unchecked(TOOL, "config_broken", f"契約のファイルが読めない: {exc}")


def _stop(tool: str, kind: str, detail: str, items: list[dict[str, str]] | None = None) -> int:
    """止まった結果 JSON を出し、その終了コードを返す。"""
    return result.emit(result.stopped(tool, kind, detail, items=items))


def _print_retired(items: list[dict[str, str]]) -> None:
    for item in items:
        print(identity.RETIRED_LINE.format(**item), file=sys.stderr)


def _unknown(ident: identity.PluginIdentity, name: str) -> int:
    print(f"知らない副命令: {name}", file=sys.stderr)
    print(_usage(ident.name), file=sys.stderr)
    return _stop(TOOL, "invalid", f"知らない副命令: {name}")


def _prepare(ident: identity.PluginIdentity) -> int | None:
    """状態の置き場を用意し、退避した元と先を標準エラーへ書く。用意できなければ止まった結果の終了コード。"""
    try:
        ready = identity.prepare_state_dir(ident)
    except (OSError, identity.IdentityError) as exc:
        # 退避の途中か後で失敗しても、それまでに退避した元と先を残す（標準エラーと items の両方）
        retired = exc.items() if isinstance(exc, identity.IdentityError) else []
        _print_retired(retired)
        return _stop(TOOL, "config_broken", f"状態の置き場を用意できない: {exc}", items=retired)
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
    """副命令の `main` を呼ぶ。結果 JSON を出さずに抜けた例外と使い方の誤りも、結果 JSON にして返す。"""
    try:
        return importlib.import_module(module).main(argv)
    except SystemExit as exc:
        if exc.code != ARGPARSE_USAGE_EXIT:
            raise
        return _stop(name, "invalid", f"{name} の引数の誤り（使い方は標準エラーに出した）")
    except contracts.ContractError:
        raise
    except Exception as exc:  # 副命令が捕まえない例外も結果 JSON（stopped/unknown）で終える
        log.exception("副命令が捕まえない例外で止まった: %s", name)
        return _stop(name, "unknown", f"{name} が捕まえない例外で止まった: {type(exc).__name__}: {exc}")
