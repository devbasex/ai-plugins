"""`ndf relay` の副命令。

    ndf relay install [--replace-old] | uninstall | status   # 複製 ~/.claude/ndf/relay.py と PATH のエントリポイント ndf-relay
    ndf relay account use <名前> | show | clear               # 子の claude を起動するアカウントの指定（#2464）
    ndf relay run --name <名前> [--prompt <1 行>] [-- <claude の引数>]   # ラッパーを今の端末で動かす
    ndf relay spawn [--name <名前>] [--cwd <dir>] [--role <役割>] [--plan <計画>] [-- <claude の引数>]
    ndf relay restart <名前> | stop <名前> | ls                # リレーハブへの依頼と一覧
    ndf relay pending                                         # 再起動の前に載せる、待ちを抱えた実行の一覧（決定 86）
    ndf relay mark <session_id>                               # 子へ入力を送る前に中継の印を立てる（決定 92）
    ndf relay decide < 区間の状態の JSON                       # 区間を切り替えるかの判断（#2962）

副命令の説明は `COMMANDS`・`ACCOUNT_COMMANDS` の表から作り、`--help` の一覧と実装が食い違わない（#2898）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import termios
import tty
from pathlib import Path
from typing import Any, Callable, Mapping

from ndf import cli, identity
from ndf.contracts import causes, limits, result
from ndf.domains.relay import account_decide, accounts, hub, install, marks, pending, registry, switch_decide, wrapper
from ndf.domains.relay.tmux import Tmux

# 結果 JSON の tool の名前は副命令の名前（正本は `cli.COMMANDS`）
TOOL = next(name for name, module in cli.COMMANDS.items() if module == __name__)
COMMANDS = {
    "install": "中継の複製と PATH のエントリポイントを置く",
    "uninstall": "置いた複製と PATH のエントリポイントを消す",
    "status": "複製と PATH のエントリポイントの有無",
    "account": "子の claude を起動するアカウントの指定",
    "run": "ラッパーを今の端末で動かす",
    "spawn": "名前つきのラッパーを tmux で起動する",
    "restart": "名前つきのラッパーを起こし直す（止める前に上限を判定する）",
    "stop": "名前つきのラッパーを止める",
    "ls": "名前つきのラッパーの一覧と状態",
    "pending": "再起動の前に、待ちを抱えた実行の一覧を出す",
    "mark": "子へ入力を送る前に中継の印を立てる",
    "decide": "区間の状態の JSON（標準入力）から次の手を出す",
}
ACCOUNT_COMMANDS = {
    "use": "子の claude を起動するアカウントを指定する",
    "show": "今選ばれるアカウント（環境変数 → 指定）を出す",
    "clear": "指定を消す",
}


class EnvError(ValueError):
    """上限を替える環境変数の値の形が違う。"""


def _env_number(environ: Mapping[str, str], name: str, policy: str, key: str) -> float:
    raw = environ.get(name)
    if not raw:
        return limits.value(policy, key)
    try:
        got = float(raw)
    except ValueError as exc:
        raise EnvError(f"{name} の値が数でない: {raw}") from exc
    if got <= 0:
        raise EnvError(f"{name} の値は 0 より大きくする: {raw}")
    return got


def plugin_version(root: Path) -> str:
    data = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    return str(data["version"])


def _status_items(got: install.Installed) -> list[dict[str, Any]]:
    return [{"path": str(s.path), "state": s.state} for s in (got.copy, got.entry)]


def _install(action: str, replace_old: bool) -> int:
    ident = identity.load()
    code = causes.exit_code("precondition")
    if action == "status":
        got = install.status(os.environ, code)
    elif action == "uninstall":
        got = install.uninstall(os.environ, code)
    else:
        old = registry.legacy_running(os.environ)
        if replace_old and old:
            names = "・".join(r.name for r in old)
            return result.emit(result.stopped(TOOL, "precondition", f"旧い NDF のラッパーが動いている: {names}。止めてから置き換える"))
        got = install.install(os.environ, ident.root, code, replace_old)
    summary = f"複製: {got.copy.state}（{got.copy.path}）・エントリポイント: {got.entry.state}（{got.entry.path}）"
    warnings = [] if got.on_path else [f'{got.entry.path.parent} が PATH に無い。export PATH="{got.entry.path.parent}:$PATH" を足す']
    if action == "install" and got.copy.state == "foreign":
        warnings.append("旧い NDF の複製がある。旧いラッパーを止めてから ndf relay install --replace-old で置き換える")
    return result.emit(result.result(TOOL, result.OK, summary, items=_status_items(got), warnings=warnings))


def _account(args: argparse.Namespace) -> int:
    state_dir = identity.load().state_dir
    if args.account_action == "use":
        accounts.use(state_dir, args.name)
        return result.emit(result.result(TOOL, result.OK, f"アカウント {args.name} を指定した"))
    if args.account_action == "clear":
        done = accounts.clear(state_dir)
        return result.emit(result.result(TOOL, result.OK, "指定を消した" if done else "指定は無かった"))
    name = accounts.chosen(os.environ, state_dir)
    return result.emit(result.result(TOOL, result.OK, f"選ばれるアカウント: {name or '（claude の既定）'}", items=[{"account": name}]))


def _pending_read(root: Path) -> pending.Pending:
    return pending.read(root, limits.seconds("relay.pending"))


def _pending() -> int:
    got = _pending_read(identity.load().root)
    summary = f"待ちを抱えた実行: {len(got.runs)} 件" if got.runs is not None else got.note
    warnings = [got.note] if got.note else None
    return result.emit(result.result(TOOL, result.OK, summary, items=got.runs or [], warnings=warnings))  # noqa: NDF501 外部コマンド: 読めなかった一覧は空


def _mark(session_id: str) -> int:
    path = marks.mark(identity.load().state_dir, session_id)
    return result.emit(result.result(TOOL, result.OK, f"セッション {session_id} に中継の印を立てた", items=[{"path": str(path)}]))


def _decide() -> int:
    try:
        data = json.loads(sys.stdin.read())
        pick = data.pop("limit_pick", None)
        state = switch_decide.SectionState(**data, limit_pick=account_decide.Pick(**pick) if pick else None)
    except (ValueError, TypeError) as exc:
        return result.emit(result.stopped(TOOL, "invalid", f"区間の状態の JSON の形が違う: {exc}"))
    step = switch_decide.decide(state)
    return result.emit(result.result(TOOL, result.OK, step.reason, items=[step._asdict()]))


def hub_context(environ: Mapping[str, str]) -> hub.Context:
    ident = identity.load()
    return hub.Context(
        state_dir=ident.state_dir,
        plugin_root=ident.root,
        version=plugin_version(ident.root),
        environ=environ,
        tmux=Tmux(environ, limits.seconds("relay.tmux")),
        max_named=int(_env_number(environ, "NDF_RELAY_MAX_NAMED", "relay.max-named", "count")),
        max_starts=int(_env_number(environ, "NDF_RELAY_MAX_STARTS", "relay.max-starts", "attempts")),
        started=hub.Wait(limits.seconds("relay.started"), limits.value("relay.started", "interval")),
        stop_wait=hub.Wait(
            _env_number(environ, "NDF_RELAY_STOP_WAIT", "relay.stop-wait", "seconds"), limits.value("relay.stop-wait", "interval")
        ),
        hub_wait=hub.Wait(
            _env_number(environ, "NDF_RELAY_HUB_WAIT", "relay.hub-wait", "seconds"), limits.value("relay.hub-poll", "interval")
        ),
        pending=lambda: _pending_read(ident.root)._asdict(),
    )


def _outcome(got: hub.Outcome) -> int:
    if got.ok:
        return result.emit(result.result(TOOL, result.OK, got.summary, items=got.items))
    return result.emit(result.stopped(TOOL, got.cause, got.summary, items=got.items))


def _hub(args: argparse.Namespace) -> int:
    ctx = hub_context(os.environ)
    if args.action == "ls":
        rows = hub.listing(ctx)
        return result.emit(result.result(TOOL, result.OK, f"名前つきのラッパー: {len(rows)} 本", items=rows))
    if args.action == "spawn":
        cwd = str(Path(args.cwd).resolve()) if args.cwd else os.getcwd()
        req = {"kind": "spawn", "name": args.name, "cwd": cwd, "role": args.role, "plan": args.plan, "args": args.rest}
    else:
        req = {"kind": args.action, "name": args.name}
    return _outcome(hub.submit(ctx, req))


def wrapper_settings(name: str, rest: list[str]) -> wrapper.Settings:
    ident = identity.load()
    environ = dict(os.environ)
    return wrapper.Settings(
        state_dir=ident.state_dir,
        version=plugin_version(ident.root),
        name=name,
        cwd=Path.cwd(),
        environ=environ,
        args=tuple(rest),
        tick=limits.value("relay.tick", "interval"),
        quiet=limits.seconds("relay.quiet"),
        stall=limits.seconds("relay.stall"),
        max_starts=int(_env_number(environ, "NDF_RELAY_MAX_STARTS", "relay.max-starts", "attempts")),
        context_limit=int(limits.value("relay.context", "count")),
        usage_threshold=limits.value("relay.usage-guard", "count"),
        transcript_bytes=int(limits.value("relay.transcript-tail", "count")),
    )


def _run(args: argparse.Namespace) -> int:
    name = args.name or os.environ.get("NDF_RELAY_NAME") or f"w{os.getpid()}"
    tty_in = sys.stdin.isatty()
    saved = termios.tcgetattr(sys.stdin.fileno()) if tty_in else None
    if tty_in:
        tty.setraw(sys.stdin.fileno())
    try:
        w = wrapper.Wrapper(wrapper_settings(name, args.rest), out=sys.stdout.buffer, stdin_fd=sys.stdin.fileno() if tty_in else None)
        ended = w.run(args.prompt or "")  # noqa: NDF501 利用者の入力: 最初の 1 行が無ければ空の入力欄で始める
    finally:
        if saved is not None:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, saved)
    if ended.cause:
        return result.emit(result.stopped(TOOL, ended.cause, ended.reason))
    return result.emit(result.result(TOOL, result.OK, ended.reason))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"ndf {TOOL}", description="中継（区間の切り替え・レーンの起動）")
    sub = parser.add_subparsers(dest="action", required=True)
    p = {name: sub.add_parser(name, help=text, description=text) for name, text in COMMANDS.items() if name != "account"}
    p["install"].add_argument("--replace-old", action="store_true", help="旧い NDF の複製を置き換える（旧いラッパーが動いていれば断る）")
    acc = sub.add_parser(
        "account", help=COMMANDS["account"], description="副命令: " + " / ".join(f"{k}（{v}）" for k, v in ACCOUNT_COMMANDS.items())
    )
    acc_sub = acc.add_subparsers(dest="account_action", required=True)
    for name, text in ACCOUNT_COMMANDS.items():
        a = acc_sub.add_parser(name, help=text)
        if name == "use":
            a.add_argument("name")
    for name in ("run", "spawn"):
        p[name].add_argument("--name")
        p[name].add_argument("rest", nargs=argparse.REMAINDER)
    p["run"].add_argument("--prompt")
    for opt in ("--cwd", "--role", "--plan"):
        p["spawn"].add_argument(opt)
    for name in ("restart", "stop"):
        p[name].add_argument("name")
    p["mark"].add_argument("session_id")
    return parser


def _parse(argv: list[str]) -> argparse.Namespace:
    args = _parser().parse_args(argv)
    if getattr(args, "rest", None) and args.rest[0] == "--":
        args.rest = args.rest[1:]
    return args


HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "install": lambda a: _install("install", a.replace_old),
    "uninstall": lambda a: _install("uninstall", False),
    "status": lambda a: _install("status", False),
    "account": _account,
    "run": _run,
    "spawn": _hub,
    "restart": _hub,
    "stop": _hub,
    "ls": _hub,
    "pending": lambda a: _pending(),
    "mark": lambda a: _mark(a.session_id),
    "decide": lambda a: _decide(),
}


def main(argv: list[str]) -> int:
    try:
        args = _parse(argv)
    except SystemExit as exc:
        return causes.exit_code("invalid") if exc.code else 0
    try:
        return HANDLERS[args.action](args)
    except (accounts.AccountError, marks.MarkError, registry.RegistryError, EnvError) as exc:
        return result.emit(result.stopped(TOOL, "invalid", str(exc)))
