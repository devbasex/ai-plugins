"""サブコマンドの表から各パッケージの `main` を呼ぶ。

サブコマンドを足す子 Issue は、`COMMANDS` へ 1 行（サブコマンドの名前 → `main(argv: list[str]) -> int` を持つモジュール）を足す。
モジュールは呼ぶときだけ読む（ほかのサブコマンドの依存を起動のたびに読まない）。

サブコマンドを呼ぶ前に、状態の保存先を 1 回用意する（`identity.prepare_state_dir`。サブコマンドの書き手が `format.json` の無い
保存先を作り、後の用意がそれを旧い保存先と見なして退避するのを防ぐ）。旧い保存先を退避したら、元と先を 1 件 1 行で
標準エラーへ書く。用意に失敗したらサブコマンドを呼ばず、`stopped`（種類 `config_broken`）の結果 JSON で止める。
使い方の表示（サブコマンドなし・`--help`）と知らないサブコマンドは保存先に触れない。

どのサブコマンドも結果 JSON を 1 行出して終わる。サブコマンドが捕まえない例外は `stopped`（種類 `unknown`）、使い方の誤り
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
# 結果 JSON の tool の名前（サブコマンドへ渡す前に止まったとき）
TOOL = "cli"

# サブコマンドの名前 → モジュールのパス
COMMANDS: dict[str, str] = {
    "answer": "ndf.progress.gate",
    "check": "ndf.checks.run",
    "ci": "ndf.ci_cli",
    "contracts": "ndf.contracts.cli",
    "cross-review": "ndf.domains.crossreview.cli",
    "gws": "ndf.domains.gws.cli",
    "hooks": "ndf.domains.hooks.cli",
    "issues": "ndf.domains.backlog.cli",
    "lanes": "ndf.progress.plan.cli",
    "manifests": "ndf.domains.manifests.cli",
    "materials": "ndf.domains.materials.cli",
    "launch-args": "ndf.adapters.cli",
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
    "settings": "ndf.domains.settings.cli",
    "statusline": "ndf.domains.statusline.cli",
    "test-slot": "ndf.testing.command",
    "training": "ndf.adapters.training",
    "worker": "ndf.core.worker",
    "worktree": "ndf.domains.worktree.cli",
}
# NDF 自身の開発のサブコマンド（使い方の表示に出さない。名前で呼べば動く。#3617 の決定 11）
DEVELOPER_COMMANDS = frozenset({"check", "manifests"})
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


def _usage(name: str) -> str:
    lines = [f"使い方: {name} <サブコマンド> [引数...]", "サブコマンド:"]
    lines += [f"  {cmd}" for cmd in sorted(COMMANDS) if cmd not in DEVELOPER_COMMANDS]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        return _run(args)
    except contracts.ContractError as exc:
        # コントラクトの表（停止理由・終了コード）そのものが読めないときは、表を使わずに config_broken を出す
        return result.emit_unchecked(TOOL, "config_broken", f"コントラクトのファイルが読めない: {exc}")


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


def _prepare(ident: identity.PluginIdentity) -> int | None:
    """状態の保存先を用意し、退避した元と先を標準エラーへ書く。用意できなければ止まった結果の終了コード。"""
    try:
        ready = identity.prepare_state_dir(ident)
    except (OSError, identity.IdentityError) as exc:
        # 退避の途中か後で失敗しても、それまでに退避した元と先を残す（標準エラーと items の両方）
        retired = exc.items() if isinstance(exc, identity.IdentityError) else []
        _print_retired(retired)
        return _stop(TOOL, "config_broken", f"状態の保存先を用意できない: {exc}", items=retired)
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
    except Exception as exc:  # サブコマンドが捕まえない例外も結果 JSON（stopped/unknown）で終える
        log.exception("サブコマンドが捕まえない例外で止まった: %s", name)
        return _stop(name, "unknown", f"{name} が捕まえない例外で止まった: {type(exc).__name__}: {exc}")
