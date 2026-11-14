"""`ndf relay` の副命令。

    ndf relay install | uninstall | status       # PATH の入口 ndf-relay（#2461）
    ndf relay account use <名前> | show | clear   # 子の claude を起動するアカウントの指定（#2464）
    ndf relay pending                            # 再起動の前に載せる、待ちを抱えた実行の一覧（#3344 の決定 86）
    ndf relay mark <session_id>                  # 中継が入力を送る前に数え手へ印を立てる（#3345 の決定 92）
    ndf relay decide < 区間の状態の JSON          # 区間を切り替えるかの判断（#2962）

副命令の説明はこのモジュールの表（`ACCOUNT_COMMANDS` など）から作り、`--help` の一覧と実装が食い違わない（#2898）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Callable

from ndf import cli, identity
from ndf.contracts import causes, limits, result
from ndf.domains.hooks import counter
from ndf.domains.relay import accounts, install, pending, switch_decide

# 結果 JSON の tool の名前は副命令の名前（正本は `cli.COMMANDS`）
TOOL = next(name for name, module in cli.COMMANDS.items() if module == __name__)
ACCOUNT_COMMANDS = {
    "use": "子の claude を起動するアカウントを指定する",
    "show": "今選ばれるアカウント（環境変数 → 指定）を出す",
    "clear": "指定を消す",
}


def _install(action: str) -> int:
    ident = identity.load()
    act: Callable = {"install": install.install, "uninstall": install.uninstall, "status": install.status}[action]
    got = act(ident.root, os.environ)
    status = result.OK if action != "install" or got.state == "ours" else result.STOPPED
    summary = f"{install.ENTRY_NAME}: {got.state}（{got.path}）"
    if status == result.STOPPED:
        return result.emit(result.stopped(TOOL, "refused", f"{summary}。印の無い同じ名前のファイルがあるので置かない"))
    return result.emit(result.result(TOOL, status, summary, items=[{"path": str(got.path), "state": got.state}]))


def _account(args: argparse.Namespace) -> int:
    state_dir = identity.load().state_dir
    if args.account_action == "use":
        try:
            accounts.use(state_dir, args.name)
        except accounts.AccountError as exc:
            return result.emit(result.stopped(TOOL, "invalid", str(exc)))
        return result.emit(result.result(TOOL, result.OK, f"アカウント {args.name} を指定した"))
    if args.account_action == "clear":
        done = accounts.clear(state_dir)
        return result.emit(result.result(TOOL, result.OK, "指定を消した" if done else "指定は無かった"))
    name = accounts.chosen(os.environ, state_dir)
    return result.emit(result.result(TOOL, result.OK, f"選ばれるアカウント: {name or '（claude の既定）'}", items=[{"account": name}]))


def _pending() -> int:
    got = pending.read(identity.load().root, limits.seconds("relay.pending"))
    summary = f"待ちを抱えた実行: {len(got.runs)} 件" if got.runs is not None else got.note
    warnings = [got.note] if got.note else None
    return result.emit(result.result(TOOL, result.OK, summary, items=got.runs or [], warnings=warnings))  # noqa: NDF501 外部コマンド: 読めなかった一覧は空


def _mark(session_id: str) -> int:
    if not counter.mark_relay(session_id, identity.load().state_dir):
        return result.emit(result.stopped(TOOL, "refused", f"セッション {session_id} の数え手に印を立てられない"))
    return result.emit(result.result(TOOL, result.OK, f"セッション {session_id} に中継の印を立てた"))


def _decide() -> int:
    try:
        state = switch_decide.SectionState(**json.loads(sys.stdin.read()))
    except (ValueError, TypeError) as exc:
        return result.emit(result.stopped(TOOL, "invalid", f"区間の状態の JSON の形が違う: {exc}"))
    step = switch_decide.decide(state)
    return result.emit(result.result(TOOL, result.OK, step.reason, items=[step._asdict()]))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"ndf {TOOL}", description="中継（区間の切り替え・レーンの起動）")
    sub = parser.add_subparsers(dest="action", required=True)
    for name, text in (("install", "PATH の入口を置く"), ("uninstall", "PATH の入口を消す"), ("status", "PATH の入口の有無")):
        sub.add_parser(name, help=text)
    acc = sub.add_parser("account", help="アカウントの指定", description="副命令: " + " / ".join(f"{k}（{v}）" for k, v in ACCOUNT_COMMANDS.items()))
    acc_sub = acc.add_subparsers(dest="account_action", required=True)
    for name, text in ACCOUNT_COMMANDS.items():
        p = acc_sub.add_parser(name, help=text)
        if name == "use":
            p.add_argument("name")
    sub.add_parser("pending", help="再起動の前に、待ちを抱えた実行の一覧を出す")
    mark = sub.add_parser("mark", help="中継が入力を送る前に数え手へ印を立てる")
    mark.add_argument("session_id")
    sub.add_parser("decide", help="区間の状態の JSON（標準入力）から次の手を出す")
    return parser


def main(argv: list[str]) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return causes.exit_code("invalid") if exc.code else 0
    if args.action in ("install", "uninstall", "status"):
        return _install(args.action)
    if args.action == "account":
        return _account(args)
    if args.action == "pending":
        return _pending()
    if args.action == "mark":
        return _mark(args.session_id)
    return _decide()
