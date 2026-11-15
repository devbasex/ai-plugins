"""`ndf relay` の副命令。

    ndf relay install [--replace-old] | uninstall | status   # 複製 ~/.claude/ndf/relay.py と PATH のエントリポイント ndf-relay
    ndf relay account use <名前> | show | clear               # 子の claude を起動するアカウントの指定（#2464）
    ndf relay account add | add-bedrock | add-api-key | api-key-monthly | api-key-use | list | capacity | remove | check
                                                              # アカウントと従量の接続の登録（旧い置き場と同じ。#3569）
    ndf relay run --name <名前> [--prompt <1 行>] [-- <claude の引数>]   # ラッパーを今の端末で動かす
    ndf relay spawn [--name <名前>] [--cwd <dir>] [--role <役割>] [--plan <計画>] [-- <claude の引数>]
    ndf relay restart <名前> | stop <名前> | stop --all | ls   # リレーハブへの依頼と一覧
    ndf relay notice                                          # カットポイントのアナウンス（ラッパーの直接の子か）
    ndf relay context resolve | record | calibrate | report   # 再起動の方針の適用値・判定の記録・見込み・作業ごとの集計
    ndf relay pending                                         # 再起動の前に載せる、待ちを抱えた実行の一覧（決定 86）
    ndf relay mark <session_id>                               # 子へ入力を送る前に中継の印を立てる（決定 92）
    ndf relay decide < 区間の状態の JSON                       # 区間を切り替えるかの判断（#2962）

副命令の説明は `COMMANDS`・`ACCOUNT_COMMANDS`・`ACCOUNT_REGISTRY` の表から作り、`--help` の一覧と実装が食い違わない（#2898）。
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import termios
import time
import tty
from pathlib import Path
from typing import Any, Callable, Mapping, NamedTuple, Optional

from ndf import cli, identity
from ndf.contracts import causes, limits, result
from ndf.domains.relay import (
    account_decide,
    accounts,
    hub,
    install,
    lane_question,
    marks,
    notice,
    pending,
    registry,
    switch_decide,
    wrapper,
)
from ndf.domains.relay import account_login, account_metered, account_registry, context_policy, context_records, context_report
from ndf.domains.relay.tmux import Tmux

# 結果 JSON の tool の名前は副命令の名前（正本は `cli.COMMANDS`）
TOOL = cli.tool_of(__name__)
COMMANDS = {
    "install": "中継の複製と PATH のエントリポイントを置く",
    "uninstall": "置いた複製と PATH のエントリポイントを消す",
    "status": "複製と PATH のエントリポイントの有無",
    "account": "子の claude を起動するアカウントの指定と、アカウント・従量の接続の登録",
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
    if args.account_action in ACCOUNT_REGISTRY_HANDLERS:
        return _account_registry(args)
    state_dir = identity.load().state_dir
    if args.account_action == "use":
        accounts.use(state_dir, args.name)
        return result.emit(result.result(TOOL, result.OK, f"アカウント {args.name} を指定した"))
    if args.account_action == "clear":
        done = accounts.clear(state_dir)
        return result.emit(result.result(TOOL, result.OK, "指定を消した" if done else "指定は無かった"))
    name = accounts.chosen(os.environ, state_dir)
    return result.emit(result.result(TOOL, result.OK, f"選ばれるアカウント: {name or '（claude の既定）'}", items=[{"account": name}]))


def _capacity_arg(v: str) -> Optional[float]:
    """枠の引数（正の数か `-`）。`-` は None。"""
    if v == "-":
        return None
    try:
        x = float(v)
    except ValueError:
        x = 0.0
    if not 0 < x < float("inf"):
        raise account_registry.AccountError(f"枠は正の数（USD）か - で渡す: {v}")
    return x


def _acc_add(store: Path, a: argparse.Namespace) -> int:
    code = (sys.stdin.readline() if a.code == "-" else a.code) if a.code is not None else None
    got = account_login.add(store, a.name, code, os.environ)
    if got.needs_code:
        detail = f"認可の URL: {got.url} をブラウザで開き、出た認可コードを {got.expires_at} までに渡す: printf '%s\\n' '<コード>' | ndf relay account add {a.name} --code -"
        return result.emit(
            result.stopped(
                TOOL, "input_missing", detail, items=[{"name": a.name, "kind": "oauth", "url": got.url, "expires_at": got.expires_at}]
            )
        )
    who = f"{got.email}・{got.org_name}" if got.org_name else got.email
    return result.emit(
        result.result(
            TOOL,
            result.OK,
            f"登録した: {a.name}（{who}）",
            items=[{"name": a.name, "kind": "oauth", "email": got.email, "org_name": got.org_name}],
        )
    )


def _acc_bedrock(store: Path, a: argparse.Namespace) -> int:
    asked = account_metered.Target(a.profile or "", a.region or "", a.model or "")  # noqa: NDF501 利用者の入力: 省いた欄は空で渡し、候補を添えて止める
    row = account_metered.add_bedrock(store, os.environ, asked, a.yes)
    return result.emit(
        result.result(TOOL, result.OK, f"登録した: 従量の接続（Bedrock・{'・'.join(row['details'].values())}）", items=[row])
    )


def _acc_api_key(store: Path, a: argparse.Namespace) -> int:
    usd = account_metered.amount(a.monthly)
    key = account_metered.read_key(a.key_file, "" if a.key_file else sys.stdin.readline())
    row = account_metered.add_api_key(store, os.environ, account_metered.KeyRequest(a.name, a.account, key, usd))
    note = "組織に買ったクレジットか自動チャージがあると、月の API クレジットを使い切った後もそちらから払われる（Console の spend limit で抑える）"
    return result.emit(result.result(TOOL, result.OK, f"登録した: {a.name}（アカウント {a.account}）", items=[row], warnings=[note]))


def _acc_monthly(store: Path, a: argparse.Namespace) -> int:
    row = account_metered.set_monthly(store, a.name, account_metered.amount(a.usd, allow_zero=True))
    usd = row["monthly_usd"]
    return result.emit(
        result.result(TOOL, result.OK, f"月の額を外した: {a.name}" if usd is None else f"月の額: {a.name} {usd:g} ドル", items=[row])
    )


def _acc_use(store: Path, a: argparse.Namespace) -> int:
    row = account_metered.set_use(store, a.mode, account_metered.weekly_at(a.weekly_at))
    return result.emit(result.result(TOOL, result.OK, f"API キーの使い方: {row['use']}（閾値 {row['weekly_at']:g}%）", items=[row]))


def _acc_list(store: Path, a: argparse.Namespace) -> int:
    state_dir = identity.load().state_dir
    rows, warnings = account_registry.list_rows(store, os.environ, accounts.pinned(state_dir), accounts.usages(state_dir))
    metrics = {"accounts": sum(r["kind"] == "oauth" for r in rows), "keys": sum(r["kind"] == "api_key" for r in rows)}
    summary = f"登録: アカウント {metrics['accounts']} 件・API キー {metrics['keys']} 件" if rows else "登録済みのアカウントは無い"
    return result.emit(result.result(TOOL, result.OK, summary, items=rows, metrics=metrics, warnings=warnings))


def _acc_capacity(store: Path, a: argparse.Namespace) -> int:
    row = account_registry.set_capacity(store, a.name, _capacity_arg(a.five), _capacity_arg(a.seven))
    return result.emit(result.result(TOOL, result.OK, f"枠の大きさを宣言した: {a.name}", items=[row]))


def _acc_remove(store: Path, a: argparse.Namespace) -> int:
    if a.target != account_registry.METERED and a.target in account_registry.names(store):
        account_registry.detach(store, a.target)  # logout が設定ディレクトリの中を消す前に、共有の項目への symlink を外す
        out = account_login.logout(store, a.target, os.environ)
        account_registry.unregister(store, a.target)
        warnings = None if out else ["claude auth logout が通らなかった（登録は外した）"]
        return result.emit(
            result.result(TOOL, result.OK, f"外した: {a.target}", items=[{"name": a.target, "kind": "oauth"}], warnings=warnings)
        )
    row = account_metered.remove(store, a.target)
    return result.emit(result.result(TOOL, result.OK, f"外した: {a.target}", items=[row]))


def _acc_check(store: Path, a: argparse.Namespace) -> int:
    row = account_metered.check(store, os.environ, a.target)
    return result.emit(result.result(TOOL, result.OK, f"呼べる: {a.target}", items=[row]))


class AccountSub(NamedTuple):
    """アカウントの登録の副命令の 1 つ（名前・説明・ハンドラ）。"""

    name: str
    text: str
    handler: Callable[[Path, argparse.Namespace], int]


# アカウントと従量の接続の登録の副命令（旧い置き場と同じ。#3569）
ACCOUNT_REGISTRY = (
    AccountSub("add", "OAuth のアカウントを登録する（--code なしで認可の URL を出し、--code で認可コードを渡す）", _acc_add),
    AccountSub("add-bedrock", "Bedrock の従量の接続を確かめて保存する", _acc_bedrock),
    AccountSub("add-api-key", "API キーを確かめて保存する（値は標準入力か --key-file）", _acc_api_key),
    AccountSub("api-key-monthly", "API キーの月の額を宣言する（0 で外す）", _acc_monthly),
    AccountSub("api-key-use", "API キーへ切り替える使い方と閾値を読む・書く", _acc_use),
    AccountSub("list", "登録したアカウント・従量の接続・API キーの一覧", _acc_list),
    AccountSub("capacity", "アカウントの 5 時間と週の枠の大きさを宣言する（- で外す）", _acc_capacity),
    AccountSub("remove", "アカウント・従量の接続（metered）・API キーの登録を外す", _acc_remove),
    AccountSub("check", "従量の接続（metered）か API キーが呼べるかを確かめる（状態は記録しない）", _acc_check),
)
ACCOUNT_REGISTRY_HANDLERS = {sub.name: sub.handler for sub in ACCOUNT_REGISTRY}


def _account_registry(args: argparse.Namespace) -> int:
    """登録の副命令を動かす。使い方の誤りは invalid、待ちきれない排他と書けない置き場は failed で止まる。"""
    try:
        return ACCOUNT_REGISTRY_HANDLERS[args.account_action](account_registry.store_dir(os.environ, identity.load().name), args)
    except account_metered.MeteredError as exc:
        return result.emit(result.stopped(TOOL, exc.kind, str(exc), items=exc.items))
    except account_login.LoginError as exc:
        return result.emit(result.stopped(TOOL, exc.kind, str(exc)))
    except account_registry.NotRegistered as exc:
        return result.emit(result.stopped(TOOL, "refused", str(exc)))
    except account_registry.AccountLockTimeout as exc:
        return result.emit(result.stopped(TOOL, "store_busy", str(exc)))
    except OSError as exc:
        return result.emit(result.stopped(TOOL, "unknown", f"アカウントの置き場へ書けない: {exc}"))


def _account_parser(sub: Any) -> None:
    texts = {**ACCOUNT_COMMANDS, **{s.name: s.text for s in ACCOUNT_REGISTRY}}
    acc = sub.add_parser("account", help=COMMANDS["account"], description="副命令: " + " / ".join(f"{k}（{v}）" for k, v in texts.items()))
    acc_sub = acc.add_subparsers(dest="account_action", required=True)
    p = {name: acc_sub.add_parser(name, help=text, description=text) for name, text in texts.items()}
    p["use"].add_argument("name")
    p["add"].add_argument("name", help="アカウントの名前（英小文字・数字・- と _ の 32 字まで）")
    p["add"].add_argument("--code", help="認可コード。- なら標準入力の 1 行（引数と記録に残さない）")
    for opt, text in (("--profile", "AWS のプロファイル"), ("--region", "地域（省けばプロファイルの region）"), ("--model", "モデルの ID")):
        p["add-bedrock"].add_argument(opt, help=text)
    p["add-bedrock"].add_argument("--yes", action="store_true", help="確かめが通れば保存する（前の宣言を置き換える）")
    p["add-api-key"].add_argument("name", help="API キーの名前")
    p["add-api-key"].add_argument("--account", required=True, help="結び付ける登録済みアカウント")
    p["add-api-key"].add_argument("--key-file", help="キーの値を 1 行目に持つファイル（省けば標準入力の 1 行）")
    p["add-api-key"].add_argument("--monthly", help="月の額（ドル）")
    p["api-key-monthly"].add_argument("name", help="API キーの名前")
    p["api-key-monthly"].add_argument("usd", help="月の額（ドル。0 で外す）")
    p["api-key-use"].add_argument("mode", nargs="?", choices=account_registry.USES, help="使い方（省けば今の値を出す）")
    p["api-key-use"].add_argument("--weekly-at", help="閾値（百分率。1〜100）")
    p["capacity"].add_argument("name", help="アカウントの名前")
    p["capacity"].add_argument("five", help="5 時間の枠（USD か -）")
    p["capacity"].add_argument("seven", help="週の枠（USD か -）")
    p["remove"].add_argument("target", help="アカウントの名前・metered・API キーの名前")
    p["check"].add_argument("target", help="metered か API キーの名前")


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
        return result.emit(
            result.stopped(TOOL, "refused", f"止められないラッパーがある: {'・'.join(refused)}", items=items, metrics=metrics)
        )
    return result.emit(result.result(TOOL, result.OK, f"ラッパーを {len(names)} 本止めた", items=items, metrics=metrics))


def _notice() -> int:
    pos = notice.position(os.environ, os.getppid())
    got = notice.lines(pos, limits.seconds("relay.quiet"))
    return result.emit(
        result.result(TOOL, result.OK, got.text, items=[{"text": got.text, "position": pos.kind}], metrics={"inside": got.inside})
    )


def _context_resolve(args: argparse.Namespace) -> int:
    got = context_policy.resolve(Path(args.root).resolve() if args.root else context_records.toplevel(None), args.policy, os.environ)
    summary = f"再起動の方針: {got.policy}（判断開始の値 {got.decide_from} / 運用上限 {got.limit} / 余白 {got.margin}）"
    warnings = [got.config_error] if got.config_error else None
    if got.config_error:
        summary += f"。設定が壊れているため読まなかった: {got.config_error}"
    return result.emit(
        result.result(
            TOOL, result.OK, summary, items=[got.as_dict()], metrics={"policy": got.policy, "limit": got.limit}, warnings=warnings
        )
    )


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
    return result.emit(
        result.result(TOOL, result.OK, f"見込みを較正した（標本 {samples}）", items=[body], metrics={"samples": sum(samples.values())})
    )


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
    ctx = sub.add_parser(
        "context", help=COMMANDS["context"], description="副命令: " + " / ".join(f"{k}（{v}）" for k, v in CONTEXT_COMMANDS.items())
    )
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
    _account_parser(sub)
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
    except (accounts.AccountError, account_registry.AccountError, marks.MarkError, registry.RegistryError, EnvError) as exc:
        return result.emit(result.stopped(TOOL, "invalid", str(exc)))
