"""`ndf relay` のサブコマンド。

    ndf relay install [--replace-old] | uninstall | status   # 複製 ~/.claude/ndf/relay.py と PATH のエントリポイント ndf-relay
    ndf relay account use <名前> | show | clear               # 子の claude を起動するアカウントの指定（#2464）
    ndf relay account add | add-bedrock | add-api-key | api-key-monthly | api-key-use | list | capacity | remove | check
                                                              # アカウントと従量の接続の登録（旧い保存先と同じ。#3569）
    ndf relay run --name <名前> [--prompt <1 行>] [-- <claude の引数>]   # ラッパーを今の端末で動かす
    ndf relay spawn [--name <名前>] [--cwd <dir>] [--role <役割>] [--plan <計画>] [-- <claude の引数>]
    ndf relay restart <名前> | stop <名前> | stop --all | ls   # リレーハブへのリクエストと一覧
    ndf relay notice                                          # カットポイントのアナウンス（ラッパーの直接の子か）
    ndf relay context resolve | record | calibrate | report   # 再起動の方針の適用値・判定の記録・見込み・作業ごとの集計
    ndf relay pending                                         # 再起動の前に載せる、待ちを抱えた実行の一覧（決定 86）
    ndf relay mark <session_id>                               # 子へ入力を送る前に中継のマーカーを立てる（決定 92）
    ndf relay decide < セグメントの状態の JSON                       # セグメントを切り替えるかの判断（#2962）

サブコマンドの説明は `COMMANDS` と `cli_account` の `ACCOUNT_COMMANDS`・`ACCOUNT_REGISTRY`、`cli_context` の `CONTEXT_COMMANDS` の表から作り、`--help` の一覧と実装が食い違わない（#2898）。
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import termios
import tty
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from ndf import cli, identity
from ndf.adapters.claude import CLAUDE
from ndf.contracts import causes, facts, limits, result
from ndf.domains.relay import (
    account_decide,
    accounts,
    claude_args,
    hub,
    install,
    lane_question,
    markers,
    notice,
    pending,
    plugin_update,
    registry,
    session_env,
    signals,
    stop_signal,
    switch_decide,
    wrapper,
)
from ndf.domains.relay import account_registry, cli_account, cli_context
from ndf.domains.relay.tmux import Tmux

# 結果 JSON の tool の名前はサブコマンドの名前（SSOT は `cli.COMMANDS`）
TOOL = cli.tool_of(__name__)
# 消えた作業ディレクトリの表示に使う（読めた値の末尾は ` (deleted)`）。読めず `PWD` も無ければ `UNKNOWN_CWD`
PROC_CWD = "/proc/self/cwd"
DELETED_SUFFIX = " (deleted)"
UNKNOWN_CWD = "（不明）"
COMMANDS = {
    "install": "中継の複製と PATH のエントリポイントを置く",
    "uninstall": "置いた複製と PATH のエントリポイントを消す",
    "status": "複製と PATH のエントリポイントの有無",
    "account": "子の claude を起動するアカウントの指定と、アカウント・従量の接続の登録",
    "run": "ラッパーを今の端末で動かす",
    "spawn": "名前つきのラッパーを tmux で起動する",
    "restart": "名前つきのラッパーを再起動する（止める前に上限を判定する）",
    "stop": "名前つきのラッパーを止める（--all ですべて）",
    "ls": "名前つきのラッパーの一覧と状態",
    "pending": "再起動の前に、待ちを抱えた実行の一覧を出す",
    "mark": "子へ入力を送る前に中継のマーカーを立てる",
    "decide": "セグメントの状態の JSON（標準入力）から次の手を出す",
    "notice": "カットポイントのアナウンスの 1 文を出す（呼び手がラッパーの直接の子か）",
    "context": "再起動の方針の適用値・判定の記録・費用の見込み・作業ごとの集計",
}


def plugin_version(root: Path) -> str:
    return str(identity.read_manifest(root)["version"])


def _status_items(got: install.Installed) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = [{"path": str(s.path), "state": s.state} for s in (got.copy, got.entry)]
    if got.version is not None:
        items.append({"path": str(got.copy.path.parent / install.VERSION_FILE), "version": got.version})
    return items


def _missing_code() -> int:
    """複製がプラグインを見つけられないときの終了コード。"""
    return causes.exit_code("precondition")


def _do_install(replace_old: bool, code: int) -> install.Installed | int:
    """複製とエントリポイントを置く。`--replace-old` で旧いラッパーが動いていれば、断った結果を出して終了コードを返す。"""
    old = registry.legacy_running(os.environ)
    if replace_old and old:
        names = "・".join(r.name for r in old)
        return result.emit(result.stopped(TOOL, "precondition", f"旧い NDF のラッパーが動いている: {names}。止めてから置き換える"))
    root = identity.load().root
    version = identity.version_of(root)
    if version is None:
        return result.emit(result.stopped(TOOL, "config_broken", f"plugin.json のバージョンを読めない: {root}"))
    return install.install(os.environ, root, code, replace_old, version)


def _emit_installed(got: install.Installed, foreign_warning: bool) -> int:
    """保存先の状態の結果を出す。`foreign_warning` が真なら、旧い NDF の複製が残ったことも警告する。"""
    summary = f"複製: {got.copy.state}（{got.copy.path}）・エントリポイント: {got.entry.state}（{got.entry.path}）"
    warnings = [] if got.on_path else [f'{got.entry.path.parent} が PATH に無い。export PATH="{got.entry.path.parent}:$PATH" を足す']
    if foreign_warning and got.copy.state == "foreign":
        warnings.append("旧い NDF の複製がある。旧いラッパーを止めてから ndf relay install --replace-old で置き換える")
    return result.emit(result.result(TOOL, result.OK, summary, items=_status_items(got), warnings=warnings))


def _install_run(args: argparse.Namespace) -> int:
    got = _do_install(args.replace_old, _missing_code())
    return got if isinstance(got, int) else _emit_installed(got, foreign_warning=True)


def _pending_read(root: Path) -> pending.Pending:
    return pending.read(root, limits.seconds("relay.pending"))


def _pending() -> int:
    got = _pending_read(identity.load().root)
    summary = f"待ちを抱えた実行: {len(got.runs)} 件" if got.runs is not None else got.note
    warnings = [got.note] if got.note else None
    return result.emit(result.result(TOOL, result.OK, summary, items=got.runs or [], warnings=warnings))  # noqa: NDF501 外部コマンド: 読めなかった一覧は空


def _mark(session_id: str) -> int:
    path = markers.mark(identity.load().state_dir, session_id)
    return result.emit(result.result(TOOL, result.OK, f"セッション {session_id} に中継のマーカーを立てた", items=[{"path": str(path)}]))


def _decide() -> int:
    try:
        data = json.loads(sys.stdin.read())
        pick = data.pop("limit_pick", None)
        state = switch_decide.SectionState(**data, limit_pick=account_decide.Pick(**pick) if pick else None)
    except (ValueError, TypeError) as exc:
        return result.emit(result.stopped(TOOL, "invalid", f"セグメントの状態の JSON の形が違う: {exc}"))
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
        max_named=int(limits.from_env(environ, "NDF_RELAY_MAX_NAMED", "relay.max-named", "count")),
        max_starts=int(limits.from_env(environ, "NDF_RELAY_MAX_STARTS", "relay.max-starts", "attempts")),
        started=hub.Wait(limits.seconds("relay.started"), limits.value("relay.started", "interval")),
        stop_wait=hub.Wait(
            limits.from_env(environ, "NDF_RELAY_STOP_WAIT", "relay.stop-wait", "seconds"), limits.value("relay.stop-wait", "interval")
        ),
        hub_wait=hub.Wait(
            limits.from_env(environ, "NDF_RELAY_HUB_WAIT", "relay.hub-wait", "seconds"), limits.value("relay.hub-poll", "interval")
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
    elif args.action == "stop" and args.all:
        return _stop_all(ctx)
    else:
        req = {"kind": args.action, "name": args.name}
    return _outcome(hub.submit(ctx, req))


def _stop_all(ctx: hub.Context) -> int:
    """動いているラッパーのすべてへ停止シグナルファイルを置き、tmux のセッションで動くものは名前つきの停止もリクエストする。

    端末から直接起動したラッパー（`ndf-relay run`）は tmux のセッションを持たないため、シグナルファイルだけで止める（今の
    セッションが終わると次のセグメントを始めずに終わる）。シグナルファイルを置けない・止められないものがあれば止まった結果。
    """
    names = [r.name for r in registry.running(ctx.state_dir)]
    items: list[dict[str, Any]] = []
    refused: list[str] = []
    for name in names:
        got = _stop_one(ctx, name)
        items.append({"name": name, "ok": got.ok, "summary": got.summary})
        if not got.ok:
            refused.append(name)
    metrics = {"stopped": len(names) - len(refused)}
    if refused:
        return result.emit(
            result.stopped(TOOL, "refused", f"止められないラッパーがある: {'・'.join(refused)}", items=items, metrics=metrics)
        )
    return result.emit(result.result(TOOL, result.OK, f"ラッパーを {len(names)} 本止めた", items=items, metrics=metrics))


def _stop_one(ctx: hub.Context, name: str) -> hub.Outcome:
    try:
        stop_signal.location_stop(registry.wrapper_dir(ctx.state_dir, name))
    except (OSError, registry.RegistryError) as exc:
        return hub.Outcome(False, f"停止シグナルファイルを置けない: {exc}", "refused")
    if ctx.tmux.has(name):
        return hub.submit(ctx, {"kind": "stop", "name": name})
    return hub.Outcome(True, f"{name} へ停止シグナルファイルを置いた（tmux の外。今のセッションが終わると止まる）")


def _notice() -> int:
    pos = notice.position(os.environ, os.getppid())
    got = notice.lines(pos, limits.seconds("relay.quiet"))
    return result.emit(
        result.result(TOOL, result.OK, got.text, items=[{"text": got.text, "position": pos.kind}], metrics={"inside": got.inside})
    )


def _cwd(environ: Mapping[str, str]) -> tuple[Path, Optional[str]]:
    """作業ディレクトリを例外なしで読む。読めれば `(Path.cwd(), None)`、消えていれば `(Path(表示), 表示)` を返す（#3842）。

    表示の文字列は停止理由に出すだけで、起動できるかは `Path.cwd()` が失敗したことだけで決める（同じ名前で作り直した
    ディレクトリや `PWD` が指す別のディレクトリで子を起動しない）。
    """
    try:
        return Path.cwd(), None
    except OSError:
        try:
            label = os.readlink(PROC_CWD).removesuffix(DELETED_SUFFIX)
        except OSError:  # 表示だけ: /proc が無ければ PWD か（不明）で表す
            label = environ.get("PWD") or UNKNOWN_CWD
        return Path(label), label


def _session_args(rest: list[str], parsed: Optional[claude_args.Parsed]) -> dict[str, Any]:
    """`wrapper.Settings` のうちセッションごとの引数の 4 項目。`parsed` が無ければ `rest` をすべてのセッションに付ける。"""
    if not parsed:
        return {"args": tuple(rest), "first_args": (), "first_session": None, "first_resume": False}
    return {
        "args": parsed.carried,
        "first_args": parsed.first,
        "first_session": parsed.resume or parsed.session_id,
        "first_resume": bool(parsed.resume),
    }


def wrapper_settings(name: str, rest: list[str], parsed: Optional[claude_args.Parsed] = None) -> wrapper.Settings:
    """`parsed` があれば引数をセッションごとに分けて渡す（無ければ `rest` をすべてのセッションに付ける）。"""
    ident = identity.load()
    environ = dict(os.environ)
    cwd, cwd_lost = _cwd(environ)
    updater = plugin_update.Updater(
        root=ident.root,
        cwd=cwd,
        env=session_env.tool_env(environ),
        timeout=limits.seconds("relay.plugin-update"),
        lock=signals.claude_config_root(environ) / facts.value("locations", "relay-plugin-update", "path"),
    )
    return wrapper.Settings(
        state_dir=ident.state_dir,
        version=plugin_version(ident.root),
        name=name,
        cwd=cwd,
        cwd_lost=cwd_lost,
        environ=environ,
        **_session_args(rest, parsed),
        tick=limits.value("relay.tick", "interval"),
        quiet=limits.seconds("relay.quiet"),
        stall=limits.seconds("relay.stall"),
        max_starts=int(limits.from_env(environ, "NDF_RELAY_MAX_STARTS", "relay.max-starts", "attempts")),
        context_limit=int(limits.value("relay.context", "count")),
        usage_threshold=limits.value("relay.usage-guard", "count"),
        transcript_bytes=int(limits.value("relay.transcript-tail", "count")),
        ask_plan=lane_question.plan_asker(environ, name, cwd, ident.root, limits.seconds("relay.lanes-ask")),
        update=updater,
        confirm=updater.confirm,
    )


def _passthrough(rest: list[str], parsed: claude_args.Parsed) -> int:
    """ラッパーを挟まず、本物の claude を同じ引数で exec する（戻らない。exec できなければ理由を出して 127）。"""
    if parsed.notice:
        print(f"ndf-relay: ラッパーを始めない（{parsed.passthrough}）。claude をそのまま起動する", file=sys.stderr)
    try:
        os.execvp(CLAUDE, [CLAUDE, *rest])
    except OSError as exc:
        print(f"ndf-relay: claude を起動できない（{exc}）", file=sys.stderr)
        return 127


def _run(args: argparse.Namespace) -> int:
    name = args.name or os.environ.get("NDF_RELAY_NAME") or f"w{os.getpid()}"
    parsed = claude_args.parse(args.rest, os.environ, sys.stdin.isatty() and sys.stdout.isatty())
    if parsed.passthrough:
        return _passthrough(args.rest, parsed)
    tty_in = sys.stdin.isatty()
    saved = termios.tcgetattr(sys.stdin.fileno()) if tty_in else None
    if tty_in:
        tty.setraw(sys.stdin.fileno())
    try:
        w = wrapper.Wrapper(
            wrapper_settings(name, args.rest, parsed), out=sys.stdout.buffer, stdin_fd=sys.stdin.fileno() if tty_in else None
        )
        ended = w.run(args.prompt or parsed.prompt or "")  # noqa: NDF501 利用者の入力: 最初の 1 行が無ければ空の入力欄で始める
    finally:
        if saved is not None:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, saved)
    return _ended(ended)


def _ended(ended: wrapper.Ended) -> int:
    """ラッパーの終わりを伝える。人が端末で打った claude の終わりなので、結果 JSON は出さない。

    普通に終わったとき（子が次のセグメントを頼まずに終わった）は何も出さず 0 を返す（claude を直接打ったときと同じ）。
    止まったときは `ndf-relay:` の 1 行で理由を標準エラーへ出し、停止理由の種類の終了コードを返す。
    """
    if not ended.cause:
        return 0
    print(f"ndf-relay: {ended.reason}", file=sys.stderr)
    return causes.exit_code(ended.cause)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"ndf {TOOL}", description="中継（セグメントの切り替え・レーンの起動）")
    sub = parser.add_subparsers(dest="action", required=True)
    p = {name: sub.add_parser(name, help=text, description=text) for name, text in COMMANDS.items() if name not in ("account", "context")}
    cli_context.parser(sub, COMMANDS["context"])
    p["install"].add_argument("--replace-old", action="store_true", help="旧い NDF の複製を置き換える（旧いラッパーが動いていれば断る）")
    cli_account.parser(sub, COMMANDS["account"])
    for name in ("run", "spawn"):
        p[name].add_argument("--name")
        p[name].add_argument("rest", nargs=argparse.REMAINDER)
    p["run"].add_argument("--prompt")
    for opt in ("--cwd", "--role", "--plan"):
        p["spawn"].add_argument(opt)
    p["restart"].add_argument("name")
    stop = p["stop"].add_mutually_exclusive_group(required=True)
    stop.add_argument("name", nargs="?", help="止めるラッパーの名前")
    stop.add_argument("--all", action="store_true", help="動いている名前つきのラッパーをすべて止める")
    p["mark"].add_argument("session_id")
    return parser


OWN_OPTIONS = {"run": ("--name", "--prompt"), "spawn": ("--name", "--cwd", "--role", "--plan")}


def _split_claude_args(argv: list[str]) -> list[str]:
    """run・spawn は先頭の ndf のオプションだけを読み、残りの前に -- を差して claude へ渡す。

    argparse の REMAINDER は、先頭が - の知らない語を拾わない（`run --resume x` が止まる）。
    """
    if not argv or argv[0] not in OWN_OPTIONS:
        return argv
    own, i = OWN_OPTIONS[argv[0]], 1
    while i < len(argv):
        word = argv[i]
        if word in ("-h", "--help", "--"):
            return argv
        if word in own:
            i += 2
        elif word.split("=", 1)[0] in own:
            i += 1
        else:
            break
    return argv[:i] + ["--"] + argv[i:] if i < len(argv) else argv


def _parse(argv: list[str]) -> argparse.Namespace:
    args = _parser().parse_args(_split_claude_args(argv))
    if args.action == "context" and args.context_action == "record" and (args.verdict is not None) != (args.reason is not None):
        _parser().error("--verdict 再起動 には --reason を、--event には --reason を付けない")
    if getattr(args, "rest", None) and args.rest[0] == "--":
        args.rest = args.rest[1:]
    return args


HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "install": _install_run,
    "uninstall": lambda a: _emit_installed(install.uninstall(os.environ, _missing_code()), foreign_warning=False),
    "status": lambda a: _emit_installed(install.status(os.environ, _missing_code()), foreign_warning=False),
    "account": cli_account.run,
    "run": _run,
    "spawn": _hub,
    "restart": _hub,
    "stop": _hub,
    "ls": _hub,
    "pending": lambda a: _pending(),
    "mark": lambda a: _mark(a.session_id),
    "decide": lambda a: _decide(),
    "notice": lambda a: _notice(),
    "context": lambda a: cli_context.CONTEXT_HANDLERS[a.context_action](a),
}


def main(argv: list[str]) -> int:
    secret_args = argv[:2] == ["account", "add-api-key"]
    try:
        with contextlib.redirect_stderr(io.StringIO()) if secret_args else contextlib.nullcontext():
            args = _parse(argv)
    except SystemExit as exc:
        if exc.code and secret_args:  # 余った引数はキーの値でありうるので、引数を写さない
            print("引数を読めない（引数は表示しない）。キーの値は標準入力か --key-file で渡す", file=sys.stderr)
        return causes.exit_code("invalid") if exc.code else 0
    try:
        return HANDLERS[args.action](args)
    except (accounts.AccountError, account_registry.AccountError, markers.MarkerError, registry.RegistryError, limits.EnvError) as exc:
        return result.emit(result.stopped(TOOL, "invalid", str(exc)))
