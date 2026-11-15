"""`ndf relay` の副命令。

    ndf relay install [--replace-old] | uninstall | status   # 複製 ~/.claude/ndf/relay.py と PATH のエントリポイント ndf-relay
    ndf relay account use <名前> | show | clear               # 子の claude を起動するアカウントの指定（#2464）
    ndf relay run --name <名前> [--prompt <1 行>] [-- <claude の引数>]   # ラッパーを今の端末で動かす
    ndf relay spawn [--name <名前>] [--cwd <dir>] [--role <役割>] [--plan <計画>] [-- <claude の引数>]
    ndf relay restart <名前> | stop <名前> | stop --all | ls   # リレーハブへの依頼と一覧
    ndf relay notice                                          # カットポイントのアナウンス（ラッパーの直接の子か）
    ndf relay context resolve | record | calibrate | report   # 再起動の方針の適用値・判定の記録・見込み・作業ごとの集計
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
import time
import tty
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from ndf import cli, identity
from ndf.contracts import causes, limits, result
from ndf.domains.relay import account_decide, accounts, hub, install, lane_question, marks, notice, pending, registry, switch_decide, wrapper
from ndf.domains.relay import context_policy, context_records, context_report
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
    "stop": "名前つきのラッパーを止める（--all ですべて）",
    "ls": "名前つきのラッパーの一覧と状態",
    "pending": "再起動の前に、待ちを抱えた実行の一覧を出す",
    "mark": "子へ入力を送る前に中継の印を立てる",
    "decide": "区間の状態の JSON（標準入力）から次の手を出す",
    "notice": "カットポイントのアナウンスの 1 文を出す（呼び手がラッパーの直接の子か）",
    "context": "再起動の方針の適用値・判定の記録・費用の見込み・作業ごとの集計",
}
CONTEXT_COMMANDS = {
    "resolve": "再起動の方針の適用値と値ごとの出所を出す",
    "record": "再起動か印（引継ぎ開始・復元完了）の 1 行を判定の記録へ足す",
    "calibrate": "判定の記録と会話の記録から費用の見込みを較正して書く",
    "report": "作業ごとの区分の token・秒・費用・再起動・自動圧縮・品質・復元の量を集計する",
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


def _missing_code() -> int:
    """複製がプラグインを見つけられないときの終了コード。"""
    return causes.exit_code("precondition")


def _do_install(replace_old: bool, code: int) -> install.Installed | int:
    """複製とエントリポイントを置く。`--replace-old` で旧いラッパーが動いていれば、断った結果を出して終了コードを返す。"""
    old = registry.legacy_running(os.environ)
    if replace_old and old:
        names = "・".join(r.name for r in old)
        return result.emit(result.stopped(TOOL, "precondition", f"旧い NDF のラッパーが動いている: {names}。止めてから置き換える"))
    return install.install(os.environ, identity.load().root, code, replace_old)


def _emit_installed(got: install.Installed, foreign_warning: bool) -> int:
    """置き場の状態の結果を出す。`foreign_warning` が真なら、旧い NDF の複製が残ったことも警告する。"""
    summary = f"複製: {got.copy.state}（{got.copy.path}）・エントリポイント: {got.entry.state}（{got.entry.path}）"
    warnings = [] if got.on_path else [f'{got.entry.path.parent} が PATH に無い。export PATH="{got.entry.path.parent}:$PATH" を足す']
    if foreign_warning and got.copy.state == "foreign":
        warnings.append("旧い NDF の複製がある。旧いラッパーを止めてから ndf relay install --replace-old で置き換える")
    return result.emit(result.result(TOOL, result.OK, summary, items=_status_items(got), warnings=warnings))


def _install_run(args: argparse.Namespace) -> int:
    got = _do_install(args.replace_old, _missing_code())
    return got if isinstance(got, int) else _emit_installed(got, foreign_warning=True)


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
    elif args.action == "stop" and args.all:
        return _stop_all(ctx)
    else:
        req = {"kind": args.action, "name": args.name}
    return _outcome(hub.submit(ctx, req))


def _stop_all(ctx: hub.Context) -> int:
    """動いている名前つきのラッパーのすべてへ、名前つきの停止を依頼する。断られたものがあれば止まった結果。"""
    names = [r.name for r in registry.running(ctx.state_dir)]
    items: list[dict[str, Any]] = []
    refused: list[str] = []
    for name in names:
        got = hub.submit(ctx, {"kind": "stop", "name": name})
        items.append({"name": name, "ok": got.ok, "summary": got.summary})
        if not got.ok:
            refused.append(name)
    metrics = {"stopped": len(names) - len(refused)}
    if refused:
        return result.emit(result.stopped(TOOL, "refused", f"止められないラッパーがある: {'・'.join(refused)}", items=items, metrics=metrics))
    return result.emit(result.result(TOOL, result.OK, f"ラッパーを {len(names)} 本止めた", items=items, metrics=metrics))


def _notice() -> int:
    pos = notice.position(os.environ, os.getppid())
    got = notice.lines(pos, limits.seconds("relay.quiet"))
    return result.emit(result.result(TOOL, result.OK, got.text, items=[{"text": got.text, "position": pos.kind}], metrics={"inside": got.inside}))


def _context_resolve(args: argparse.Namespace) -> int:
    got = context_policy.resolve(Path(args.root).resolve() if args.root else context_records.toplevel(None), args.policy, os.environ)
    summary = f"再起動の方針: {got.policy}（判断開始の値 {got.decide_from} / 運用上限 {got.limit} / 余白 {got.margin}）"
    warnings = [got.config_error] if got.config_error else None
    if got.config_error:
        summary += f"。設定が壊れているため読まなかった: {got.config_error}"
    return result.emit(result.result(TOOL, result.OK, summary, items=[got.as_dict()], metrics={"policy": got.policy, "limit": got.limit}, warnings=warnings))


def _context_record(args: argparse.Namespace) -> int:
    root = context_records.toplevel(None)
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    work = context_records.recorded_work(args.work, sid if sid else None)
    applied = context_policy.resolve(root, None, os.environ)
    row = context_records.record_row(sid if sid else None, work, applied, args.event, args.reason)
    try:
        context_records.append(context_records.repo_key(root), row, os.environ)
    except OSError as exc:
        return result.emit(result.stopped(TOOL, "unknown", f"判定の記録へ書けない（{context_records.context_dir(os.environ)}）: {exc}"))
    label = args.event if args.event else args.verdict
    return result.emit(result.result(TOOL, result.OK, f"{label} を記録した（{work if work else 'セッションの前の行の作業'}）", items=[row]))


def _context_calibrate(args: argparse.Namespace) -> int:
    try:
        body = context_records.calibrate(context_records.toplevel(None), args.days, time.time(), os.environ)
    except OSError as exc:
        return result.emit(result.stopped(TOOL, "unknown", f"見込みのファイルへ書けない: {exc}"))
    samples = body["samples"]
    return result.emit(result.result(TOOL, result.OK, f"見込みを較正した（標本 {samples}）", items=[body], metrics={"samples": sum(samples.values())}))


def _span(args: argparse.Namespace) -> Optional[tuple[float, float]]:
    """`--from`・`--until` の区切り。どちらも無ければ None。読めなければ `ValueError`。"""
    if not (args.from_ or args.until):
        return None
    lo = context_records.parse_ts(args.from_) if args.from_ else float("-inf")
    hi = context_records.parse_ts(args.until) if args.until else float("inf")
    if lo is None or hi is None:
        raise ValueError(f"--from と --until は ISO 8601 の時刻: {args.from_} / {args.until}")
    return lo, hi


def _since(args: argparse.Namespace) -> Optional[float]:
    if not args.since:
        return None
    at = context_records.parse_ts(f"{args.since}T00:00:00Z")
    if at is None:
        raise ValueError(f"--since は YYYY-MM-DD: {args.since}")
    return at


def _context_report(args: argparse.Namespace) -> int:
    try:
        span, since = _span(args), _since(args)
    except ValueError as exc:
        return result.emit(result.stopped(TOOL, "invalid", str(exc)))
    repo = context_records.repo_key(context_records.toplevel(None))
    try:
        rows, skipped = context_records.read(repo, os.environ)
    except OSError as exc:
        return result.emit(result.stopped(TOOL, "unknown", f"判定の記録を読めない: {exc}"))
    rows = [r for r in rows if since is None or context_records.at_least(r, since)]
    ledger = context_report.load_ledger(os.environ)
    targets = [context_records.recorded_work(args.work, None)] if args.work else context_report.works(rows)
    reports = [context_report.report(w, rows, ledger, span) for w in targets]
    metrics = {"works": len(reports), "skipped": skipped, "no_policy_rows": sum(r["no_policy_rows"] for r in reports)}
    return result.emit(result.result(TOOL, result.OK, f"{len(reports)} 件の作業を集計した", items=reports, metrics=metrics))


CONTEXT_HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "resolve": _context_resolve,
    "record": _context_record,
    "calibrate": _context_calibrate,
    "report": _context_report,
}


def _context_parser(sub: Any) -> None:
    ctx = sub.add_parser("context", help=COMMANDS["context"], description="副命令: " + " / ".join(f"{k}（{v}）" for k, v in CONTEXT_COMMANDS.items()))
    ctx_sub = ctx.add_subparsers(dest="context_action", required=True)
    p = {name: ctx_sub.add_parser(name, help=text, description=text) for name, text in CONTEXT_COMMANDS.items()}
    p["resolve"].add_argument("--root", help="設定を読むリポジトリ（省けば今の作業場所の git の根）")
    p["resolve"].add_argument("--policy", choices=context_policy.POLICIES, help="方針を上書きする")
    kind = p["record"].add_mutually_exclusive_group(required=True)
    kind.add_argument("--verdict", choices=(context_policy.RESTART,), help="再起動の行を書く（--reason と組む）")
    kind.add_argument("--event", choices=context_records.MARKS, help="印の行を書く")
    p["record"].add_argument("--reason", choices=context_policy.REASONS, help="再起動の理由")
    p["record"].add_argument("--work", required=True, help="作業の識別子（課題番号の並び）")
    p["calibrate"].add_argument("--days", type=int, default=30, help="較正に使う直近の日数")
    target = p["report"].add_mutually_exclusive_group(required=True)
    target.add_argument("--work", help="集計する作業の識別子")
    target.add_argument("--all", action="store_true", help="記録にあるすべての作業を集計する")
    p["report"].add_argument("--since", help="この日（YYYY-MM-DD）以降の判定の行だけを読む")
    p["report"].add_argument("--from", dest="from_", help="この時刻（ISO 8601）からの呼び出しと行だけを数える")
    p["report"].add_argument("--until", help="この時刻（ISO 8601）までの呼び出しと行だけを数える")


def wrapper_settings(name: str, rest: list[str]) -> wrapper.Settings:
    ident = identity.load()
    environ = dict(os.environ)
    cwd = Path.cwd()
    return wrapper.Settings(
        state_dir=ident.state_dir,
        version=plugin_version(ident.root),
        name=name,
        cwd=cwd,
        environ=environ,
        args=tuple(rest),
        tick=limits.value("relay.tick", "interval"),
        quiet=limits.seconds("relay.quiet"),
        stall=limits.seconds("relay.stall"),
        max_starts=int(_env_number(environ, "NDF_RELAY_MAX_STARTS", "relay.max-starts", "attempts")),
        context_limit=int(limits.value("relay.context", "count")),
        usage_threshold=limits.value("relay.usage-guard", "count"),
        transcript_bytes=int(limits.value("relay.transcript-tail", "count")),
        ask_plan=lane_question.plan_asker(environ, name, cwd, ident.root, limits.seconds("relay.lanes-ask")),
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
    p = {name: sub.add_parser(name, help=text, description=text) for name, text in COMMANDS.items() if name not in ("account", "context")}
    _context_parser(sub)
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
    p["restart"].add_argument("name")
    stop = p["stop"].add_mutually_exclusive_group(required=True)
    stop.add_argument("name", nargs="?", help="止めるラッパーの名前")
    stop.add_argument("--all", action="store_true", help="動いている名前つきのラッパーをすべて止める")
    p["mark"].add_argument("session_id")
    return parser


def _parse(argv: list[str]) -> argparse.Namespace:
    args = _parser().parse_args(argv)
    if args.action == "context" and args.context_action == "record" and (args.verdict is not None) != (args.reason is not None):
        _parser().error("--verdict 再起動 には --reason を、--event には --reason を付けない")
    if getattr(args, "rest", None) and args.rest[0] == "--":
        args.rest = args.rest[1:]
    return args


HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "install": _install_run,
    "uninstall": lambda a: _emit_installed(install.uninstall(os.environ, _missing_code()), foreign_warning=False),
    "status": lambda a: _emit_installed(install.status(os.environ, _missing_code()), foreign_warning=False),
    "account": _account,
    "run": _run,
    "spawn": _hub,
    "restart": _hub,
    "stop": _hub,
    "ls": _hub,
    "pending": lambda a: _pending(),
    "mark": lambda a: _mark(a.session_id),
    "decide": lambda a: _decide(),
    "notice": lambda a: _notice(),
    "context": lambda a: CONTEXT_HANDLERS[a.context_action](a),
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
