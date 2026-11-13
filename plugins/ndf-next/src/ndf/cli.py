"""副命令の表から各パッケージの `main` を呼ぶ。

副命令を足す子 Issue は、`COMMANDS` へ 1 行（副命令の名前 → `main(argv: list[str]) -> int` を持つモジュール）を足す。
モジュールは呼ぶときだけ読む（ほかの副命令の依存を起動のたびに読まない）。
"""

from __future__ import annotations

import importlib
import json
import sys

from ndf import identity

# 副命令の名前 → モジュールのパス
COMMANDS: dict[str, str] = {
    "check": "ndf.checks.run",
}

# 呼び出しの誤り・設定の壊れ（`plugin.json` が読めない）の終了コード。
# 止まった理由の表（契約の子 D1）ができたら、`config_broken` の行の終了コードを引く形へ替える
EXIT_UNREADABLE = 2


def _usage(name: str) -> str:
    lines = [f"使い方: {name} <副命令> [引数...]", "副命令:"]
    lines += [f"  {cmd}" for cmd in sorted(COMMANDS)] or ["  （まだ無い）"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        ident = identity.load()
    except identity.IdentityError as exc:
        report = {"status": "stopped", "summary": str(exc), "cause": {"kind": "config_broken"}}
        print(json.dumps(report, ensure_ascii=False))
        return EXIT_UNREADABLE
    if not args or args[0] in ("-h", "--help"):
        print(_usage(ident.name))
        return 0
    module = COMMANDS.get(args[0])
    if module is None:
        print(f"知らない副命令: {args[0]}", file=sys.stderr)
        print(_usage(ident.name), file=sys.stderr)
        return EXIT_UNREADABLE
    return importlib.import_module(module).main(args[1:])
