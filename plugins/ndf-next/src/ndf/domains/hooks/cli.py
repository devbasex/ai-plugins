"""`ndf hooks` の副命令（#3345 の決定 90・94・95）。

    ndf hooks stats [--days 14]                         # 事象・guard ごとの p50・p95・拒否と差し戻しの数・注入の最大
    ndf hooks check-budget                              # 予算の順序の検査（破れがあれば stopped）
    ndf hooks capture <ランタイム> <版> <事象> < payload  # 本体の入力の見本を、秘密を伏せて足す

hook の経路ではない（利用者と CI が打つ）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from ndf import cli, identity
from ndf.contracts import causes, result
from ndf.domains.hooks import budget, metrics, tables

# 結果 JSON の tool の名前は副命令の名前（正本は `cli.COMMANDS`）
TOOL = cli.tool_of(__name__)
SAMPLES = tables.PLUGIN_ROOT / "tests" / "domains" / "hooks" / "samples"
# 見本で中身を残す鍵（ほかの文字列は伏せる）
_KEEP = frozenset({"hook_event_name", "tool_name", "source", "reason", "permission_mode", "model", "matcher"})
_ID_KEYS = frozenset({"session_id", "turn_id", "tool_use_id", "prompt_id"})
_PLACEHOLDER = {"cwd": "<cwd>", "transcript_path": "<transcript>"}
_VERSION_RE = re.compile(r"^\d+(\.\d+){1,3}$")


def redact(value: Any, key: str = "") -> Any:
    """見本に書く形。識別子は 0 に、作業ディレクトリと会話の記録のパスは置き換え、ほかの文字列は伏せる。"""
    if isinstance(value, dict):
        return {k: redact(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, key) for v in value]
    if not isinstance(value, str) or key in _KEEP:
        return value
    if key in _ID_KEYS:
        return re.sub(r"[0-9a-fA-F]", "0", value)
    return _PLACEHOLDER[key] if key in _PLACEHOLDER else "<text>"


def capture(runtime: str, version: str, event: str, payload: dict, root: Path = SAMPLES) -> Path:
    """見本を `<root>/<ランタイム>/<版>/<事象>.json` へ書く。"""
    path = root / runtime / version / f"{event}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(redact(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _stats(days: float) -> int:
    state_dir = identity.load().state_dir
    got = metrics.stats(metrics.read_records(state_dir, days))
    summary = f"{days:g} 日の hook の計測: 事象 {len(got['events'])} 種・guard {len(got['guards'])} 種"
    return result.emit(result.result(TOOL, result.OK, summary, items=[got], metrics={"days": days}))


def _check_budget() -> int:
    errors = budget.check_budget(tables.policies(), budget.registrations_of(tables.PLUGIN_ROOT))
    if errors:
        return result.emit(result.stopped(TOOL, "config_broken", f"hook の予算の破れが {len(errors)} 件ある", items=errors))
    return result.emit(result.result(TOOL, result.OK, "hook の予算の順序を守れている"))


def _capture(args: argparse.Namespace) -> int:
    if args.runtime not in tables.runtimes() or tables.event_spec(args.runtime, args.event) is None:
        return result.emit(result.stopped(TOOL, "invalid", f"契約表に無いランタイムか事象: {args.runtime} {args.event}"))
    if not _VERSION_RE.match(args.version):
        return result.emit(result.stopped(TOOL, "invalid", f"版の形が違う: {args.version}"))
    try:
        payload = json.loads(sys.stdin.read())
    except ValueError as exc:
        return result.emit(result.stopped(TOOL, "invalid", f"標準入力が JSON でない: {exc}"))
    if not isinstance(payload, dict):
        return result.emit(result.stopped(TOOL, "invalid", "標準入力が JSON のオブジェクトでない"))
    path = capture(args.runtime, args.version, args.event, payload)
    return result.emit(result.result(TOOL, result.OK, f"見本を書いた: {path}", items=[{"path": str(path)}]))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog=f"ndf {TOOL}", description="hook の計測・予算の検査・入力の見本")
    sub = parser.add_subparsers(dest="action", required=True)
    st = sub.add_parser("stats", help="事象・guard ごとの所要と判定の数")
    st.add_argument("--days", type=float, default=metrics.keep_days())
    sub.add_parser("check-budget", help="予算の順序の検査")
    cap = sub.add_parser("capture", help="本体の入力の見本を、秘密を伏せて足す（標準入力に payload）")
    cap.add_argument("runtime")
    cap.add_argument("version")
    cap.add_argument("event")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return causes.exit_code("invalid") if exc.code else 0
    if args.action == "stats":
        return _stats(args.days)
    if args.action == "check-budget":
        return _check_budget()
    return _capture(args)
