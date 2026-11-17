"""`ndf relay context` のサブコマンド（再起動の方針の適用値・判定の記録・費用の見込み・作業ごとの集計。#3569）。

`relay/cli.py` から分けた。結果 JSON の tool の名前は `ndf relay` と同じ。
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path
from typing import Any, Callable, Optional

from ndf import cli
from ndf.contracts import result
from ndf.domains.relay import context_policy, context_records, context_report

# 結果 JSON の tool の名前はサブコマンドの名前（SSOT は `cli.COMMANDS`）
TOOL = cli.tool_of(f"{__package__}.cli")
CONTEXT_COMMANDS = {
    "resolve": "再起動の方針の適用値と値ごとの出所を出す",
    "record": "再起動かマーカー（引継ぎ開始・復元完了）の 1 行を判定の記録へ足す",
    "calibrate": "判定の記録と会話の記録から費用の見込みを較正して書く",
    "report": "作業ごとの区分の token・秒・費用・再起動・自動圧縮・品質・復元の量を集計する",
}


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
    work = context_records.recorded_work(args.work, sid if sid else None)  # noqa: NDF501 利用者の入力: 空の CLAUDE_CODE_SESSION_ID は未設定と同じ
    applied = context_policy.resolve(root, None, os.environ)
    row = context_records.record_row(sid if sid else None, work, applied, args.event, args.reason)  # noqa: NDF501 利用者の入力: 空の CLAUDE_CODE_SESSION_ID は未設定と同じ
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
    usage_log = context_report.load_usage_log(os.environ)
    targets = [context_records.recorded_work(args.work, None)] if args.work else context_report.works(rows)
    reports = [context_report.report(w, rows, usage_log, span) for w in targets]
    metrics = {"works": len(reports), "skipped": skipped, "no_policy_rows": sum(r["no_policy_rows"] for r in reports)}
    return result.emit(result.result(TOOL, result.OK, f"{len(reports)} 件の作業を集計した", items=reports, metrics=metrics))


CONTEXT_HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "resolve": _context_resolve,
    "record": _context_record,
    "calibrate": _context_calibrate,
    "report": _context_report,
}


def parser(sub: Any, help_text: str) -> None:
    """`context` のサブコマンドの parser を `sub` へ足す（`help_text` は `ndf relay` の一覧の説明）。"""
    ctx = sub.add_parser(
        "context", help=help_text, description="サブコマンド: " + " / ".join(f"{k}（{v}）" for k, v in CONTEXT_COMMANDS.items())
    )
    ctx_sub = ctx.add_subparsers(dest="context_action", required=True)
    p = {name: ctx_sub.add_parser(name, help=text, description=text) for name, text in CONTEXT_COMMANDS.items()}
    p["resolve"].add_argument("--root", help="設定を読むリポジトリ（省けば今の作業場所の git の根）")
    p["resolve"].add_argument("--policy", choices=context_policy.POLICIES, help="方針を上書きする")
    kind = p["record"].add_mutually_exclusive_group(required=True)
    kind.add_argument("--verdict", choices=(context_policy.RESTART,), help="再起動の行を書く（--reason と組む）")
    kind.add_argument("--event", choices=context_records.MARKS, help="マーカーの行を書く")
    p["record"].add_argument("--reason", choices=context_policy.REASONS, help="再起動の理由")
    p["record"].add_argument("--work", required=True, help="作業の識別子（課題番号の並び）")
    p["calibrate"].add_argument("--days", type=int, default=30, help="較正に使う直近の日数")
    target = p["report"].add_mutually_exclusive_group(required=True)
    target.add_argument("--work", help="集計する作業の識別子")
    target.add_argument("--all", action="store_true", help="記録にあるすべての作業を集計する")
    p["report"].add_argument("--since", help="この日（YYYY-MM-DD）以降の判定の行だけを読む")
    p["report"].add_argument("--from", dest="from_", help="この時刻（ISO 8601）からの呼び出しと行だけを数える")
    p["report"].add_argument("--until", help="この時刻（ISO 8601）までの呼び出しと行だけを数える")
