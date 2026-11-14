"""副命令の表から各パッケージの `main` を呼ぶ。

副命令を足す子 Issue は、`COMMANDS` へ 1 行（副命令の名前 → `main(argv: list[str]) -> int` を持つモジュール）を足す。
モジュールは呼ぶときだけ読む（ほかの副命令の依存を起動のたびに読まない）。
"""

from __future__ import annotations

import importlib
import sys

from ndf import contracts, identity
from ndf.contracts import causes, result

# 結果 JSON の tool の名前（副命令へ渡す前に止まったとき）
TOOL = "cli"

# 副命令の名前 → モジュールのパス
COMMANDS: dict[str, str] = {
    "check": "ndf.checks.run",
    "ci": "ndf.ci_cli",
    "contracts": "ndf.contracts.cli",
    "launch-args": "ndf.adapters.cli",
    "progress": "ndf.progress.command",
    "test-slot": "ndf.testing.command",
}


def _usage(name: str) -> str:
    lines = [f"使い方: {name} <副命令> [引数...]", "副命令:"]
    lines += [f"  {cmd}" for cmd in sorted(COMMANDS)] or ["  （まだ無い）"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        return _run(args)
    except contracts.ContractError as exc:
        # 契約の表（止まった理由・終了コード）そのものが読めないときは、表を使わずに config_broken を出す
        return result.emit_unchecked(TOOL, "config_broken", f"契約のファイルが読めない: {exc}")


def _run(args: list[str]) -> int:
    try:
        ident = identity.load()
    except identity.IdentityError as exc:
        return result.emit(result.stopped(TOOL, "config_broken", str(exc)))
    if not args or args[0] in ("-h", "--help"):
        print(_usage(ident.name))
        return 0
    module = COMMANDS.get(args[0])
    if module is None:
        print(f"知らない副命令: {args[0]}", file=sys.stderr)
        print(_usage(ident.name), file=sys.stderr)
        return causes.exit_code("invalid")
    return importlib.import_module(module).main(args[1:])
